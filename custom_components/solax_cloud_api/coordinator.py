"""DataUpdateCoordinator with automatic OAuth2 token lifecycle management.

Key design decisions:
- config_entry passed explicitly to super().__init__() — required from HA 2026.8+
- Token persisted in ConfigEntry.data — survives HA restart without new API call
- _ensure_token() called before every update — only fetches when needed (1h buffer)
- Single config entry enforced via manifest.json — prevents parallel token invalidation
- Never logs full token — only first 4 chars for debugging
- 401 response → triggers re-auth flow (user sees notification in HA UI)

See ARCHITECTURE.md §6 and SECURITY.md §2 for details.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    API_SUCCESS_CODE,
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_EVC_SN,
    CONF_TOKEN_EXPIRES,
    DATA_URL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TOKEN_LIFETIME,
    DEVICE_TYPE_EVC,
    DOMAIN,
    TOKEN_REFRESH_BUFFER,
    TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)


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

    # ── Token Management ─────────────────────────────────────────────────────

    async def _load_token_from_entry(self) -> None:
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
        Only call this when the token is genuinely missing or within TOKEN_REFRESH_BUFFER
        of expiry. Never call from background tasks or polling loops directly.
        """
        _LOGGER.info("SolaxCloud: Fetching new access token")
        payload = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "grant_type": "client_credentials",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(TOKEN_URL, data=payload) as resp:
                    resp.raise_for_status()
                    result = await resp.json()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"SolaxCloud: Token fetch failed: {err}") from err

        # NOTE: SolaxCloud always returns HTTP 200 — auth errors indicated by
        # application-level 'code' field (e.g. 10400 = bad credentials).
        api_code = result.get("code")
        if api_code != 200:  # noqa: PLR2004
            msg = result.get("message", "Unknown error")
            _LOGGER.error(
                "SolaxCloud: Token API error — %s (code=%s) — triggering re-auth",
                msg,
                api_code,
            )
            self._entry.async_start_reauth(self.hass)
            raise UpdateFailed(f"Invalid credentials (code={api_code}) — re-authentication required")

        access_token = result.get("access_token")
        if not access_token:
            raise UpdateFailed(
                f"SolaxCloud: Token response missing access_token: {result}"
            )

        self._token = access_token
        expires_in = result.get("expires_in", DEFAULT_TOKEN_LIFETIME)
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
            await self._load_token_from_entry()

        needs_refresh = self._token is None or time.time() >= (
            self._token_expires - TOKEN_REFRESH_BUFFER
        )
        if needs_refresh:
            await self._fetch_new_token()

    # ── Data Fetching ────────────────────────────────────────────────────────

    async def _async_update_data(self) -> dict:
        """Fetch EVC realtime data — called every DEFAULT_SCAN_INTERVAL seconds.

        Raises UpdateFailed on any error so HA marks the integration unavailable
        and retries on the next interval.
        """
        await self._ensure_token()

        evc_sn = self._entry.data.get(CONF_EVC_SN)
        params = {
            "snList": evc_sn,
            "deviceType": str(DEVICE_TYPE_EVC),
            "businessType": "1",
        }
        headers = {"Authorization": f"bearer {self._token}"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    DATA_URL, params=params, headers=headers
                ) as resp:
                    if resp.status == 401:
                        # Token was invalidated externally (e.g. another app fetched a new token)
                        _LOGGER.warning(
                            "SolaxCloud: Data request returned 401 — token invalidated, "
                            "will fetch new token on next update"
                        )
                        self._token = None          # force re-fetch on next cycle
                        self._token_expires = 0.0
                        raise UpdateFailed("Token invalidated — will refresh on next update")
                    resp.raise_for_status()
                    data = await resp.json()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"SolaxCloud: Data fetch failed: {err}") from err

        code = data.get("code")
        if code != API_SUCCESS_CODE:
            msg = data.get("msg", "unknown error")
            _LOGGER.error("SolaxCloud API error: %s (code=%s)", msg, code)
            raise UpdateFailed(f"SolaxCloud API error: {msg} (code={code})")

        result = data.get("result")
        if not result:
            raise UpdateFailed("SolaxCloud: API returned empty result list")

        _LOGGER.debug(
            "SolaxCloud: EVC data received — status=%s power=%sW",
            result[0].get("deviceStatus"),
            result[0].get("chargingPower"),
        )
        return result[0]
