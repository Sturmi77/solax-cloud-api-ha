"""Control commands: status mapping, result tracking, notifications, error texts."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

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


def _measured_in(minutes: float) -> str:
    """dataTime of an EVC measurement `minutes` from now (ISO, UTC)."""
    return (dt_util.utcnow() + timedelta(minutes=minutes)).isoformat()


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
    # Measured 3 min after the command — late enough to count as evidence
    finished = realtime(
        deviceStatus=3, deviceWorkingMode=0, chargingPower=0, dataTime=_measured_in(3)
    )
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
    assert "zeigen weiterhin Modus Stop (angefordert: ECO)" in message
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


async def test_late_effect_is_awaited(hass, setup_integration):
    """Old data does not count: the tracker waits until the EVC data shows ECO."""
    coordinator = _coordinator(hass, setup_integration)
    poll = AsyncMock(return_value=(4, {"sn": EVC_SN, "status": 4}))
    old = realtime(deviceWorkingMode=3)["result"][0]            # measured before the command
    later = realtime(deviceWorkingMode=2, dataTime=_measured_in(4))["result"][0]
    fetch = AsyncMock(side_effect=[old, old, old, later])
    with (
        patch.object(coordinator, "async_poll_command_result", poll),
        patch.object(coordinator, "_fetch_realtime", fetch),
        patch(SLEEP, AsyncMock(side_effect=_yield)),
        patch(NOTIFY) as notify,
    ):
        await coordinator._async_track_command(
            "req-4", "Work Mode → ECO (16 A)", {"deviceWorkingMode": 2}, dt_util.utcnow()
        )

    assert fetch.await_count == 4
    notify.async_create.assert_not_called()


async def test_no_new_data_reports_unverified(hass, setup_integration):
    """Without data measured after the command the notification says so."""
    hass.config.language = "de"
    coordinator = _coordinator(hass, setup_integration)
    poll = AsyncMock(return_value=(4, {"sn": EVC_SN, "status": 4}))
    fetch = AsyncMock(return_value=realtime(deviceWorkingMode=3)["result"][0])
    with (
        patch.object(coordinator, "async_poll_command_result", poll),
        patch.object(coordinator, "_fetch_realtime", fetch),
        patch(SLEEP, AsyncMock(side_effect=_yield)),
        patch(NOTIFY) as notify,
    ):
        await coordinator._async_track_command(
            "req-5", "Work Mode → ECO (16 A)", {"deviceWorkingMode": 2}, dt_util.utcnow()
        )

    assert fetch.await_count == 12  # one refresh per minute for 12 minutes
    message = notify.async_create.call_args[0][1]
    assert "innerhalb von 12 min keine neuen Wallbox-Daten" in message


async def test_superseded_command_stays_silent(hass, setup_integration):
    """A newer command ends the tracking of an older one without a notification."""
    coordinator = _coordinator(hass, setup_integration)
    coordinator._command_seq = 2
    poll = AsyncMock(return_value=(4, {"sn": EVC_SN, "status": 4}))
    with (
        patch.object(coordinator, "async_poll_command_result", poll),
        patch(SLEEP, AsyncMock(side_effect=_yield)),
        patch(NOTIFY) as notify,
    ):
        await coordinator._async_track_command(
            "req-6", "Work Mode → ECO (16 A)", {"deviceWorkingMode": 2}, dt_util.utcnow(), 1
        )

    notify.async_create.assert_not_called()


async def test_select_keeps_choice_until_data_settles(hass, aioclient_mock, setup_integration):
    """Updates with data measured before the command do not flip the select back."""
    coordinator = _coordinator(hass, setup_integration)
    aioclient_mock.post(EVC_CONTROL_WORK_MODE_URL, json={"code": 10000, "requestId": "r"})
    with patch(
        "custom_components.solax_cloud_evc.coordinator.SolaxCoordinator._async_track_command",
        AsyncMock(),
    ):
        await hass.services.async_call(
            "select", "select_option", {"entity_id": WORK_MODE, "option": "ECO"}, blocking=True
        )
    assert hass.states.get(WORK_MODE).state == "ECO"

    # Cached/old data (Green, measured before the command): keep ECO
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(WORK_MODE).state == "ECO"

    # Data measured well after the command still says Green: show the real mode
    coordinator.async_set_updated_data(
        realtime(deviceWorkingMode=3, dataTime=_measured_in(3))["result"][0]
    )
    await hass.async_block_till_done()
    assert hass.states.get(WORK_MODE).state == "Green"


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
