"""SolaxCloud API — Home Assistant Custom Integration.

Supports EV Charger (Phase 1), Inverter and Battery (Phase 2/3).
See ARCHITECTURE.md for full design documentation.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import SolaxCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.SELECT, Platform.NUMBER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SolaxCloud API from a config entry."""
    coordinator = SolaxCoordinator(hass, entry)

    # Perform first refresh — raises ConfigEntryNotReady on failure so HA retries
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # NOTE: No update_listener registered intentionally.
    # add_update_listener() fires on ANY async_update_entry() call, including
    # the coordinator's internal token saves. Registering a listener that reloads
    # the integration would cause a reload loop on every token persistence.
    # Re-add when an options flow exists and token saves are separated from options updates.
    # See: Issue #7 (HACS release) — options flow planned.
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
