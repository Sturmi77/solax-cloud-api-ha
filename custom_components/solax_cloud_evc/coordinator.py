"""DataUpdateCoordinator with automatic OAuth2 token lifecycle management.

Key design decisions:
- config_entry passed explicitly to super().__init__() — required from HA 2026.8+
- Token persisted in ConfigEntry.data — survives HA restart without new API call
- _ensure_token() called before every update — only fetches when needed (1h buffer)
- Single config entry enforced via manifest.json — prevents parallel token invalidation
- Never logs full token — only first 4 chars for debugging
- 401 response → clears token + ConfigEntry, raises UpdateFailed; self-heals on next cycle
- code=10402 ("Request access_token authentication failed") handled identically to 401 —
  clears both in-memory token and ConfigEntry to prevent _load_token_from_entry() from
  resurrecting a dead token. Self-heals without user action.
- No update_listener registered — add_update_listener() fires on ANY async_update_entry()
  call, including the coordinator's internal token saves. A listener that reloads the
  integration would cause a reload loop on every token persistence.
- code=10200 ("Operation abnormality") is now handled separately — logs real `message` field for diagnosis.
  code=10406 is the only true rate-limit code per official docs.
  Commands raise HomeAssistantError; data polls raise UpdateFailed. See const.py.
- Client-side command guard: COMMAND_MIN_INTERVAL enforced before each API command call.
- Data polls are retried on transient errors (network, timeout, 10001/10200/11500,
  token refresh) and a failed poll is bridged with the last good data for
  STALE_TOLERANCE_LONG seconds, so a single cloud hiccup no longer makes every entity
  unavailable. Each entity decides via data_age() how long it shows bridged data
  (live values such as power only for STALE_TOLERANCE_SHORT). Poll health (consecutive failures, last error) is exposed for
  diagnostics and a dedicated sensor.
- All HTTP calls share one aiohttp session with an explicit REQUEST_TIMEOUT. The session
  belongs to the integration because its resolver falls back to the last known IPs
  when DNS for the API host fails (dns_fallback.py).
- totalChargeEnergy is guarded against 0/None/decreasing values so a glitch never
  looks like a meter reset to the TOTAL_INCREASING statistics.

See ARCHITECTURE.md §6 and SECURITY.md §2 for details.
"""

from __future__ import annotations

import logging
import asyncio
import time
from copy import deepcopy
from datetime import datetime, timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    API_AUTH_SUCCESS_CODE,
    API_RATE_LIMIT_CODE,
    API_RATE_LIMIT_CODE_OFFICIAL,
    API_SUCCESS_CODE,
    API_TOKEN_EXPIRED_CODE,
    COMMAND_MIN_INTERVAL,
    COMMAND_POLL_DELAY,
    COMMAND_POLL_URL,
    COMMAND_STATUS_MAP,
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_EVC_SN,
    CONF_TOKEN_EXPIRES,
    DATA_RETRY_DELAYS,
    DATA_URL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TOKEN_LIFETIME,
    BUSINESS_TYPE_RESIDENTIAL,
    DEVICE_TYPE_EVC,
    DOMAIN,
    REQUEST_TIMEOUT,
    STALE_TOLERANCE_LONG,
    TOKEN_REFRESH_BUFFER,
    TOKEN_URL,
    TRANSIENT_API_CODES,
)
from .dns_fallback import async_create_fallback_session

_LOGGER = logging.getLogger(__name__)


class _TransientPollError(UpdateFailed):
    """A data poll failure worth retrying within the same update cycle."""


def _is_rate_limited_response(resp: dict) -> bool:
    """Return True ONLY when Solax response indicates API throttling (code=10406).

    NOTE: code=10200 ("Operation abnormality") is NOT treated as rate limit here.
    It is a generic error — the real cause is in the `message` field and must be
    logged separately. Only code=10406 ("API call rate limit reached") is a true
    rate limit per Solax Developer Portal Appendix 1.
    """
    if not isinstance(resp, dict):
        return False
    code = resp.get("code")
    if code == API_RATE_LIMIT_CODE_OFFICIAL:  # 10406 = true rate limit
        return True
    # String-based fallback for undocumented rate-limit responses
    exception_msg = str(resp.get("exception", "")).lower()
    rate_limit_markers = (
        "rate limit",
        "maximum call threshold",
        "suspend the request",
        "current minute > threshold",
        "within the current minute",
        "too many requests",
    )
    return any(marker in exception_msg for marker in rate_limit_markers)


class SolaxCoordinator(DataUpdateCoordinator[dict]):
    """Coordinator for SolaxCloud API — manages token lifecycle and data fetching."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator.

        config_entry is passed explicitly to avoid the ContextVar deprecation
        warning introduced in HA 2026.3 (breaking in 2026.8).
        """
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,                          # explicit — required from 2026.8
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self._entry = entry
        self._client_id: str = entry.data[CONF_CLIENT_ID]
        self._client_secret: str = entry.data[CONF_CLIENT_SECRET]
        self._token: str | None = None
        self._token_expires: float = 0.0
        self._last_command_time: float = 0.0
        self._postpone_poll_until: float = 0.0   # skip polls until this monotonic timestamp
        # Own session with DNS fallback (see dns_fallback.py)
        self._session = async_create_fallback_session(hass, entry)
        self._timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        # Raw (unfiltered) API response — stored for diagnostics export
        self.raw_api_response: dict | None = None
        # Poll health — exposed via diagnostics and the "EVC Poll Failures" sensor
        self.consecutive_failures: int = 0
        self.total_failures: int = 0
        self.last_error: str | None = None
        self.last_error_time: datetime | None = None
        self.last_success_time: datetime | None = None

    # ── Token Management ─────────────────────────────────────────────────────

    def _load_token_from_entry(self) -> None:
        """Load token and expiry from ConfigEntry.

        Called once on first update. Subsequent restarts skip the token endpoint
        entirely as long as the stored token is still valid.
        """
        self._token = self._entry.data.get(CONF_ACCESS_TOKEN)
        self._token_expires = self._entry.data.get(CONF_TOKEN_EXPIRES, 0.0)
        if self._token:
            remaining_h = max(0, self._token_expires - time.time()) / 3600
            _LOGGER.debug(
                "SolaxCloud: Token loaded from ConfigEntry (%s..., %.0fh remaining)",
                self._token[:4],
                remaining_h,
            )

    async def _fetch_new_token(self) -> None:
        """Fetch a new token from the SolaxCloud API and persist immediately.

        ⚠️ WARNING: Each call immediately invalidates the previous active token.
        SolaxCloud allows only one active token per Application — fetching a new
        token IMMEDIATELY invalidates the previous one for all clients sharing
        the same client_id/client_secret.

        Only call this when the token is genuinely missing or within TOKEN_REFRESH_BUFFER
        of expiry. Never call from background tasks or polling loops directly.

        Response structure (auth endpoint, code=0 on success):
          {
            "code": 0,
            "result": {
              "access_token": "...",
              "token_type": "bearer",
              "expires_in": 2591999,
              ...
            }
          }

        NOTE: access_token is flat inside result (i.e. result.access_token),
        not nested in result.result. The result key IS the token data object.
        """
        _LOGGER.info("SolaxCloud: Fetching new access token")
        payload = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "grant_type": "client_credentials",
        }
        try:
            async with self._session.post(
                TOKEN_URL, data=payload, timeout=self._timeout
            ) as resp:
                resp.raise_for_status()
                result = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise _TransientPollError(f"SolaxCloud: Token fetch failed: {err!r}") from err

        # Auth endpoint: code=0 on success, code=10400 on bad credentials.
        # access_token is flat inside result (result.access_token), not nested.
        api_code = result.get("code")
        token_data = result.get("result")
        if api_code != API_AUTH_SUCCESS_CODE or not token_data:
            msg = result.get("message", "Unknown error")
            _LOGGER.error(
                "SolaxCloud: Token API error — %s (code=%s) — triggering re-auth",
                msg,
                api_code,
            )
            self._entry.async_start_reauth(self.hass)
            raise UpdateFailed(f"Invalid credentials (code={api_code}) — re-authentication required")

        access_token = token_data.get("access_token")
        if not access_token:
            raise UpdateFailed(
                f"SolaxCloud: Token response missing access_token: {token_data}"
            )

        self._token = access_token
        expires_in = token_data.get("expires_in", DEFAULT_TOKEN_LIFETIME)
        self._token_expires = time.time() + expires_in

        # Persist to ConfigEntry — survives HA restart without a new API call
        self.hass.config_entries.async_update_entry(
            self._entry,
            data={
                **self._entry.data,
                CONF_ACCESS_TOKEN: self._token,
                CONF_TOKEN_EXPIRES: self._token_expires,
            },
        )
        _LOGGER.info(
            "SolaxCloud: New token persisted (%s..., valid %.1f days)",
            self._token[:4],
            expires_in / 86400,
        )

    async def _ensure_token(self) -> None:
        """Guarantee a valid token before each API call.

        Logic:
          1. If token not in memory → load from ConfigEntry (first call after restart)
          2. If still None or within TOKEN_REFRESH_BUFFER of expiry → fetch new token
          3. Otherwise → reuse existing token (no API call)
        """
        if self._token is None:
            self._load_token_from_entry()

        needs_refresh = self._token is None or time.time() >= (
            self._token_expires - TOKEN_REFRESH_BUFFER
        )
        if needs_refresh:
            await self._fetch_new_token()

    # ── Data Fetching ────────────────────────────────────────────────────────

    async def _async_update_data(self) -> dict:
        """Fetch EVC realtime data — called every DEFAULT_SCAN_INTERVAL seconds.

        Transient failures are retried (DATA_RETRY_DELAYS). If the poll still
        fails, the last good data is served for up to STALE_TOLERANCE_LONG seconds
        after the last success; each entity limits this further via data_age().
        After that UpdateFailed marks all entities unavailable.
        """
        # Skip poll if a command was sent very recently — avoids hitting the API
        # rate limit by polling immediately after a command in the same burst window.
        if time.monotonic() < self._postpone_poll_until:
            remaining = self._postpone_poll_until - time.monotonic()
            _LOGGER.debug(
                "SolaxCloud: Skipping data poll — command cooldown, %.1fs remaining",
                remaining,
            )
            if self.data is not None:
                return self.data
            raise UpdateFailed("SolaxCloud: Poll skipped — command cooldown active")

        try:
            result = await self._fetch_realtime_with_retry()
        except UpdateFailed as err:
            self._record_failure(err)
            if self._can_bridge_failure():
                _LOGGER.warning(
                    "SolaxCloud: Poll failed (%d in a row), keeping last data from %s: %s",
                    self.consecutive_failures,
                    self.last_success_time.isoformat() if self.last_success_time else "n/a",
                    err,
                )
                return self.data
            raise

        if self.consecutive_failures:
            _LOGGER.info(
                "SolaxCloud: Poll recovered after %d failed attempt(s)",
                self.consecutive_failures,
            )
        self.consecutive_failures = 0
        self.last_success_time = dt_util.utcnow()
        return self._sanitize(result)

    def _record_failure(self, err: Exception) -> None:
        """Track poll health for diagnostics and the poll-failure sensor."""
        self.consecutive_failures += 1
        self.total_failures += 1
        self.last_error = str(err)
        self.last_error_time = dt_util.utcnow()

    def data_age(self) -> float | None:
        """Seconds since the last successful poll, or None before the first one."""
        if self.last_success_time is None:
            return None
        return (dt_util.utcnow() - self.last_success_time).total_seconds()

    def _can_bridge_failure(self) -> bool:
        """Return True while the last good data is recent enough to keep serving."""
        if self.data is None:
            return False
        age = self.data_age()
        return age is not None and age < STALE_TOLERANCE_LONG

    def _sanitize(self, result: dict) -> dict:
        """Guard the lifetime energy counter against glitches.

        totalChargeEnergy is TOTAL_INCREASING: a 0, a missing value or a drop would
        be read as a meter reset and corrupt the long-term statistics. Keep the
        previous value in that case.
        """
        previous = (self.data or {}).get("totalChargeEnergy")
        current = result.get("totalChargeEnergy")
        if previous is not None and (
            not isinstance(current, (int, float)) or current <= 0 or current < previous
        ):
            _LOGGER.warning(
                "SolaxCloud: Implausible totalChargeEnergy %s (previous %s) — keeping previous",
                current,
                previous,
            )
            result = {**result, "totalChargeEnergy": previous}
        return result

    async def _fetch_realtime_with_retry(self) -> dict:
        """Fetch realtime data, retrying transient failures within this cycle."""
        delays = (*DATA_RETRY_DELAYS, None)
        for attempt, delay in enumerate(delays, start=1):
            try:
                return await self._fetch_realtime()
            except _TransientPollError as err:
                if delay is None:
                    raise
                _LOGGER.debug(
                    "SolaxCloud: Transient poll error (attempt %d/%d), retrying in %ds: %s",
                    attempt,
                    len(delays),
                    delay,
                    err,
                )
                await asyncio.sleep(delay)
        raise UpdateFailed("SolaxCloud: Data poll failed")  # not reached

    async def _fetch_realtime(self) -> dict:
        """Single realtime data request.

        Raises _TransientPollError for errors worth retrying, UpdateFailed otherwise.
        """
        await self._ensure_token()

        evc_sn = self._entry.data.get(CONF_EVC_SN)
        params = {
            "snList": evc_sn,
            "deviceType": str(DEVICE_TYPE_EVC),
            "businessType": BUSINESS_TYPE_RESIDENTIAL,
        }
        headers = {"Authorization": f"bearer {self._token}"}

        try:
            async with self._session.get(
                DATA_URL, params=params, headers=headers, timeout=self._timeout
            ) as resp:
                if resp.status == 401:
                    # Token was invalidated externally (e.g. another app fetched a new token)
                    _LOGGER.warning(
                        "SolaxCloud: Data request returned 401 — token invalidated, "
                        "fetching a new token"
                    )
                    self._token = None          # force re-fetch on the next attempt
                    self._token_expires = 0.0
                    raise _TransientPollError("Token invalidated (HTTP 401)")
                if resp.status >= 500:
                    raise _TransientPollError(f"SolaxCloud: HTTP {resp.status}")
                resp.raise_for_status()
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise _TransientPollError(f"SolaxCloud: Data fetch failed: {err!r}") from err

        code = data.get("code")
        if code == API_TOKEN_EXPIRED_CODE:
            _LOGGER.warning(
                "SolaxCloud: Token expired/invalid (code=10402) — clearing token and ConfigEntry, "
                "fetching a fresh token"
            )
            self._token = None
            self._token_expires = 0.0
            # Also clear from ConfigEntry so _load_token_from_entry() does not resurrect
            # the dead token on the next attempt. Without this, _ensure_token would reload
            # the stale token from ConfigEntry, see it as "not expired", and skip _fetch_new_token.
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={
                    **self._entry.data,
                    CONF_ACCESS_TOKEN: None,
                    CONF_TOKEN_EXPIRES: 0.0,
                },
            )
            raise _TransientPollError("SolaxCloud: Token expired (code=10402)")
        if _is_rate_limited_response(data):
            exception_msg = data.get("exception", "")
            _LOGGER.warning(
                "SolaxCloud: Rate limit exceeded during data poll (code=%s, exception=%s)",
                code,
                exception_msg or "n/a",
            )
            # Not retried — retrying would only extend the throttling window
            raise UpdateFailed(
                f"SolaxCloud: Rate limit exceeded (code={code}) — will retry on next interval"
            )
        if code in TRANSIENT_API_CODES:
            # e.g. code=10200 "Operation abnormality", 11500 "System busy":
            # log the real message for diagnosis, then retry
            actual_msg = data.get("message") or data.get("msg") or "<no message>"
            _LOGGER.warning(
                "SolaxCloud: Data poll error (code=%s) — message='%s' exception='%s'",
                code,
                actual_msg,
                data.get("exception", "<no exception>"),
            )
            raise _TransientPollError(
                f"SolaxCloud: Data poll error (code={code}) — {actual_msg}"
            )
        if code != API_SUCCESS_CODE:
            msg = data.get("message") or data.get("msg") or "unknown error"
            _LOGGER.error("SolaxCloud API error: %s (code=%s)", msg, code)
            raise UpdateFailed(f"SolaxCloud API error: {msg} (code={code})")

        result = data.get("result")
        if not result:
            raise _TransientPollError("SolaxCloud: API returned empty result list")

        _LOGGER.debug(
            "SolaxCloud: EVC data received — status=%s power=%sW",
            result[0].get("deviceStatus"),
            result[0].get("chargingPower"),
        )
        # Cache raw response for diagnostics
        self.raw_api_response = deepcopy(data) if isinstance(data, dict) else None
        return result[0]

    async def async_send_evc_command(self, url: str, payload: dict) -> dict:
        """Send a control command to the SolaxCloud EVC API.

        Ensures a valid token is present before sending.
        Schedules async_poll_command_result() to run after COMMAND_POLL_DELAY
        seconds to check delivery and surface failures as persistent notifications.

        Returns the full API response dict.

        Raises:
            HomeAssistantError  — on API-level error (code != 10000)
            aiohttp.ClientError — on network error
        """
        # Client-side rate-limit guard — reject early if last command was too recent
        now = time.monotonic()
        elapsed = now - self._last_command_time
        if elapsed < COMMAND_MIN_INTERVAL:
            raise HomeAssistantError(
                f"SolaxCloud: Rate limit — please wait {COMMAND_MIN_INTERVAL - elapsed:.0f}s before sending another command"
            )
        self._last_command_time = now

        # Postpone the next scheduled data poll so it does not collide with this
        # command in the same API burst window. Set a timestamp that
        # _async_update_data checks before fetching.
        self._postpone_poll_until = time.monotonic() + COMMAND_MIN_INTERVAL

        # Retry loop — if the API returns rate-limit (code=10200/10406), wait and retry
        # automatically instead of surfacing an error to the user.
        # Max 3 attempts: initial + 2 retries with exponential backoff (15s, 30s).
        _max_retries = 3
        _retry_delay = 15  # seconds — first retry after 15s, second after 30s
        _attempt = 0

        while True:
            await self._ensure_token()

            headers = {
                "Authorization": f"bearer {self._token}",
                "Content-Type": "application/json",
            }

            _LOGGER.debug("SolaxCloud: sending EVC command to %s — %s (attempt %d)", url, payload, _attempt + 1)

            try:
                async with self._session.post(
                    url, json=payload, headers=headers, timeout=self._timeout
                ) as resp:
                    if resp.status == 401:
                        self._token = None
                        self._token_expires = 0.0
                        raise HomeAssistantError(
                            "SolaxCloud: Command rejected — token invalidated"
                        )
                    resp.raise_for_status()
                    data = await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise HomeAssistantError(
                    f"SolaxCloud: Command failed (network): {err}"
                ) from err

            code = data.get("code")
            if code == API_TOKEN_EXPIRED_CODE:
                _LOGGER.warning(
                    "SolaxCloud: Token expired/invalid (code=10402) — clearing token and ConfigEntry, command not sent"
                )
                self._token = None
                self._token_expires = 0.0
                self.hass.config_entries.async_update_entry(
                    self._entry,
                    data={
                        **self._entry.data,
                        CONF_ACCESS_TOKEN: None,
                        CONF_TOKEN_EXPIRES: 0.0,
                    },
                )
                raise HomeAssistantError(
                    "SolaxCloud: Token expired — please retry the command in a few seconds"
                )
            if _is_rate_limited_response(data):
                exception_msg = data.get("exception", "")
                if _attempt < _max_retries - 1:
                    _LOGGER.warning(
                        "SolaxCloud: Rate limit hit (code=%s, attempt=%d/%d) — retrying in %ds",
                        code,
                        _attempt + 1,
                        _max_retries,
                        _retry_delay,
                    )
                    await asyncio.sleep(_retry_delay)
                    _attempt += 1
                    _retry_delay = min(_retry_delay * 2, 60)
                    continue
                raise HomeAssistantError(
                    f"SolaxCloud: Rate limit exceeded after {_max_retries} attempts — please try again in a minute"
                )
            if code == API_RATE_LIMIT_CODE:
                # code=10200 = "Operation abnormality" — NOT a rate limit per API docs.
                # Log the actual `message` field so we can diagnose the real cause.
                # Possible causes: wrong EVC state, missing parameter, OCPP mode active,
                # invalid payload, device offline, or unsupported operation.
                actual_msg = data.get("message", "<no message>")
                exception_detail = data.get("exception", "<no exception>")
                _LOGGER.error(
                    "SolaxCloud: EVC command abnormality (code=10200) — "
                    "message='%s' exception='%s' full_response=%s",
                    actual_msg,
                    exception_detail,
                    data,
                )
                raise HomeAssistantError(
                    f"SolaxCloud: EVC command error — {actual_msg} (code=10200)"
                )
            if code != API_SUCCESS_CODE:
                msg = data.get("message", "unknown error")
                _LOGGER.error(
                    "SolaxCloud: EVC command error — %s (code=%s)", msg, code
                )
                raise HomeAssistantError(
                    f"SolaxCloud command failed: {msg} (code={code})"
                )
            # Success — break out of retry loop
            break

        request_id = data.get("requestId")
        _LOGGER.debug(
            "SolaxCloud: EVC command accepted — requestId=%s, polling in %ds",
            request_id,
            COMMAND_POLL_DELAY,
        )

        # Schedule delivery confirmation poll — non-blocking
        if request_id:
            async def _schedule_poll() -> None:
                await asyncio.sleep(COMMAND_POLL_DELAY)
                await self.async_poll_command_result(request_id)
            self.hass.async_create_task(_schedule_poll())

        return data

    async def async_poll_command_result(self, request_id: str) -> None:
        """Poll the command delivery result and notify on failure.

        Called automatically COMMAND_POLL_DELAY seconds after each control command.
        Uses the /openapi/apiRequestLog/listByCondition endpoint.

        Status codes (Appendix 8):
          1 = Pending   — device not yet reached (log debug, no notification)
          2 = Success   — command executed successfully
          3 = Delivered — delivered to device
          4 = Failed    — device rejected the command (persistent notification)

        NOTE: This endpoint returns code=10000 (same as data/control endpoints).
        Official docs state code=0 — this is incorrect, verified by live testing.
        """
        _LOGGER.debug("SolaxCloud: Polling command result for requestId=%s", request_id)

        try:
            async with self._session.post(
                COMMAND_POLL_URL,
                json={"requestId": request_id},
                headers={"Authorization": f"bearer {self._token}"},
                timeout=self._timeout,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "SolaxCloud: Command poll failed for requestId=%s: %s", request_id, err
            )
            return

        if data.get("code") != API_SUCCESS_CODE:
            _LOGGER.warning(
                "SolaxCloud: Poll endpoint error — code=%s message=%s",
                data.get("code"),
                data.get("message"),
            )
            return

        results: list[dict] = data.get("result") or []
        for device_result in results:
            sn = device_result.get("sn", "unknown")
            status = device_result.get("status")
            status_name = COMMAND_STATUS_MAP.get(status, f"Unknown({status})")

            if status in (2, 3):  # Success or Delivered
                _LOGGER.debug(
                    "SolaxCloud: Command %s — device %s: %s",
                    request_id,
                    sn,
                    status_name,
                )
            elif status == 1:  # Still pending after COMMAND_POLL_DELAY
                _LOGGER.debug(
                    "SolaxCloud: Command %s still pending for device %s after %ds",
                    request_id,
                    sn,
                    COMMAND_POLL_DELAY,
                )
            elif status == 4:  # Failed
                _LOGGER.error(
                    "SolaxCloud: Command %s FAILED for device %s",
                    request_id,
                    sn,
                )
                self.hass.async_create_task(
                    self.hass.services.async_call(
                        "persistent_notification",
                        "create",
                        {
                            "title": "SolaxCloud: Command Failed",
                            "message": (
                                f"A command to device **{sn}** could not be delivered.\n\n"
                                f"Request ID: `{request_id}`\n"
                                "Please check the device status in the SolaxCloud app."
                            ),
                            "notification_id": f"solax_cmd_failed_{request_id}",
                        },
                    )
                )
