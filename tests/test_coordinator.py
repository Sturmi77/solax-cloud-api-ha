"""Poll robustness: retries, bridging, per-entity staleness, counter guard."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.solax_cloud_evc.const import (
    DATA_URL,
    DOMAIN,
    STALE_TOLERANCE_LONG,
    STALE_TOLERANCE_SHORT,
)
from custom_components.solax_cloud_evc.coordinator import _TransientPollError

from .conftest import CHARGING_POWER, DATA_TIME, POLL_FAILURES, TOTAL_ENERGY, realtime

SLEEP = "custom_components.solax_cloud_evc.coordinator.asyncio.sleep"


def _coordinator(hass, entry):
    return hass.data[DOMAIN][entry.entry_id]


async def test_setup_creates_entities(hass, setup_integration):
    """A successful first poll creates all sensors with values."""
    assert hass.states.get(TOTAL_ENERGY).state == "9990.7"
    assert hass.states.get(CHARGING_POWER).state == "7200"
    assert hass.states.get(POLL_FAILURES).state == "0"
    assert hass.states.get(DATA_TIME).state == "2026-10-14T14:44:52+00:00"


async def test_transient_errors_retried_within_cycle(hass, setup_integration):
    """Two transient failures followed by success resolve in the same cycle."""
    coordinator = _coordinator(hass, setup_integration)
    fetch = AsyncMock(
        side_effect=[_TransientPollError("a"), _TransientPollError("b"), realtime(totalChargeEnergy=9991.0)["result"][0]]
    )
    with patch.object(coordinator, "_fetch_realtime", fetch), patch(SLEEP, AsyncMock()):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert fetch.await_count == 3
    assert coordinator.consecutive_failures == 0
    assert hass.states.get(TOTAL_ENERGY).state == "9991.0"


async def test_non_transient_error_not_retried(hass, setup_integration):
    """Rate limits and other API errors are not retried."""
    coordinator = _coordinator(hass, setup_integration)
    fetch = AsyncMock(side_effect=UpdateFailed("rate limit"))
    with patch.object(coordinator, "_fetch_realtime", fetch), patch(SLEEP, AsyncMock()):
        await coordinator.async_refresh()

    assert fetch.await_count == 1
    assert coordinator.consecutive_failures == 1


async def test_bridging_and_per_entity_staleness(hass, setup_integration, freezer):
    """Live values go unavailable after the short tolerance, counters after the long one."""
    coordinator = _coordinator(hass, setup_integration)
    failing = AsyncMock(side_effect=_TransientPollError("cloud down"))

    with patch.object(coordinator, "_fetch_realtime", failing), patch(SLEEP, AsyncMock()):
        # Within the short tolerance: everything bridged
        freezer.tick(timedelta(seconds=STALE_TOLERANCE_SHORT - 60))
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(CHARGING_POWER).state == "7200"
        assert hass.states.get(TOTAL_ENERGY).state == "9990.7"
        failures = hass.states.get(POLL_FAILURES)
        assert failures.state == "1"
        assert "cloud down" in failures.attributes["last_error"]

        # Past the short tolerance: power unavailable, counter still bridged
        freezer.tick(timedelta(seconds=120))
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(CHARGING_POWER).state == STATE_UNAVAILABLE
        assert hass.states.get(TOTAL_ENERGY).state == "9990.7"

        # Past the long tolerance: update fails, all data entities unavailable
        freezer.tick(timedelta(seconds=STALE_TOLERANCE_LONG))
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert not coordinator.last_update_success
        assert hass.states.get(TOTAL_ENERGY).state == STATE_UNAVAILABLE
        # The health sensor stays available and keeps counting
        assert hass.states.get(POLL_FAILURES).state == "3"

    # Recovery resets the counter and restores all entities
    with patch.object(coordinator, "_fetch_realtime", AsyncMock(return_value=realtime()["result"][0])):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert hass.states.get(POLL_FAILURES).state == "0"
    assert hass.states.get(CHARGING_POWER).state == "7200"


@pytest.mark.parametrize("bad_value", [0, None, 9990.0])
async def test_total_energy_guard(hass, setup_integration, bad_value):
    """0, missing or decreasing lifetime energy keeps the previous reading."""
    coordinator = _coordinator(hass, setup_integration)
    fetch = AsyncMock(return_value=realtime(totalChargeEnergy=bad_value)["result"][0])
    with patch.object(coordinator, "_fetch_realtime", fetch):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert hass.states.get(TOTAL_ENERGY).state == "9990.7"


@pytest.mark.parametrize(
    ("response", "status", "transient"),
    [
        ({"code": 10200, "message": "Operation abnormality"}, 200, True),
        ({"code": 11500, "message": "System busy"}, 200, True),
        ({"code": 10000, "result": []}, 200, True),
        ({"code": 10406, "message": "rate limit"}, 200, False),
        ({"code": 10500, "message": "no permission"}, 200, False),
        ({}, 503, True),
        ({}, 401, True),
    ],
)
async def test_fetch_classifies_errors(
    hass, aioclient_mock, setup_integration, response, status, transient
):
    """API responses are classified as transient (retried) or final."""
    coordinator = _coordinator(hass, setup_integration)
    aioclient_mock.clear_requests()
    aioclient_mock.get(DATA_URL, json=response, status=status)

    with pytest.raises(UpdateFailed) as err:
        await coordinator._fetch_realtime()
    assert isinstance(err.value, _TransientPollError) is transient
    if status == 401:
        assert coordinator._token is None
