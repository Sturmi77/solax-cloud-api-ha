"""Diagnostics support for SolaxCloud API integration.

Provides structured debug data exportable via HA UI (Settings → Devices → Download Diagnostics).
Sensitive fields (access_token, client_secret) are redacted — see SECURITY.md §3.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_ACCESS_TOKEN, CONF_CLIENT_SECRET, DOMAIN
from .coordinator import SolaxCloudApiCoordinator

# Fields redacted from the diagnostics output
_TO_REDACT = {CONF_ACCESS_TOKEN, CONF_CLIENT_SECRET}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: SolaxCloudApiCoordinator = hass.data[DOMAIN][entry.entry_id]
    return {
        "config_entry": async_redact_data(entry.as_dict(), _TO_REDACT),
        "coordinator_data": coordinator.data,
    }
