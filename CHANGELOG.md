# Changelog

All notable changes are documented here. Follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format.

## [Unreleased]

### Fixed
- **EVC Charging Status showed wrong states** — the `deviceStatus` mapping was shifted by one: an active charging session was shown as "Complete" and a finished session (vehicle full) as "Fault". The sensor now uses the OCPP-style states of the EVC (Available, Preparing, Charging, Finishing, Faulted, Unavailable, Suspended EV, Suspended EVSE, Update, …). **Breaking for automations** that compare against the old texts "Waiting", "Complete", "Fault" or "Stop"
- **False "Command Failed" notifications** — the SolaX Cloud sometimes reports a command as failed although the charger executed it. Work-mode commands are now cross-checked against fresh realtime data before anything is reported; a pending result is polled up to three times (after 5, 15 and 35 s)

### Changed
- **Command notifications explain what happened** — one notification (a newer one replaces the older, a later successful command removes it) in German or English with the command, time sent, the cloud's answer, the charger state (status, mode, power, data time), the cross-check result and a likely cause, e.g. "charging session already finished (vehicle full)"
- **Command errors name the command and the cause** — errors shown when a command cannot be sent (cooldown, network, token, rate limit, rejection by the cloud) are translated (de/en), name the command (e.g. "Work Mode → ECO (16 A)") and, for rejections, include the API message, code and a hint from the charger state; the log contains payload, charger state and full API response

## [1.3.0] — 2026-10-16

### Changed
- **Per-entity staleness** — after a failed poll, live values (Charging Power, Current L1–L3, Session Duration) stay available for 15 minutes only, so a frozen power value is never shown for long; energy counters, status, mode and selects are bridged for up to 2 hours (`STALE_TOLERANCE_SHORT` / `STALE_TOLERANCE_LONG`, replaces the single 30-minute `STALE_TOLERANCE`)
- HTTP requests use an integration-owned aiohttp session (needed for the DNS fallback resolver) instead of Home Assistant's shared session; it is closed on unload and shutdown

### Added
- **DNS fallback** — when `openapi-eu.solaxcloud.com` cannot be resolved (e.g. "DNS server returned answer with no data"), requests go to the last successfully resolved IP addresses instead of failing. TLS SNI, certificate check and `Host` header still use the hostname. Logged once per outage as a warning, recovery as info (`dns_fallback.py`)
- Test suite based on `pytest-homeassistant-custom-component` (`tests/`, `requirements_test.txt`) covering setup, retries, bridging, per-entity staleness, the energy counter guard, error classification, diagnostics and the DNS fallback (simulated DNS outage against a local TLS server)
- GitHub workflow `Tests` running the suite on push and pull requests

## [1.2.0] — 2026-10-14

### Added
- Diagnostic sensor **EVC Poll Failures** — consecutive failed data polls, always available; attributes `last_error`, `last_error_time`, `last_success_time`, `total_failures`
- Diagnostic sensor **EVC Data Time** — timestamp of the measurement in the cloud (`dataTime`)
- Diagnostics export includes poll health (consecutive/total failures, last error, last success)

### Changed
- **Data polls are retried** on transient errors (network errors, timeouts, HTTP 5xx, `10001`, `10200`, `11500`, empty result, token refresh after `401`/`10402`) — up to 3 attempts per cycle (after 10 s and 30 s). Rate limits (`10406`) are not retried
- **Short outages no longer make entities unavailable** — after a failed poll the last good data is served for up to 30 minutes (`STALE_TOLERANCE`) after the last successful poll *(refined in a later release, see above)*
- All HTTP requests use Home Assistant's shared aiohttp session with a 30 s timeout instead of a new session without timeout per request

### Fixed
- `totalChargeEnergy` values of 0, missing or lower than the previous reading are ignored — prevents false meter resets in `TOTAL_INCREASING` statistics
- Diagnostics reported `token_present: false` although a token was stored (looked up key `token` instead of `access_token`)

## [1.1.0] — 2026-06-26

### Changed
- **BREAKING — HA domain renamed `solax_cloud_api` → `solax_cloud_evc`** to resolve HACS catalog conflict with the existing `solax_cloud_api` integration (HACS PR #7282 review feedback). Users upgrading from v1.0.x must remove and re-add the integration via Settings → Devices & Services. The new domain better reflects the integration's scope (EV Charger).
- HACS / manifest display name changed from `SolaxCloud API` to `SolaxCloud EV Charger`
- `custom_components/solax_cloud_api/` folder renamed to `custom_components/solax_cloud_evc/`

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
