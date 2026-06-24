"""Select platform for SolaxCloud API — EVC control entities.

Entities:
  1. EvcWorkModeSelect    — Stop / Fast / ECO / Green
     API: POST /openapi/v2/device/evc/setWorkMode
     Payload: {"sn": "<evc_sn>", "workMode": <int>}

  2. EvcMaxCurrentSelect  — 6A … 32A (Fast mode charging current)
     API: POST /openapi/v2/device/evc/setMaxCurrent
     Payload: {"sn": "<evc_sn>", "maxCurrent": <int>}

See ARCHITECTURE.md §6 for optimistic vs. coordinator-refresh strategy.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EVC_SN,
    CONTROL_URL_MAX_CURRENT,
    CONTROL_URL_WORK_MODE,
    DATA_EVC_MAX_CURRENT,
    DATA_EVC_WORK_MODE,
    DOMAIN,
    EVC_WORK_MODE_LABEL_TO_INT,
    EVC_WORK_MODE_LABELS,
    build_device_info,
)
from .coordinator import SolaxCloudApiCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EVC select entities."""
    coordinator: SolaxCloudApiCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            EvcWorkModeSelect(coordinator, entry),
            EvcMaxCurrentSelect(coordinator, entry),
        ]
    )


# ── Base helper — shared POST logic ─────────────────────────────────────────


class _EvcSelectBase(CoordinatorEntity[SolaxCloudApiCoordinator], SelectEntity):
    """Base class with shared POST-to-API logic."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolaxCloudApiCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = build_device_info(entry.data[CONF_EVC_SN])

    async def _post_command(
        self,
        url: str,
        payload: dict[str, Any],
    ) -> bool:
        """POST a control command to the EVC API.

        Returns True on success, False on API or network error.
        Token is always read from the entry at call time to handle token refresh.
        """
        headers = {
            "Authorization": f"Bearer {self._entry.data[CONF_ACCESS_TOKEN]}",
            "Content-Type": "application/json",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    resp.raise_for_status()
                    result = await resp.json()
                    api_code = result.get("code")
                    if api_code != 10000:  # noqa: PLR2004
                        _LOGGER.warning(
                            "EVC control command failed: code=%s msg=%s",
                            api_code,
                            result.get("message"),
                        )
                        return False
        except aiohttp.ClientError as err:
            _LOGGER.error("EVC control command network error: %s", err)
            return False
        return True


# ── Work mode select ——————————————————————————————————————————


class EvcWorkModeSelect(_EvcSelectBase):
    """Select entity for EVC work mode: Stop / Fast / ECO / Green."""

    _attr_translation_key = "evc_work_mode"
    _attr_options = list(EVC_WORK_MODE_LABELS.values())  # ["Stop", "Fast", "ECO", "Green"]

    def __init__(
        self,
        coordinator: SolaxCloudApiCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_EVC_SN]}_work_mode"

    @property
    def current_option(self) -> str | None:
        """Return current work mode label."""
        if self.coordinator.data is None:
            return None
        raw = self.coordinator.data.get(DATA_EVC_WORK_MODE)
        return EVC_WORK_MODE_LABELS.get(raw) if raw is not None else None

    async def async_select_option(self, option: str) -> None:
        """Send work mode command to EVC."""
        mode_int = EVC_WORK_MODE_LABEL_TO_INT.get(option)
        if mode_int is None:
            _LOGGER.error("Unknown work mode option: %s", option)
            return

        payload = {
            "sn": self._entry.data[CONF_EVC_SN],
            "workMode": mode_int,
        }
        if await self._post_command(CONTROL_URL_WORK_MODE, payload):
            await self.coordinator.async_request_refresh()


# ── Max current select —————————————————————————————————————————


class EvcMaxCurrentSelect(_EvcSelectBase):
    """Select entity for EVC max charging current: 6A … 32A (Fast mode)."""

    _attr_translation_key = "evc_max_current"
    _attr_options = [str(a) for a in range(6, 33)]  # "6" .. "32"

    def __init__(
        self,
        coordinator: SolaxCloudApiCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_EVC_SN]}_max_current"

    @property
    def current_option(self) -> str | None:
        """Return current max current as string label."""
        if self.coordinator.data is None:
            return None
        raw = self.coordinator.data.get(DATA_EVC_MAX_CURRENT)
        return str(raw) if raw is not None else None

    async def async_select_option(self, option: str) -> None:
        """Send max current command to EVC."""
        payload = {
            "sn": self._entry.data[CONF_EVC_SN],
            "maxCurrent": int(option),
        }
        if await self._post_command(CONTROL_URL_MAX_CURRENT, payload):
            await self.coordinator.async_request_refresh()
