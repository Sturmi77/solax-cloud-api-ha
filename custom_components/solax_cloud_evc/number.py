"""Number platform for SolaxCloud API — EVC Charging Current control.

Allows the user to set the charging current (Ampere) for ECO and Green modes.
The writable API endpoints accept integer Ampere values; HA NumberEntity is
configured with step=1 so only integers are sent.

See ARCHITECTURE.md §6 for design rationale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EVC_SN,
    CONTROL_URL_ECO_CURRENT,
    CONTROL_URL_GREEN_CURRENT,
    CONTROL_URL_MAX_CURRENT,
    DATA_EVC_ECO_MAX_CURRENT,
    DATA_EVC_ECO_MIN_CURRENT,
    DATA_EVC_GREEN_MAX_CURRENT,
    DATA_EVC_GREEN_MIN_CURRENT,
    DATA_EVC_MAX_CURRENT,
    DOMAIN,
    build_device_info,
)
from .coordinator import SolaxCloudApiCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class EvcCurrentDescription(NumberEntityDescription):
    """Describes an EVC current number entity."""

    url: str
    param_key: str
    data_key: str


# ── Entity descriptors ──────────────────────────────────────────────────────

EVC_CURRENT_NUMBERS: tuple[EvcCurrentDescription, ...] = (
    EvcCurrentDescription(
        key="eco_min_current",
        translation_key="eco_min_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=NumberDeviceClass.CURRENT,
        mode=NumberMode.BOX,
        native_min_value=6,
        native_max_value=32,
        native_step=1,
        url=CONTROL_URL_ECO_CURRENT,
        param_key="minCurrent",
        data_key=DATA_EVC_ECO_MIN_CURRENT,
    ),
    EvcCurrentDescription(
        key="eco_max_current",
        translation_key="eco_max_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=NumberDeviceClass.CURRENT,
        mode=NumberMode.BOX,
        native_min_value=6,
        native_max_value=32,
        native_step=1,
        url=CONTROL_URL_ECO_CURRENT,
        param_key="maxCurrent",
        data_key=DATA_EVC_ECO_MAX_CURRENT,
    ),
    EvcCurrentDescription(
        key="green_min_current",
        translation_key="green_min_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=NumberDeviceClass.CURRENT,
        mode=NumberMode.BOX,
        native_min_value=6,
        native_max_value=32,
        native_step=1,
        url=CONTROL_URL_GREEN_CURRENT,
        param_key="minCurrent",
        data_key=DATA_EVC_GREEN_MIN_CURRENT,
    ),
    EvcCurrentDescription(
        key="green_max_current",
        translation_key="green_max_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=NumberDeviceClass.CURRENT,
        mode=NumberMode.BOX,
        native_min_value=6,
        native_max_value=32,
        native_step=1,
        url=CONTROL_URL_GREEN_CURRENT,
        param_key="maxCurrent",
        data_key=DATA_EVC_GREEN_MAX_CURRENT,
    ),
    EvcCurrentDescription(
        key="max_current",
        translation_key="max_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=NumberDeviceClass.CURRENT,
        mode=NumberMode.BOX,
        native_min_value=6,
        native_max_value=32,
        native_step=1,
        url=CONTROL_URL_MAX_CURRENT,
        param_key="maxCurrent",
        data_key=DATA_EVC_MAX_CURRENT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EVC current number entities."""
    coordinator: SolaxCloudApiCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        EvcCurrentNumber(coordinator, entry, desc) for desc in EVC_CURRENT_NUMBERS
    )


class EvcCurrentNumber(CoordinatorEntity[SolaxCloudApiCoordinator], NumberEntity):
    """Represents a writable EVC charging current control."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolaxCloudApiCoordinator,
        entry: ConfigEntry,
        description: EvcCurrentDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description: EvcCurrentDescription = description
        self._entry = entry
        self._attr_unique_id = f"{entry.data[CONF_EVC_SN]}_{description.key}"
        self._attr_device_info = build_device_info(entry.data[CONF_EVC_SN])

    @property
    def native_value(self) -> float | None:
        """Return current value from coordinator data."""
        if self.coordinator.data is None:
            return None
        raw = self.coordinator.data.get(self.entity_description.data_key)
        return float(raw) if raw is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Send updated current value to the EVC API."""
        desc = self.entity_description
        payload = {
            "sn": self._entry.data[CONF_EVC_SN],
            desc.param_key: int(value),
        }
        headers = {
            "Authorization": f"Bearer {self._entry.data[CONF_ACCESS_TOKEN]}",
            "Content-Type": "application/json",
        }
        try:
            import aiohttp as _aiohttp
            async with _aiohttp.ClientSession() as session:
                async with session.post(
                    desc.url, json=payload, headers=headers
                ) as resp:
                    resp.raise_for_status()
                    result = await resp.json()
                    if result.get("code") != 10000:  # noqa: PLR2004
                        _LOGGER.warning(
                            "EVC set %s failed: %s", desc.key, result.get("message")
                        )
        except _aiohttp.ClientError as err:
            _LOGGER.error("EVC set %s error: %s", desc.key, err)
        else:
            await self.coordinator.async_request_refresh()
