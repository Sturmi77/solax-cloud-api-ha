"""Select platform for SolaxCloud API — EVC control entities.

Entities:
  1. EvcWorkModeSelect    — Stop / Fast / ECO / Green
     API: POST /openapi/v2/device/evc_control/set_evc_work_mode

  2. EvcStartModeSelect   — Plug & Charge / Swipe Card / APP
     API: POST /openapi/v2/device/evc_control/set_evc_start_mode

  3. EvcChargeSceneSelect — Home / OCPP / Standard
     API: POST /openapi/v2/device/evc_control/set_charge_scene

All entities reuse coordinator.async_send_evc_command() for token management.
See ARCHITECTURE.md §8 for design rationale.
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    BUSINESS_TYPE_RESIDENTIAL,
    CONF_EVC_SN,
    DOMAIN,
    EVC_CHARGE_SCENE_TO_INT,
    EVC_CONTROL_SCENE_URL,
    EVC_CONTROL_START_MODE_URL,
    EVC_CONTROL_WORK_MODE_URL,
    EVC_DEFAULT_CURRENT_GEAR,
    EVC_START_MODE_TO_INT,
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
    """Set up SolaxCloud select entities from config entry."""
    coordinator: SolaxCoordinator = hass.data[DOMAIN][entry.entry_id]
    evc_sn = entry.data.get(CONF_EVC_SN, "unknown")

    async_add_entities([
        EvcWorkModeSelect(coordinator, entry, evc_sn),
        EvcStartModeSelect(coordinator, entry, evc_sn),
        EvcChargeSceneSelect(coordinator, entry, evc_sn),
    ])


class EvcWorkModeSelect(CoordinatorEntity[SolaxCoordinator], SelectEntity):
    """Select entity to control the EV Charger work mode.

    Current state is read from coordinator data (deviceWorkingMode field).
    Writing sends a command to the SolaxCloud API; state updates arrive
    on the next regular poll cycle.
    """

    _attr_has_entity_name = True
    _attr_name = "EVC Work Mode"
    _attr_icon = "mdi:ev-station"
    _attr_options = list(EVC_WORK_MODE_TO_INT.keys())

    def __init__(
        self,
        coordinator: SolaxCoordinator,
        entry: ConfigEntry,
        evc_sn: str,
    ) -> None:
        super().__init__(coordinator)
        self._evc_sn = evc_sn
        self._attr_unique_id = f"{entry.entry_id}_evc_work_mode_select"
        self._attr_device_info = _evc_device_info(DOMAIN, evc_sn)

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
            "businessType": BUSINESS_TYPE_RESIDENTIAL,
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

        # State will update on the next regular poll (DEFAULT_SCAN_INTERVAL)
        # Do NOT call async_request_refresh() here — it triggers an immediate API call
        # that hits the rate limit when combined with the command call above.


class EvcStartModeSelect(CoordinatorEntity[SolaxCoordinator], SelectEntity):
    """Select entity to control the EV Charger start mode.

    Determines how a charging session is initiated:
      Plug & Charge — session starts automatically on plug-in
      Swipe Card    — RFID card required before charging starts
      APP           — session must be started via the SolaxCloud APP

    NOTE: The realtime_data API does not return the current startMode value,
    so current_option always returns None (unknown). The entity is still fully
    writable — HA will show the last user-selected option via _attr_current_option
    after a successful command.
    """

    _attr_has_entity_name = True
    _attr_name = "EVC Start Mode"
    _attr_icon = "mdi:key-wireless"
    _attr_options = list(EVC_START_MODE_TO_INT.keys())

    def __init__(
        self,
        coordinator: SolaxCoordinator,
        entry: ConfigEntry,
        evc_sn: str,
    ) -> None:
        super().__init__(coordinator)
        self._evc_sn = evc_sn
        self._attr_unique_id = f"{entry.entry_id}_evc_start_mode_select"
        self._attr_current_option = None   # API does not report current value
        self._attr_device_info = _evc_device_info(DOMAIN, evc_sn)

    @property
    def available(self) -> bool:
        """Return True if EVC is online and coordinator data is present."""
        return self.coordinator.last_update_success and self.coordinator.data is not None

    async def async_select_option(self, option: str) -> None:
        """Send start mode command to SolaxCloud API."""
        mode_int = EVC_START_MODE_TO_INT.get(option)
        if mode_int is None:
            raise HomeAssistantError(f"Unknown start mode: {option}")

        payload = {
            "snList": [self._evc_sn],
            "startMode": mode_int,
            "businessType": BUSINESS_TYPE_RESIDENTIAL,
        }

        _LOGGER.info(
            "SolaxCloud: Setting EVC start mode → %s (startMode=%s)", option, mode_int
        )

        await self.coordinator.async_send_evc_command(
            EVC_CONTROL_START_MODE_URL, payload
        )

        # Persist selection locally — API does not report it back in realtime_data
        self._attr_current_option = option
        self.async_write_ha_state()


class EvcChargeSceneSelect(CoordinatorEntity[SolaxCoordinator], SelectEntity):
    """Select entity to control the EV Charger charge scene.

    Determines the charging scenario:
      Home     — residential charging (standard mode)
      OCPP     — connect to an OCPP backend (requires URL + ChargerId — future option)
      Standard — standard / solar mode

    NOTE: OCPP requires ocppUrl + ocppChargerId. Selecting OCPP here switches the
    scene but does not configure OCPP parameters (deferred to Issue #9 options flow).
    The realtime_data API does not return the current chargerScene value.
    """

    _attr_has_entity_name = True
    _attr_name = "EVC Charge Scene"
    _attr_icon = "mdi:home-lightning-bolt"
    _attr_options = list(EVC_CHARGE_SCENE_TO_INT.keys())

    def __init__(
        self,
        coordinator: SolaxCoordinator,
        entry: ConfigEntry,
        evc_sn: str,
    ) -> None:
        super().__init__(coordinator)
        self._evc_sn = evc_sn
        self._attr_unique_id = f"{entry.entry_id}_evc_charge_scene_select"
        self._attr_current_option = None   # API does not report current value
        self._attr_device_info = _evc_device_info(DOMAIN, evc_sn)

    @property
    def available(self) -> bool:
        """Return True if EVC is online and coordinator data is present."""
        return self.coordinator.last_update_success and self.coordinator.data is not None

    async def async_select_option(self, option: str) -> None:
        """Send charge scene command to SolaxCloud API."""
        scene_int = EVC_CHARGE_SCENE_TO_INT.get(option)
        if scene_int is None:
            raise HomeAssistantError(f"Unknown charge scene: {option}")

        payload: dict = {
            "snList": [self._evc_sn],
            "chargerScene": scene_int,
            "businessType": BUSINESS_TYPE_RESIDENTIAL,
        }

        _LOGGER.info(
            "SolaxCloud: Setting EVC charge scene → %s (chargerScene=%s)", option, scene_int
        )

        await self.coordinator.async_send_evc_command(EVC_CONTROL_SCENE_URL, payload)

        # Persist selection locally — API does not report it back in realtime_data
        self._attr_current_option = option
        self.async_write_ha_state()
