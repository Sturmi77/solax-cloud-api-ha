"""Sensor platform for SolaxCloud API — Phase 1: EV Charger (X3-EVC-22K).

Device class / state_class rationale — see ARCHITECTURE.md §7:
  - charging_power: device_class=POWER (W), state_class=MEASUREMENT
  - charged_energy (session): device_class=ENERGY (kWh), state_class=MEASUREMENT
    (resets to 0 when a new charging session starts — not monotonically increasing)
  - total_charged_energy: device_class=ENERGY (kWh), state_class=TOTAL_INCREASING
  - evc_status: device_class=ENUM, no state_class
  - evc_work_mode: device_class=ENUM, no state_class
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_EVC_SN,
    DATA_EVC_CHARGED_ENERGY,
    DATA_EVC_CHARGING_POWER,
    DATA_EVC_STATUS,
    DATA_EVC_TOTAL_CHARGED_ENERGY,
    DATA_EVC_WORK_MODE,
    DOMAIN,
    EVC_STATUS_LABELS,
    EVC_WORK_MODE_LABELS,
    build_device_info,
)
from .coordinator import SolaxCloudApiCoordinator


@dataclass(frozen=True, kw_only=True)
class EvcSensorDescription(SensorEntityDescription):
    """Describes an EVC sensor entity with optional label mapping."""

    label_map: dict[int, str] | None = None


# ── Entity descriptors ──────────────────────────────────────────────────────

EVC_SENSORS: tuple[EvcSensorDescription, ...] = (
    EvcSensorDescription(
        key=DATA_EVC_STATUS,
        translation_key="evc_status",
        device_class=SensorDeviceClass.ENUM,
        options=["Idle", "Charging", "Fault"],
        label_map=EVC_STATUS_LABELS,
    ),
    EvcSensorDescription(
        key=DATA_EVC_CHARGING_POWER,
        translation_key="charging_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    EvcSensorDescription(
        key=DATA_EVC_CHARGED_ENERGY,
        translation_key="charged_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    EvcSensorDescription(
        key=DATA_EVC_TOTAL_CHARGED_ENERGY,
        translation_key="total_charged_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    EvcSensorDescription(
        key=DATA_EVC_WORK_MODE,
        translation_key="evc_work_mode",
        device_class=SensorDeviceClass.ENUM,
        options=["Stop", "Fast", "ECO", "Green"],
        label_map=EVC_WORK_MODE_LABELS,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EVC sensor entities."""
    coordinator: SolaxCloudApiCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        EvcSensor(coordinator, entry, desc) for desc in EVC_SENSORS
    )


class EvcSensor(CoordinatorEntity[SolaxCloudApiCoordinator], SensorEntity):
    """Represents a read-only EVC sensor entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolaxCloudApiCoordinator,
        entry: ConfigEntry,
        description: EvcSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description: EvcSensorDescription = description
        self._attr_unique_id = f"{entry.data[CONF_EVC_SN]}_{description.key}"
        self._attr_device_info = build_device_info(entry.data[CONF_EVC_SN])

    @property
    def native_value(self) -> Any:
        """Return the sensor value, mapping int codes to labels where applicable."""
        if self.coordinator.data is None:
            return None
        raw = self.coordinator.data.get(self.entity_description.key)
        if raw is None:
            return None
        if self.entity_description.label_map is not None:
            return self.entity_description.label_map.get(raw, str(raw))
        return raw
