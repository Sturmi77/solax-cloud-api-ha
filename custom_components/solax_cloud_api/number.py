"""Number platform for SolaxCloud API — EVC Charging Current control.

Allows the user to set the charging current (Ampere) for ECO and Green modes.
The entity is unavailable when work mode is Stop or Fast (no current setting needed).

Valid currentGear values per mode (from API docs):
  ECO:   6, 10, 16, 20, 25 A
  Green: 3, 6 A

The number entity uses the min/max of the valid range for the current mode.
The user picks any value in that range; we snap it to the nearest valid gear.

API: POST /openapi/v2/device/evc_control/set_evc_work_mode
     {"snList": ["<sn>"], "workMode": <int>, "currentGear": <int>, "businessType": 1}

See ARCHITECTURE.md §8 for design rationale.
"""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    BUSINESS_TYPE_RESIDENTIAL,
    CONF_EVC_SN,
    DOMAIN,
    EVC_CONTROL_WORK_MODE_URL,
    EVC_CURRENT_GEAR_OPTIONS,
    EVC_WORK_MODE_TO_INT,
    EVC_WORKING_MODE_MAP,
    _evc_device_info,
)
from .coordinator import SolaxCoordinator

_LOGGER = logging.getLogger(__name__)

# Limit concurrent update calls to 1 — appropriate for cloud API to avoid rate limiting
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SolaxCloud number entities from config entry."""
    coordinator: SolaxCoordinator = hass.data[DOMAIN][entry.entry_id]
    evc_sn = entry.data.get(CONF_EVC_SN, "unknown")

    async_add_entities([EvcChargingCurrentNumber(coordinator, entry, evc_sn)])


class EvcChargingCurrentNumber(CoordinatorEntity[SolaxCoordinator], NumberEntity):
    """Number entity to set EVC charging current.

    Only available when deviceWorkingMode is ECO or Green.
    Snaps user input to the nearest valid currentGear for the current mode.
    """

    _attr_has_entity_name = True
    _attr_name = "EVC Charging Current"
    _attr_icon = "mdi:current-ac"
    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_mode = NumberMode.BOX          # show as input box, not slider
    _attr_native_step = 1.0

    def __init__(
        self,
        coordinator: SolaxCoordinator,
        entry: ConfigEntry,
        evc_sn: str,
    ) -> None:
        super().__init__(coordinator)
        self._evc_sn = evc_sn
        self._attr_unique_id = f"{entry.entry_id}_evc_charging_current"
        self._attr_device_info = _evc_device_info(DOMAIN, evc_sn)
        self._optimistic_value: float | None = None  # set locally after command for immediate UI feedback

    def _current_mode_name(self) -> str | None:
        """Return current work mode name (Stop/Fast/ECO/Green) or None."""
        if self.coordinator.data is None:
            return None
        raw = self.coordinator.data.get("deviceWorkingMode")
        return EVC_WORKING_MODE_MAP.get(raw)

    def _valid_gears(self) -> list[int] | None:
        """Return valid currentGear values for the current mode, or None."""
        mode = self._current_mode_name()
        if mode is None:
            return None
        return EVC_CURRENT_GEAR_OPTIONS.get(mode)

    @property
    def available(self) -> bool:
        """Available only when work mode requires a current setting (ECO or Green)."""
        if not self.coordinator.last_update_success or self.coordinator.data is None:
            return False
        return self._valid_gears() is not None

    @property
    def native_min_value(self) -> float:
        """Return minimum valid current for active mode."""
        gears = self._valid_gears()
        return float(min(gears)) if gears else 3.0

    @property
    def native_max_value(self) -> float:
        """Return maximum valid current for active mode."""
        gears = self._valid_gears()
        return float(max(gears)) if gears else 25.0

    @property
    def native_value(self) -> float | None:
        """Return current charging current.

        Returns the optimistic (locally cached) value immediately after a command,
        falling back to coordinator data on the next poll.
        """
        if self._optimistic_value is not None:
            return self._optimistic_value
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("currentGear")

    async def async_set_native_value(self, value: float) -> None:
        """Send charging current command — snaps to nearest valid gear."""
        gears = self._valid_gears()
        if gears is None:
            raise HomeAssistantError(
                "Cannot set current — EVC is in Stop or Fast mode"
            )

        # Snap to nearest valid gear
        target_gear = min(gears, key=lambda g: abs(g - value))
        mode_name = self._current_mode_name()
        work_mode_int = EVC_WORK_MODE_TO_INT.get(mode_name)

        if work_mode_int is None:
            raise HomeAssistantError(
                f"Cannot determine work mode int for mode: {mode_name}"
            )

        payload = {
            "snList": [self._evc_sn],
            "workMode": work_mode_int,
            "currentGear": target_gear,
            "businessType": BUSINESS_TYPE_RESIDENTIAL,
        }

        _LOGGER.info(
            "SolaxCloud: Setting EVC current → %sA (requested %.0fA, mode=%s)",
            target_gear,
            value,
            mode_name,
        )

        await self.coordinator.async_send_evc_command(
            EVC_CONTROL_WORK_MODE_URL, payload
        )

        # Optimistic update — show new value immediately without waiting for next poll
        self._optimistic_value = float(target_gear)
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        """Reset optimistic state on coordinator update so real API value takes over."""
        self._optimistic_value = None
        super()._handle_coordinator_update()
