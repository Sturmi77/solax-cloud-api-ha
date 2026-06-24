"""DataUpdateCoordinator for SolaxCloud EVC integration."""

from __future__ import annotations

import logging
import asyncio
import time
from copy import deepcopy
from datetime import timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

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
    DATA_URL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TOKEN_LIFETIME,
    BUSINESS_TYPE_RESIDENTIAL,
    DEVICE_TYPE_EVC,
    DOMAIN,
    TOKEN_REFRESH_BUFFER,
    TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)


def _is_rate_limited_response(resp: dict) -> bool:
    if not isinstance(resp, dict):
        return False
    code = resp.get("code")
    if code == API_RATE_LIMIT_CODE_OFFICIAL:
        return True
    exception_msg = str(resp.get("exception", "")).lower()
    return any(m in exception_msg for m in ("rate limit", "maximum call threshold", "too many requests"))


class SolaxCloudApiCoordinator(DataUpdateCoordinator[dict]):
    """Coordinator for SolaxCloud API."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN, config_entry=entry,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self._entry = entry
        self._client_id: str = entry.data[CONF_CLIENT_ID]
        self._client_secret: str = entry.data[CONF_CLIENT_SECRET]
        self._token: str | None = None
        self._token_expires: float = 0.0
        self._last_command_time: float = 0.0
        self._postpone_poll_until: float = 0.0
        self.raw_api_response: dict | None = None

    def _load_token_from_entry(self) -> None:
        self._token = self._entry.data.get(CONF_ACCESS_TOKEN)
        self._token_expires = self._entry.data.get(CONF_TOKEN_EXPIRES, 0.0)

    async def _fetch_new_token(self) -> None:
        payload = {"client_id": self._client_id, "client_secret": self._client_secret, "grant_type": "client_credentials"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(TOKEN_URL, data=payload) as resp:
                    resp.raise_for_status()
                    result = await resp.json()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Token fetch failed: {err}") from err
        api_code = result.get("code")
        token_data = result.get("result")
        if api_code != API_AUTH_SUCCESS_CODE or not token_data:
            self._entry.async_start_reauth(self.hass)
            raise UpdateFailed(f"Invalid credentials (code={api_code})")
        self._token = token_data["access_token"]
        expires_in = token_data.get("expires_in", DEFAULT_TOKEN_LIFETIME)
        self._token_expires = time.time() + expires_in
        self.hass.config_entries.async_update_entry(
            self._entry,
            data={**self._entry.data, CONF_ACCESS_TOKEN: self._token, CONF_TOKEN_EXPIRES: self._token_expires},
        )

    async def _ensure_token(self) -> None:
        if self._token is None:
            self._load_token_from_entry()
        if self._token is None or time.time() >= (self._token_expires - TOKEN_REFRESH_BUFFER):
            await self._fetch_new_token()

    async def _async_update_data(self) -> dict:
        if time.monotonic() < self._postpone_poll_until:
            if self.data is not None:
                return self.data
            raise UpdateFailed("Poll skipped - command cooldown active")
        await self._ensure_token()
        evc_sn = self._entry.data.get(CONF_EVC_SN)
        params = {"snList": evc_sn, "deviceType": str(DEVICE_TYPE_EVC), "businessType": BUSINESS_TYPE_RESIDENTIAL}
        headers = {"Authorization": f"bearer {self._token}"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(DATA_URL, params=params, headers=headers) as resp:
                    if resp.status == 401:
                        self._token = None
                        self._token_expires = 0.0
                        raise UpdateFailed("Token invalidated")
                    resp.raise_for_status()
                    data = await resp.json()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Data fetch failed: {err}") from err
        code = data.get("code")
        if code == API_TOKEN_EXPIRED_CODE:
            self._token = None
            self._token_expires = 0.0
            self.hass.config_entries.async_update_entry(
                self._entry, data={**self._entry.data, CONF_ACCESS_TOKEN: None, CONF_TOKEN_EXPIRES: 0.0}
            )
            raise UpdateFailed("Token expired")
        if _is_rate_limited_response(data) or code == API_RATE_LIMIT_CODE:
            raise UpdateFailed(f"API error (code={code})")
        if code != API_SUCCESS_CODE:
            raise UpdateFailed(f"API error: {data.get('msg')} (code={code})")
        result = data.get("result")
        if not result:
            raise UpdateFailed("API returned empty result")
        self.raw_api_response = deepcopy(data)
        return result[0]

    async def async_send_evc_command(self, url: str, payload: dict) -> dict:
        now = time.monotonic()
        elapsed = now - self._last_command_time
        if elapsed < COMMAND_MIN_INTERVAL:
            raise HomeAssistantError(f"Please wait {COMMAND_MIN_INTERVAL - elapsed:.0f}s before next command")
        self._last_command_time = now
        self._postpone_poll_until = time.monotonic() + COMMAND_MIN_INTERVAL
        _max_retries, _retry_delay, _attempt = 3, 15, 0
        while True:
            await self._ensure_token()
            headers = {"Authorization": f"bearer {self._token}", "Content-Type": "application/json"}
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, headers=headers) as resp:
                        if resp.status == 401:
                            self._token = None
                            raise HomeAssistantError("Command rejected - token invalidated")
                        resp.raise_for_status()
                        data = await resp.json()
            except aiohttp.ClientError as err:
                raise HomeAssistantError(f"Command failed: {err}") from err
            code = data.get("code")
            if code == API_TOKEN_EXPIRED_CODE:
                self._token = None
                self._token_expires = 0.0
                raise HomeAssistantError("Token expired - retry in a few seconds")
            if _is_rate_limited_response(data):
                if _attempt < _max_retries - 1:
                    await asyncio.sleep(_retry_delay)
                    _attempt += 1
                    _retry_delay = min(_retry_delay * 2, 60)
                    continue
                raise HomeAssistantError(f"Rate limit exceeded after {_max_retries} attempts")
            if code != API_SUCCESS_CODE:
                raise HomeAssistantError(f"Command failed: {data.get('message')} (code={code})")
            break
        request_id = data.get("requestId")
        if request_id:
            async def _schedule_poll() -> None:
                await asyncio.sleep(COMMAND_POLL_DELAY)
                await self.async_poll_command_result(request_id)
            self.hass.async_create_task(_schedule_poll())
        return data

    async def async_poll_command_result(self, request_id: str) -> None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    COMMAND_POLL_URL, json={"requestId": request_id},
                    headers={"Authorization": f"bearer {self._token}"},
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Command poll failed for %s: %s", request_id, err)
            return
        results: list[dict] = data.get("result") or []
        for device_result in results:
            sn = device_result.get("sn", "unknown")
            status = device_result.get("status")
            if status == 4:
                self.hass.async_create_task(
                    self.hass.services.async_call(
                        "persistent_notification", "create",
                        {"title": "SolaxCloud: Command Failed",
                         "message": f"Command to device **{sn}** failed.\nRequest ID: `{request_id}`",
                         "notification_id": f"solax_cmd_failed_{request_id}"},
                    )
                )
