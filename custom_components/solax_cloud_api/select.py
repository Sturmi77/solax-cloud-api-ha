"""Select platform for SolaxCloud API — EVC Work Mode control.

Allows the user to switch the EV Charger between Stop / Fast / ECO / Green
directly from the HA UI or automations.

API: POST /openapi/v2/device/evc_control/set_evc_work_mode
     {"snList": ["<sn>"], "workMode": <int>, "businessType": 1}

When switching to ECO or Green, the default currentGear from const.py is sent.
Use the companion number entity (evc_charging_current) to change the gear after
switching mode.

See ARCHITECTURE.md §8 for design rationale.
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_EVC_SN,
    DOMAIN,
    EVC_CONTROL_WORK_MODE_URL,
    EVC_CURRENT_GEAR_OPTIONS,
    EVC_DEFAULT_CURRENT_GEAR,
    EVC_WORK_MODE_TO_INT,
    EVC_WORKING_MODE_MAP,
)
from .coordinator import SolaxCoordinator

_LOGGER = logging.getLogger(__name__)

# Human-readable options shown in HA UI (order matters — matches workMode 0–3)
WORK_MODE_OPTIONS = ["Stop", "Fast", "ECO", "Green"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SolaxCloud select entities from config entry."""
    coordinator: SolaxCoordinator = hass.data[DOMAIN][entry.entry_id]
    evc_sn = entry.data.get(CONF_EVC_SN, "unknown")

    async_add_entities([EvcWorkModeSelect(coordinator, entry, evc_sn)])


class EvcWorkModeSelect(CoordinatorEntity[SolaxCoordinator], SelectEntity):
    """Select entity to control the EV Charger work mode.

    Current state is read from coordinator data (deviceWorkingMode field).
    Writing sends a command to the SolaxCloud API and then requests a
    coordinator refresh so the UI reflects the new state immediately.
    """

    _attr_has_entity_name = True
    _attr_name = "EVC Work Mode"
    _attr_icon = "mdi:ev-station"
    _attr_options = WORK_MODE_OPTIONS

    def __init__(
        self,
        coordinator: SolaxCoordinator,
        entry: ConfigEntry,
        evc_sn: str,
    ) -> None:
        super().__init__(coordinator)
        self._evc_sn = evc_sn
        self._attr_unique_id = f"{entry.entry_id}_evc_work_mode_select"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, evc_sn)},
            name="SolaxCloud EV Charger",
            manufacturer="Solax Power",
            model="X3-EVC-22K",
            serial_number=evc_sn,
            configuration_url="https://developer.solaxcloud.com",
        )

    @property
    def current_option(self) -> str | None:
        """Return current work mode from latest coordinator data."""
        if self.coordinator.data is None:
            return None
        raw = self.coordinator.data.get("deviceWorkingMode")
        if raw is None:
            return None
        # API returns int; EVC_WORKING_MODE_MAP maps int → string
        return EVC_WORKING_MODE_MAP.get(raw)

    @property
    def available(self) -> bool:
        """Unavailable when coordinator has no data."""
        return self.coordinator.last_update_success and self.coordinator.data is not None

    async def async_select_option(self, option: str) -> None:
        """Send work mode command to SolaxCloud API.

        For ECO and Green modes, sends the default currentGear automatically.
        The user can fine-tune the current afterwards using the number entity.
        """
        work_mode_int = EVC_WORK_MODE_TO_INT.get(option)
        if work_mode_int is None:
            raise HomeAssistantError(f"Unknown work mode: {option}")

        payload: dict = {
            "snList": [self._evc_sn],
            "workMode": work_mode_int,
            "businessType": 1,
        }

        # ECO and Green require a currentGear — send the default
        default_gear = EVC_DEFAULT_CURRENT_GEAR.get(option)
        if default_gear is not None:
            payload["currentGear"] = default_gear

        _LOGGER.info(
            "SolaxCloud: Setting EVC work mode → %s (workMode=%s%s)",
            option,
            work_mode_int,
            f", currentGear={default_gear}" if default_gear else "",
        )

        await self.coordinator.async_send_evc_command(
            EVC_CONTROL_WORK_MODE_URL, payload
        )

        # Refresh coordinator so sensor + select state update immediately
        await self.coordinator.async_request_refresh()
