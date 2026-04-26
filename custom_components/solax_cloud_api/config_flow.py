"""Config Flow for SolaxCloud API integration.

Flow:
  1. User enters client_id + client_secret
  2. Integration fetches & validates a token from the SolaxCloud API
  3. Token + credentials stored in ConfigEntry.data
  4. client_id used as unique_id — prevents duplicate setup (single_config_entry
     also enforced in manifest.json)

Re-auth flow:
  - Triggered by coordinator when a token-fetch 401 occurs
     (i.e. client_secret was rotated in the Developer Portal)
  - User enters new client_secret; new token is fetched immediately
  - Existing ConfigEntry updated in-place via async_update_reload_and_abort
  - Integration is reloaded automatically after update

Security notes — see SECURITY.md:
  - client_secret displayed as password field (PasswordSelector)
  - Never logged; stored in ConfigEntry.data (same security level as secrets.yaml)
  - access_token stored in ConfigEntry.data — never in logs

See ARCHITECTURE.md §5 for full design rationale.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

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

# TODO Issue #5: replace with device-list API discovery once inverter/battery endpoints are implemented
DEFAULT_EVC_SN = "C32203J3501037"

# ── Schemas ──────────────────────────────────────────────────────────────────

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_ID): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="off")
        ),
        vol.Required(CONF_CLIENT_SECRET): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)

STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_SECRET): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


# ── Custom exceptions ────────────────────────────────────────────────────────


class SolaxAuthError(Exception):
    """Raised when SolaxCloud auth endpoint returns a non-zero code.

    The auth endpoint always returns HTTP 200. Errors are indicated by the JSON 'code' field:
      code=0     → success
      code=10400 → invalid client_id / client_secret
      code=10401 → username/password incorrect (OAuth2 flow only)
    """


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _fetch_token(client_id: str, client_secret: str) -> tuple[str, float]:
    """Fetch an OAuth2 token from SolaxCloud and return (access_token, expires_at).

    NOTE: SolaxCloud always returns HTTP 200, even for auth errors.
    Actual errors are indicated by the JSON 'code' field:
      code=0     → success; access_token present in result.access_token
      code=10400 → bad credentials (invalid client_id / client_secret)

    Response structure:
      {
        "code": 0,
        "result": {
          "access_token": "...",
          "token_type": "bearer",
          "expires_in": 2591999,
          "scope": "...",
          "grant_type": "client_credentials"
        }
      }

    Raises:
        SolaxAuthError               — API returned non-zero code
        aiohttp.ClientResponseError  — unexpected HTTP-level error
        aiohttp.ClientError          — network / connection error
        KeyError                     — unexpected response shape
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

    # Auth endpoint returns code=0 on success, code=10400 on bad credentials.
    # (Data endpoint uses code=10000 for success — different code space.)
    api_code = result.get("code")
    token_data = result.get("result")
    if api_code != 0 or not token_data:  # noqa: PLR2004
        msg = result.get("message", "Unknown error")
        _LOGGER.warning(
            "SolaxCloud token API error: %s (code=%s)", msg, api_code
        )
        raise SolaxAuthError(f"API error {api_code}: {msg}")

    token: str = token_data["access_token"]
    expires_in: int = token_data.get("expires_in", DEFAULT_TOKEN_LIFETIME)
    return token, time.time() + expires_in


# ── Config Flow ───────────────────────────────────────────────────────────────


class SolaxCloudConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the SolaxCloud API config flow.

    VERSION history:
      1 — initial: client_id, client_secret, access_token, token_expires, evc_sn
    """

    VERSION = 1

    # ── Setup step ────────────────────────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle initial user setup.

        Validates credentials by fetching a token, sets client_id as unique_id
        to prevent duplicate integrations (also enforced by single_config_entry).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            client_id: str = user_input[CONF_CLIENT_ID].strip()
            client_secret: str = user_input[CONF_CLIENT_SECRET]

            # Prevent duplicate — unique_id = client_id (stable, non-user-changeable)
            await self.async_set_unique_id(client_id)
            self._abort_if_unique_id_configured()

            try:
                token, expires_at = await _fetch_token(client_id, client_secret)
            except SolaxAuthError:
                errors["base"] = "invalid_auth"
            except aiohttp.ClientResponseError as err:
                _LOGGER.warning(
                    "SolaxCloud setup: HTTP %s during token fetch", err.status
                )
                errors["base"] = "cannot_connect"
            except (aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.warning("SolaxCloud setup: connection error: %s", err)
                errors["base"] = "cannot_connect"
            except KeyError:
                _LOGGER.warning("SolaxCloud setup: unexpected token response shape")
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
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    # ── Re-auth flow ──────────────────────────────────────────────────────────

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication triggered by a 401 from the token endpoint.

        Called automatically by HA when the coordinator raises ConfigEntryAuthFailed
        or calls async_start_reauth(). Delegates immediately to the confirm step.
        """
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-auth by entering a new client_secret.

        client_id is read from the existing entry and displayed in the description
        so the user can verify they are re-authorising the correct application.
        Uses async_update_reload_and_abort to update + reload in one step.
        """
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            client_id: str = reauth_entry.data[CONF_CLIENT_ID]
            new_secret: str = user_input[CONF_CLIENT_SECRET]

            try:
                token, expires_at = await _fetch_token(client_id, new_secret)
            except SolaxAuthError:
                errors["base"] = "invalid_auth"
            except aiohttp.ClientResponseError as err:
                _LOGGER.warning(
                    "SolaxCloud reauth: HTTP %s during token fetch", err.status
                )
                errors["base"] = "cannot_connect"
            except (aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.warning("SolaxCloud reauth: connection error: %s", err)
                errors["base"] = "cannot_connect"
            except KeyError:
                _LOGGER.warning("SolaxCloud reauth: unexpected token response shape")
                errors["base"] = "cannot_connect"
            else:
                # Update entry in-place, reload integration, abort flow
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={
                        CONF_CLIENT_SECRET: new_secret,
                        CONF_ACCESS_TOKEN: token,
                        CONF_TOKEN_EXPIRES: expires_at,
                    },
                )

        client_id_hint = reauth_entry.data.get(CONF_CLIENT_ID, "")
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"client_id": client_id_hint},
        )
