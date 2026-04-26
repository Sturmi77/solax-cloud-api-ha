# Changelog

All notable changes are documented here. Follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format.

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
- Rate limit handling (`code=10200` / `code=10406`)
- Token expiry self-healing (`code=10402`) — auto-recovers on next poll cycle without user action
- Energy Dashboard compatible — `EVC Total Charge Energy` sensor (`TOTAL_INCREASING`, kWh)
- EVC serial number entered during setup — no hardcoded defaults

### Fixed
- Token reload loop: clearing in-memory token alone was insufficient — ConfigEntry must also be cleared on `code=10402` to prevent `_load_token_from_entry()` from resurrecting the stale token on the next cycle
- Integration reload loop: removed `add_update_listener` which fired on every `async_update_entry()` call including internal token saves, causing continuous integration reloads and token invalidation

### Known Limitations
- Phase 2 (Inverter) and Phase 3 (Battery) sensor support not yet implemented
- EU API endpoint only (`openapi-eu.solaxcloud.com`) — China IDC not yet configurable
- EVC serial number entered manually at setup — auto-discovery via Device List API planned for Phase 2
