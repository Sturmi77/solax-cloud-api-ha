# Changelog

All notable changes are documented here. Follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format.

## [Unreleased]

### Fixed
- **EVC command payload field name** — renamed `currentGear` → `current` in `set_evc_work_mode` payload to match Solax Developer Portal API docs. Wrong field name caused `code=10200` ("Operation abnormality") on all ECO and Green mode changes
- **`code=10200` misidentified as rate limit** — `_is_rate_limited_response()` no longer treats `10200` as a rate limit. Per Solax Appendix 1, `10200` means "Operation abnormality — see message field"; `10406` is the only true rate-limit code. Real `message` content is now logged verbatim so root causes are visible in HA logs
- **`hass.loop.call_later` AttributeError** — replaced with `asyncio.sleep` + `hass.async_create_task` for command result polling. `hass.loop` does not exist in modern HA
- **`setattr` on `update_interval` AttributeError** — removed `hass.async_call_later` lambda that used `setattr(self, "update_interval", ...)`. `update_interval` is a HA property, not a plain attribute. Poll-skip logic replaced with a monotonic `_postpone_poll_until` timestamp checked at the start of `_async_update_data`

### Changed
- `_is_rate_limited_response()` now only matches `code=10406` and string-based exception markers — not `10200`
- EVC command errors (non-10000, non-10406 codes) surface the actual Solax `message` field as the HA error text instead of a generic string
- `const.py` comments updated to reflect correct meaning of `10200` vs `10406`

## [1.0.0] — 2026-04-26

### Added
- Initial release — EV Charger (X3-EVC-22K) support via SolaxCloud Developer API
- 9 sensor entities: charging status, working mode, power, total energy, session energy, session duration, L1/L2/L3 current
- 3 select entities: EVC Work Mode (Stop/Fast/ECO/Green), Start Mode, Charge Scene
- 1 number entity: EVC Charging Current (A) — available in ECO and Green modes
- OAuth2 `client_credentials` token lifecycle management — auto-renews 1 hour before expiry, persisted in ConfigEntry across HA restarts
- Re-auth flow — rotate Client Secret via HA UI without reinstalling the integration
- Single config entry enforced — matches SolaxCloud's one-active-token-per-application constraint
- EVC command result polling via `/apiRequestLog/listByCondition` with persistent HA notification on failure
- Rate limit handling (`code=10406`)
- Token expiry self-healing (`code=10402`) — auto-recovers on next poll cycle without user action
- Energy Dashboard compatible — `EVC Total Charge Energy` sensor (`TOTAL_INCREASING`, kWh)
- EVC serial number entered during setup — no hardcoded defaults
- Client-side command guard (`COMMAND_MIN_INTERVAL = 6.0s`) prevents burst command calls
- Poll-skip after command: data poll is skipped for `COMMAND_MIN_INTERVAL` seconds after a command to avoid rate-limit collision

### Fixed
- Token reload loop: clearing in-memory token alone was insufficient — ConfigEntry must also be cleared on `code=10402` to prevent `_load_token_from_entry()` from resurrecting the stale token on the next cycle
- Integration reload loop: removed `add_update_listener` which fired on every `async_update_entry()` call including internal token saves, causing continuous integration reloads and token invalidation

### Known Limitations
- Phase 2 (Inverter) and Phase 3 (Battery) sensor support not yet implemented
- EU API endpoint only (`openapi-eu.solaxcloud.com`) — China IDC not yet configurable
- EVC serial number entered manually at setup — auto-discovery via Device List API planned for Phase 2
