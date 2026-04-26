"""Sensor platform for SolaxCloud API — Phase 1: EV Charger (X3-EVC-22K).

Device class / state_class rationale — see ARCHITECTURE.md §7:
  - chargingPower:             POWER  + MEASUREMENT      → instantaneous W (API: businessType=1 delivers W)
  - totalChargeEnergy:         ENERGY + TOTAL_INCREASING → monotonic lifetime kWh; primary Energy Dashboard sensor
  - chargingEnergyThisSession: ENERGY + TOTAL            → resets to 0 on new session; NOT TOTAL_INCREASING
  - l1/l2/l3Current:           CURRENT + MEASUREMENT     → monitoring / automations
  - chargingTimeThisSession:   DURATION + MEASUREMENT    → session duration in seconds
  - deviceStatus:              no device_class           → text enum, not tracked in statistics
  - deviceWorkingMode:         no device_class           → text enum (Stop/Fast/ECO/Green)

Field names verified against live API response 2026-04-26:
  deviceStatus, deviceWorkingMode, chargingPower, totalChargeEnergy,
  chargingEnergyThisSession, chargingTimeThisSession, l1Current, l2Current, l3Current
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CONF_EVC_SN, DOMAIN, EVC_STATUS_MAP, EVC_WORKING_MODE_MAP, _evc_device_info
from .coordinator import SolaxCoordinator

# Limit concurrent update calls to 1 — appropriate for cloud API to avoid rate limiting
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class SolaxSensorEntityDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with a typed value function."""

    value_fn: Callable[[dict[str, Any]], Any]


EVC_SENSORS: tuple[SolaxSensorEntityDescription, ...] = (
    SolaxSensorEntityDescription(
        key="deviceStatus",
        name="EVC Charging Status",
        icon="mdi:ev-station",
        # No device_class — text enum; no state_class — excluded from long-term statistics
        value_fn=lambda d: EVC_STATUS_MAP.get(d.get("deviceStatus"), "Unknown"),
    ),
    SolaxSensorEntityDescription(
        key="deviceWorkingMode",
        name="EVC Working Mode",
        icon="mdi:tune",
        # No device_class — text enum (Stop/Fast/ECO/Green)
        value_fn=lambda d: EVC_WORKING_MODE_MAP.get(d.get("deviceWorkingMode"), "Unknown"),
    ),
    SolaxSensorEntityDescription(
        key="chargingPower",
        name="EVC Charging Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,   # API delivers W for businessType=1
        icon="mdi:lightning-bolt",
        value_fn=lambda d: d.get("chargingPower"),
    ),
    SolaxSensorEntityDescription(
        key="totalChargeEnergy",
        name="EVC Total Charge Energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,  # monotonically increasing — primary Energy Dashboard sensor
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:counter",
        value_fn=lambda d: d.get("totalChargeEnergy"),
    ),
    SolaxSensorEntityDescription(
        key="chargingEnergyThisSession",
        name="EVC Session Energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,     # NOT TOTAL_INCREASING — resets to 0 at each session start
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:battery-charging",
        value_fn=lambda d: d.get("chargingEnergyThisSession"),
    ),
    SolaxSensorEntityDescription(
        key="chargingTimeThisSession",
        name="EVC Session Duration",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        icon="mdi:timer",
        value_fn=lambda d: d.get("chargingTimeThisSession"),
    ),
    SolaxSensorEntityDescription(
        key="l1Current",
        name="EVC Current L1",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        icon="mdi:current-ac",
        value_fn=lambda d: d.get("l1Current"),
    ),
    SolaxSensorEntityDescription(
        key="l2Current",
        name="EVC Current L2",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        icon="mdi:current-ac",
        value_fn=lambda d: d.get("l2Current"),
    ),
    SolaxSensorEntityDescription(
        key="l3Current",
        name="EVC Current L3",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        icon="mdi:current-ac",
        value_fn=lambda d: d.get("l3Current"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SolaxCloud API sensor entities from config entry."""
    coordinator: SolaxCoordinator = hass.data[DOMAIN][entry.entry_id]
    evc_sn = entry.data.get(CONF_EVC_SN, "unknown")

    async_add_entities(
        SolaxSensorEntity(coordinator, entry, description, evc_sn)
        for description in EVC_SENSORS
    )


class SolaxSensorEntity(CoordinatorEntity[SolaxCoordinator], SensorEntity):
    """A single SolaxCloud API sensor entity backed by the coordinator."""

    entity_description: SolaxSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolaxCoordinator,
        entry: ConfigEntry,
        description: SolaxSensorEntityDescription,
        evc_sn: str,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = _evc_device_info(DOMAIN, evc_sn)
        # Track previous session energy to detect session resets (for last_reset)
        self._last_session_energy: float | None = None
        self._session_last_reset: datetime | None = None

    @property
    def native_value(self) -> Any:
        """Return the sensor value computed from latest coordinator data."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def last_reset(self) -> datetime | None:
        """Return last reset time for TOTAL state_class sensors (session energy).

        Detects session resets when chargingEnergyThisSession drops back to a
        value significantly lower than the previous reading (new session started).
        """
        if self.entity_description.key != "chargingEnergyThisSession":
            return None
        if self.coordinator.data is None:
            return self._session_last_reset

        current = self.coordinator.data.get("chargingEnergyThisSession")
        if current is None:
            return self._session_last_reset

        # Detect session reset: current value is less than 10% of previous reading
        # (avoids false positives from small fluctuations near 0)
        if (
            self._last_session_energy is not None
            and self._last_session_energy > 0.1
            and current < self._last_session_energy * 0.1
        ):
            self._session_last_reset = dt_util.utcnow()

        self._last_session_energy = current
        return self._session_last_reset

    @property
    def available(self) -> bool:
        """Mark unavailable when coordinator has no data or last update failed."""
        return self.coordinator.last_update_success and self.coordinator.data is not None
