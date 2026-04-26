"""Sensor platform for SolaxCloud API — Phase 1: EV Charger sensors.

Device class / state_class rationale — see ARCHITECTURE.md §7:
  - chargingPower:           POWER  + MEASUREMENT   → instantaneous W reading
  - totalChargeEnergy:       ENERGY + TOTAL_INCREASING → monotonic kWh counter (Energy Dashboard primary)
  - chargingEnergyThisSession: ENERGY + TOTAL        → resets per session; NOT TOTAL_INCREASING
  - l1/l2/l3Current:         CURRENT + MEASUREMENT  → monitoring only
  - deviceStatus:            no device_class        → text enum, no statistics
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, EVC_STATUS_MAP
from .coordinator import SolaxCoordinator


@dataclass(frozen=True, kw_only=True)
class SolaxSensorEntityDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with a value function."""
    value_fn: Callable[[dict[str, Any]], Any]


EVC_SENSORS: tuple[SolaxSensorEntityDescription, ...] = (
    SolaxSensorEntityDescription(
        key="deviceStatus",
        name="EVC Charging Status",
        icon="mdi:ev-station",
        # No device_class — text enum; no state_class — not tracked in statistics
        value_fn=lambda d: EVC_STATUS_MAP.get(d.get("deviceStatus"), "Unknown"),
    ),
    SolaxSensorEntityDescription(
        key="chargingPower",
        name="EVC Charging Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,  # API delivers W for businessType=1
        icon="mdi:lightning-bolt",
        value_fn=lambda d: d.get("chargingPower"),
    ),
    SolaxSensorEntityDescription(
        key="totalChargeEnergy",
        name="EVC Total Charge Energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,  # monotonic counter — Energy Dashboard primary
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:counter",
        value_fn=lambda d: d.get("totalChargeEnergy"),
    ),
    SolaxSensorEntityDescription(
        key="chargingEnergyThisSession",
        name="EVC Session Energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,  # NOT TOTAL_INCREASING — resets to 0 at session start
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:battery-charging",
        value_fn=lambda d: d.get("chargingEnergyThisSession"),
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
    """Set up SolaxCloud API sensor entities."""
    coordinator: SolaxCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SolaxSensorEntity(coordinator, entry, description)
        for description in EVC_SENSORS
    )


class SolaxSensorEntity(CoordinatorEntity[SolaxCoordinator], SensorEntity):
    """Represents a single SolaxCloud API sensor entity."""

    entity_description: SolaxSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolaxCoordinator,
        entry: ConfigEntry,
        description: SolaxSensorEntityDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="SolaxCloud EV Charger",
            manufacturer="Solax Power",
            model="X3-EVC-22K",
        )

    @property
    def native_value(self) -> Any:
        """Return the sensor value from coordinator data."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
