"""Diagnostics support for SolaxCloud API integration.

Provides structured debug data exportable via HA UI (Settings → Devices → Download Diagnostics).
Token and serial numbers are automatically masked before export.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

# Fields to redact completely from diagnostics output
_TO_REDACT = {
    "token",
    "access_token",
    "tokenId",
    "authorization",
}


def _mask_serial(value: Any) -> str | None:
    """Mask a serial number, keeping first 3 and last 3 characters."""
    if value is None:
        return None
    serial = str(value)
    if len(serial) <= 6:
        return "*" * len(serial)
    return f"{serial[:3]}***{serial[-3:]}"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Masks sensitive data (token, serial numbers) before returning.
    Raw API response is included to help debug missing or null sensor values.
    """
    coordinator = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)

    # Config entry info (token redacted)
    entry_data = dict(config_entry.data)
    evc_sn_raw = entry_data.get("evc_sn")

    diagnostics: dict[str, Any] = {
        "config_entry": {
            "entry_id": config_entry.entry_id,
            "title": config_entry.title,
            "domain": config_entry.domain,
            "version": config_entry.version,
            "evc_sn_masked": _mask_serial(evc_sn_raw),
            "token_present": bool(entry_data.get("token")),
            "token_length": len(str(entry_data.get("token", ""))) if entry_data.get("token") else 0,
        },
        "coordinator": {
            "available": coordinator is not None,
        },
    }

    if coordinator is None:
        return async_redact_data(diagnostics, _TO_REDACT)

    # Coordinator state
    diagnostics["coordinator"].update(
        {
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
        }
    )

    # Raw API response (with serial masked) — helps debug null/missing sensor values
    raw = deepcopy(getattr(coordinator, "raw_api_response", None))
    if isinstance(raw, dict):
        # Mask serial in raw response
        for key in ("deviceSn", "wifiSn", "inverterSN", "evc_sn"):
            if key in raw:
                raw[key] = _mask_serial(raw[key])
        if isinstance(raw.get("result"), dict):
            for key in ("deviceSn", "wifiSn"):
                if key in raw["result"]:
                    raw["result"][key] = _mask_serial(raw["result"][key])

    diagnostics["raw_api_response"] = raw

    # Filtered coordinator data (what sensors actually see)
    diagnostics["coordinator_data_keys"] = (
        sorted(coordinator.data.keys()) if isinstance(coordinator.data, dict) else None
    )

    return async_redact_data(diagnostics, _TO_REDACT)
