"""Config Flow for SolaxCloud API integration.

Flow:
  1. User enters client_id + client_secret
  2. Integration validates by fetching a token
  3. Token stored in ConfigEntry — no further manual steps required

Re-auth flow (v1.1):
  - Triggered when token fetch returns authentication error
  - Allows client_secret rotation via HA UI without reinstalling
"""

from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_EVC_SN,
    CONF_TOKEN_EXPIRES,
    DEFAULT_TOKEN_LIFETIME,
    DOMAIN,
    TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)

# TODO Phase 2: discover EVC SN from /openapi/v2/device/list instead of hardcoding
DEFAULT_EVC_SN = "C32203J3501037"

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_ID): str,
        vol.Required(CONF_CLIENT_SECRET): str,
    }
)


async def _fetch_token(client_id: str, client_secret: str) -> tuple[str, float]:
    """Fetch a token and return (access_token, expires_at_timestamp).

    Raises aiohttp.ClientError or KeyError on failure.
    """
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(TOKEN_URL, data=payload) as resp:
            resp.raise_for_status()
            result = await resp.json()
    token = result["access_token"]
    expires_in = result.get("expires_in", DEFAULT_TOKEN_LIFETIME)
    expires_at = time.time() + expires_in
    return token, expires_at


class SolaxCloudConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the SolaxCloud API config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle initial user setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client_id = user_input[CONF_CLIENT_ID]
            client_secret = user_input[CONF_CLIENT_SECRET]

            try:
                token, expires_at = await _fetch_token(client_id, client_secret)
            except aiohttp.ClientResponseError as err:
                _LOGGER.error("SolaxCloud auth failed (HTTP %s)", err.status)
                errors["base"] = "invalid_auth"
            except (aiohttp.ClientError, KeyError) as err:
                _LOGGER.error("SolaxCloud connection error: %s", err)
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title="SolaxCloud API",
                    data={
                        CONF_CLIENT_ID: client_id,
                        CONF_CLIENT_SECRET: client_secret,
                        CONF_ACCESS_TOKEN: token,
                        CONF_TOKEN_EXPIRES: expires_at,
                        CONF_EVC_SN: DEFAULT_EVC_SN,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    # ── Re-auth Flow (v1.1 — placeholder) ───────────────────────────────────

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication (e.g., after client_secret rotation in Developer Portal)."""
        # TODO v1.1: implement full re-auth flow
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-auth with new client_secret."""
        errors: dict[str, str] = {}
        entry: ConfigEntry = self._get_reauth_entry()

        if user_input is not None:
            try:
                token, expires_at = await _fetch_token(
                    entry.data[CONF_CLIENT_ID],
                    user_input[CONF_CLIENT_SECRET],
                )
            except (aiohttp.ClientError, KeyError):
                errors["base"] = "invalid_auth"
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={
                        **entry.data,
                        CONF_CLIENT_SECRET: user_input[CONF_CLIENT_SECRET],
                        CONF_ACCESS_TOKEN: token,
                        CONF_TOKEN_EXPIRES: expires_at,
                    },
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        schema = vol.Schema({vol.Required(CONF_CLIENT_SECRET): str})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
        )
