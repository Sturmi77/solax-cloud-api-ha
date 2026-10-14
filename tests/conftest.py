"""Shared fixtures for the SolaxCloud EV Charger tests."""

from __future__ import annotations

import time
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solax_cloud_evc.const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_EVC_SN,
    CONF_TOKEN_EXPIRES,
    DATA_URL,
    DOMAIN,
)

pytest_plugins = "pytest_homeassistant_custom_component"

EVC_SN = "C32203J3501099"
TOTAL_ENERGY = "sensor.solaxcloud_ev_charger_evc_total_charge_energy"
CHARGING_POWER = "sensor.solaxcloud_ev_charger_evc_charging_power"
POLL_FAILURES = "sensor.solaxcloud_ev_charger_evc_poll_failures"
DATA_TIME = "sensor.solaxcloud_ev_charger_evc_data_time"


def realtime(**overrides: Any) -> dict[str, Any]:
    """Return a successful realtime_data API response."""
    device = {
        "deviceSn": EVC_SN,
        "deviceStatus": 1,
        "deviceWorkingMode": 3,
        "chargingPower": 7200,
        "totalChargeEnergy": 9990.7,
        "chargingEnergyThisSession": 4.2,
        "chargingTimeThisSession": 2100,
        "l1Current": 10.4,
        "l2Current": 10.5,
        "l3Current": 10.3,
        "dataTime": "2026-10-14T14:44:52.000+00:00",
    }
    device.update(overrides)
    return {"code": 10000, "result": [device]}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the integration from custom_components/."""
    return


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Config entry with a stored, still valid token (no token request needed)."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="SolaxCloud EV Charger",
        unique_id="client-id",
        data={
            CONF_CLIENT_ID: "client-id",
            CONF_CLIENT_SECRET: "client-secret",
            CONF_EVC_SN: EVC_SN,
            CONF_ACCESS_TOKEN: "stored-token",
            CONF_TOKEN_EXPIRES: time.time() + 30 * 86400,
        },
    )


@pytest.fixture
async def setup_integration(hass, aioclient_mock, config_entry):
    """Set up the integration with one successful poll; unload afterwards."""
    aioclient_mock.get(DATA_URL, json=realtime())
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    yield config_entry
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
