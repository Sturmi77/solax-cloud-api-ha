"""Diagnostics export."""

from __future__ import annotations

from custom_components.solax_cloud_evc.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_report_token_and_poll_health(hass, setup_integration):
    """Stored token is detected and poll health is exported without the token itself."""
    diag = await async_get_config_entry_diagnostics(hass, setup_integration)

    assert diag["config_entry"]["token_present"] is True
    assert diag["config_entry"]["token_length"] == len("stored-token")
    assert diag["coordinator"]["consecutive_failures"] == 0
    assert diag["coordinator"]["last_success_time"] is not None
    assert "stored-token" not in str(diag)
