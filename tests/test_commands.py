"""Control commands: status mapping, result tracking, notifications, error texts."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.solax_cloud_evc.const import (
    COMMAND_NOTIFICATION_ID,
    COMMAND_POLL_URL,
    DATA_URL,
    DOMAIN,
    EVC_CONTROL_WORK_MODE_URL,
)

from .conftest import EVC_SN, realtime

SLEEP = "custom_components.solax_cloud_evc.coordinator.asyncio.sleep"
COOLDOWN = "custom_components.solax_cloud_evc.coordinator.COMMAND_MIN_INTERVAL"
NOTIFY = "custom_components.solax_cloud_evc.coordinator.persistent_notification"
_real_sleep = asyncio.sleep


async def _yield(*_args):
    """Stand-in for the delays: yield once instead of waiting."""
    await _real_sleep(0)


STATUS = "sensor.solaxcloud_ev_charger_evc_charging_status"
WORK_MODE = "select.solaxcloud_ev_charger_evc_work_mode"


def _coordinator(hass, entry):
    return hass.data[DOMAIN][entry.entry_id]


async def _select_eco(hass, coordinator, aioclient_mock, data_after):
    """Select ECO and run the background result tracking to completion.

    data_after is what the cross-check refresh sees. Sleeps are mocked, so the
    poll cooldown after a command is disabled (it expires long before in reality).
    """
    aioclient_mock.clear_requests()
    aioclient_mock.get(DATA_URL, json=data_after)
    aioclient_mock.post(EVC_CONTROL_WORK_MODE_URL, json={"code": 10000, "requestId": "req-1"})
    with patch(COOLDOWN, 0):
        await hass.services.async_call(
            "select", "select_option", {"entity_id": WORK_MODE, "option": "ECO"}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, "Available"), (1, "Preparing"), (2, "Charging"), (3, "Finishing"), (4, "Faulted"), (42, "Unknown")],
)
async def test_status_mapping(hass, aioclient_mock, config_entry, raw, expected):
    """deviceStatus follows the OCPP numbering (2 = charging, 3 = finished)."""
    aioclient_mock.get(DATA_URL, json=realtime(deviceStatus=raw))
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(STATUS).state == expected


async def test_failed_but_executed_no_notification(hass, aioclient_mock, setup_integration, caplog):
    """Cloud says Failed, but the EVC shows the requested mode: warning only."""
    coordinator = _coordinator(hass, setup_integration)
    poll = AsyncMock(return_value=(4, {"sn": EVC_SN, "status": 4}))
    with (
        patch.object(coordinator, "async_poll_command_result", poll),
        patch(SLEEP, AsyncMock(side_effect=_yield)),
        patch(NOTIFY) as notify,
    ):
        await _select_eco(hass, coordinator, aioclient_mock, realtime(deviceWorkingMode=2))

    assert poll.await_count == 1  # Failed is final, no further result polls
    notify.async_create.assert_not_called()
    notify.async_dismiss.assert_called_once_with(hass, COMMAND_NOTIFICATION_ID)
    assert "treating it as executed" in caplog.text


async def test_failed_notification_explains(hass, aioclient_mock, setup_integration, caplog):
    """Cloud says Failed and the mode did not change: one German notification with details."""
    hass.config.language = "de"
    coordinator = _coordinator(hass, setup_integration)
    poll = AsyncMock(return_value=(4, {"sn": EVC_SN, "status": 4, "remark": "busy"}))
    finished = realtime(deviceStatus=3, deviceWorkingMode=0, chargingPower=0)
    with (
        patch.object(coordinator, "async_poll_command_result", poll),
        patch(SLEEP, AsyncMock(side_effect=_yield)),
        patch(NOTIFY) as notify,
    ):
        await _select_eco(hass, coordinator, aioclient_mock, finished)

    notify.async_create.assert_called_once()
    args, kwargs = notify.async_create.call_args
    message = args[1]
    assert kwargs["title"] == "SolaX Wallbox: Befehl abgelehnt"
    assert kwargs["notification_id"] == COMMAND_NOTIFICATION_ID
    assert "Work Mode → ECO (16 A)" in message
    assert "Failed (status 4, nach 5 s) – remark=busy" in message
    assert "Status Finishing, Modus Stop, Leistung 0 W" in message
    assert "weiterhin Stop (angefordert: ECO)" in message
    assert "Ladevorgang ist bereits beendet" in message
    assert "req-1" in message and EVC_SN in message
    assert "did not take effect" in caplog.text
    # The select shows the real mode again after the cross-check refresh
    assert hass.states.get(WORK_MODE).state == "Stop"


async def test_pending_then_success(hass, aioclient_mock, setup_integration):
    """Pending results are polled again; a later success clears old notifications."""
    coordinator = _coordinator(hass, setup_integration)
    poll = AsyncMock(side_effect=[(1, {}), None, (2, {})])
    with (
        patch.object(coordinator, "async_poll_command_result", poll),
        patch(SLEEP, AsyncMock(side_effect=_yield)),
        patch(NOTIFY) as notify,
    ):
        await _select_eco(hass, coordinator, aioclient_mock, realtime(deviceWorkingMode=2))

    assert poll.await_count == 3
    notify.async_create.assert_not_called()
    notify.async_dismiss.assert_called_once_with(hass, COMMAND_NOTIFICATION_ID)


async def test_unconfirmed_without_expected_state(hass, setup_integration):
    """Commands without a checkable state notify when never confirmed."""
    coordinator = _coordinator(hass, setup_integration)
    poll = AsyncMock(return_value=(1, {"sn": EVC_SN, "status": 1}))
    with (
        patch.object(coordinator, "async_poll_command_result", poll),
        patch(SLEEP, AsyncMock(side_effect=_yield)),
        patch(NOTIFY) as notify,
    ):
        await coordinator._async_track_command("req-2", "Start Mode → Swipe Card", None, None)

    assert poll.await_count == 3
    _, kwargs = notify.async_create.call_args
    assert kwargs["title"] == "SolaX EV charger: command not confirmed"


async def test_poll_command_result_parses_response(hass, aioclient_mock, setup_integration):
    """The result poll picks this EVC's entry and returns its status and fields."""
    coordinator = _coordinator(hass, setup_integration)
    aioclient_mock.post(
        COMMAND_POLL_URL,
        json={"code": 10000, "result": [{"sn": "other", "status": 2}, {"sn": EVC_SN, "status": 4}]},
    )
    assert await coordinator.async_poll_command_result("req-3") == (4, {"sn": EVC_SN, "status": 4})


async def test_rejected_command_error_text(hass, aioclient_mock, setup_integration):
    """An API rejection raises a translated error naming the command, code and a hint."""
    coordinator = _coordinator(hass, setup_integration)
    aioclient_mock.post(
        EVC_CONTROL_WORK_MODE_URL,
        json={"code": 10200, "message": "Device status abnormal", "exception": "x"},
    )
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "select", "select_option", {"entity_id": WORK_MODE, "option": "ECO"}, blocking=True
        )

    assert err.value.translation_key == "command_rejected"
    placeholders = err.value.translation_placeholders
    assert placeholders["action"] == "Work Mode → ECO (16 A)"
    assert placeholders["code"] == "10200"
    assert placeholders["message"] == "Device status abnormal"
    assert str(err.value).startswith(
        "Work Mode → ECO (16 A) rejected by SolaX Cloud: Device status abnormal (code 10200)."
    )
    # Hint derived from the last known EVC state (deviceStatus=1 → Preparing)
    assert placeholders["hint"].startswith("The vehicle is connected")
    assert coordinator.data["deviceStatus"] == 1
