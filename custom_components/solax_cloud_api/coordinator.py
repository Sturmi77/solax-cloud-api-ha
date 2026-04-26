"""DataUpdateCoordinator with automatic OAuth2 token lifecycle management.

Key design:
- Token persisted in ConfigEntry.data — survives HA restart without new API call
- _ensure_token() called before every update — only fetches when needed (1h buffer)
- Single config entry enforced — prevents parallel token invalidation
- Never logs full token — only first 4 chars for debugging

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


class SolaxCoordinator(DataUpdateCoordinator):
    """Coordinator for SolaxCloud API — manages token lifecycle and data fetching."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self._entry = entry
        self._client_id: str = entry.data[CONF_CLIENT_ID]
        self._client_secret: str = entry.data[CONF_CLIENT_SECRET]
        self._token: str | None = None
        self._token_expires: float = 0.0

    # ── Token Management ─────────────────────────────────────────────────────

    async def _load_token_from_entry(self) -> None:
        """Load token and expiry from ConfigEntry (populated by config flow or previous fetch)."""
        self._token = self._entry.data.get(CONF_ACCESS_TOKEN)
        self._token_expires = self._entry.data.get(CONF_TOKEN_EXPIRES, 0.0)
        if self._token:
            _LOGGER.debug(
                "Loaded token from ConfigEntry: %s... (expires in %.0f hours)",
                self._token[:4],
                max(0, self._token_expires - time.time()) / 3600,
            )

    async def _fetch_new_token(self) -> None:
        """Fetch a new token from the SolaxCloud API and persist it in ConfigEntry.

        WARNING: Each call immediately invalidates the previously active token.
        Only call this when genuinely needed (token missing or within refresh buffer).
        """
        _LOGGER.info("SolaxCloud API: Fetching new access token")
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
            raise UpdateFailed(f"Token fetch failed: {err}") from err

        self._token = result["access_token"]
        expires_in = result.get("expires_in", DEFAULT_TOKEN_LIFETIME)
        self._token_expires = time.time() + expires_in

        # Persist — next HA restart will reuse this token without a new API call
        self.hass.config_entries.async_update_entry(
            self._entry,
            data={
                **self._entry.data,
                CONF_ACCESS_TOKEN: self._token,
                CONF_TOKEN_EXPIRES: self._token_expires,
            },
        )
        _LOGGER.info(
            "SolaxCloud API: New token stored (%s...), valid for %.1f days",
            self._token[:4],
            expires_in / 86400,
        )

    async def _ensure_token(self) -> None:
        """Ensure a valid token is available — fetch only when necessary."""
        if self._token is None:
            await self._load_token_from_entry()

        needs_refresh = self._token is None or time.time() >= (
            self._token_expires - TOKEN_REFRESH_BUFFER
        )
        if needs_refresh:
            await self._fetch_new_token()

    # ── Data Fetching ────────────────────────────────────────────────────────

    async def _async_update_data(self) -> dict:
        """Fetch EVC realtime data from SolaxCloud API."""
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
                async with session.get(DATA_URL, params=params, headers=headers) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Data fetch failed: {err}") from err

        if data.get("code") != API_SUCCESS_CODE:
            raise UpdateFailed(
                f"SolaxCloud API error: {data.get('msg')} (code={data.get('code')})"
            )

        result = data.get("result")
        if not result:
            raise UpdateFailed("SolaxCloud API returned empty result list")

        return result[0]
