"""Charging current: API field, remembered value per mode, persistence."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from custom_components.solax_cloud_evc.const import DATA_URL, EVC_CONTROL_WORK_MODE_URL

from .conftest import realtime

TRACK = "custom_components.solax_cloud_evc.coordinator.SolaxCoordinator._async_track_command"
CURRENT = "number.solaxcloud_ev_charger_evc_charging_current"
WORK_MODE = "select.solaxcloud_ev_charger_evc_work_mode"


def _sent_payloads(aioclient_mock) -> list[dict]:
    return [kwargs for method, url, kwargs, _ in aioclient_mock.mock_calls if method == "POST"]


async def test_set_current_sends_current_field(hass, aioclient_mock, setup_integration, hass_storage):
    """The command uses the API field "current"; the value is shown and persisted."""
    # Base data: deviceWorkingMode=3 (Green, valid 3/6 A, default 6 A)
    assert hass.states.get(CURRENT).state == "6.0"
    aioclient_mock.post(EVC_CONTROL_WORK_MODE_URL, json={"code": 10000, "requestId": "r"})

    with patch(TRACK, AsyncMock()):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": CURRENT, "value": 4}, blocking=True
        )
        await hass.async_block_till_done()

    payload = _sent_payloads(aioclient_mock)[-1]
    assert payload == {
        "snList": [payload["snList"][0]],
        "workMode": 3,
        "current": 3,  # 4 A snapped to the nearest Green gear
        "businessType": 1,
    }
    assert hass.states.get(CURRENT).state == "3.0"
    stored = hass_storage[f"solax_cloud_evc.{setup_integration.entry_id}.current_gear"]
    assert stored["data"] == {"ECO": 16, "Green": 3}

    # A regular poll does not reset the value (the API does not report it)
    await hass.data["solax_cloud_evc"][setup_integration.entry_id].async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(CURRENT).state == "3.0"


async def test_mode_switch_uses_remembered_current(hass, aioclient_mock, config_entry, hass_storage):
    """Switching to ECO sends the ECO current stored by an earlier run."""
    hass_storage[f"solax_cloud_evc.{config_entry.entry_id}.current_gear"] = {
        "version": 1,
        "key": f"solax_cloud_evc.{config_entry.entry_id}.current_gear",
        "data": {"ECO": 10, "Green": 99},  # invalid Green value is ignored
    }
    aioclient_mock.get(DATA_URL, json=realtime(deviceWorkingMode=0))
    aioclient_mock.post(EVC_CONTROL_WORK_MODE_URL, json={"code": 10000, "requestId": "r"})
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data["solax_cloud_evc"][config_entry.entry_id]
    assert coordinator.current_gears == {"ECO": 10, "Green": 6}
    # Stop mode: no current setting
    assert hass.states.get(CURRENT).state == "unavailable"

    with patch(TRACK, AsyncMock()):
        await hass.services.async_call(
            "select", "select_option", {"entity_id": WORK_MODE, "option": "ECO"}, blocking=True
        )

    assert _sent_payloads(aioclient_mock)[-1]["current"] == 10
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_storage_removed_with_entry(hass, aioclient_mock, config_entry, hass_storage):
    """Removing the config entry deletes the stored charging currents."""
    aioclient_mock.get(DATA_URL, json=realtime())
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data["solax_cloud_evc"][config_entry.entry_id]
    await coordinator.async_set_current_gear("ECO", 20)
    key = f"solax_cloud_evc.{config_entry.entry_id}.current_gear"
    assert key in hass_storage

    await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
