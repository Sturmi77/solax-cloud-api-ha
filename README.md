# SolaxCloud API — Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue)](https://www.home-assistant.io)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A Home Assistant custom integration for the **SolaxCloud Developer API** (`openapi-eu.solaxcloud.com`).  
Supports EV Charger (Phase 1), with planned support for Inverter and Battery.

---

## Supported Devices

| Device | Type ID | Status |
|--------|---------|--------|
| EV Charger (X3-EVC-22K) | `deviceType=4` | ✅ Phase 1 |
| Inverter (X3) | `deviceType=1` | 🔜 Phase 2 |
| Battery | `deviceType=2` | 🔜 Phase 3 |

---

## Features

- **Automatic token management** — OAuth2 `client_credentials` flow; token persisted in HA config store, renewed 1 hour before expiry
- **No `secrets.yaml` required** — credentials entered via HA UI Config Flow and stored in HA's encrypted config store
- **Energy Dashboard compatible** — `totalChargeEnergy` sensor is directly usable as an individual device energy source
- **7 EVC sensor entities** out of the box
- **Single config entry** — enforces the Solax API's one-active-token-per-application constraint
- **Re-auth flow** — update `client_secret` via HA UI without reinstalling

---

## Sensors (EVC)

| Entity | Device Class | State Class | Unit | Energy Dashboard |
|--------|-------------|-------------|------|-----------------|
| EVC Charging Status | — | — | — | ❌ (text enum) |
| EVC Charging Power | `power` | `measurement` | W | ✅ individual device |
| EVC Total Charge Energy | `energy` | `total_increasing` | kWh | ✅ **primary** |
| EVC Session Energy | `energy` | `total` | kWh | ❌ (resets per session) |
| EVC Current L1 | `current` | `measurement` | A | ❌ |
| EVC Current L2 | `current` | `measurement` | A | ❌ |
| EVC Current L3 | `current` | `measurement` | A | ❌ |

---

## Prerequisites

- A [SolaxCloud Developer Portal](https://developer.solaxcloud.com) account
- An Application created in the Developer Portal with your EVC registered
- `client_id` and `client_secret` from the Developer Portal
- Your EV Charger's Device Serial Number (`deviceSn`) — visible in Developer Portal → Device → Device List

---

## Installation

### Via HACS (recommended)

1. Open HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/Sturmi77/solax-cloud-api-ha` as type **Integration**
3. Install **SolaxCloud API**
4. Restart Home Assistant

### Manual

1. Copy `custom_components/solax_cloud_api/` to your HA `config/custom_components/` directory
2. Restart Home Assistant

### Supported Languages

The integration UI is available in **English** and **German**. Home Assistant automatically uses your configured language.

---

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **SolaxCloud API**
3. Enter your `client_id` and `client_secret` from the Developer Portal
4. Enter your **EV Charger Serial Number** (`deviceSn`) — found in the Developer Portal under **Device → Device List**
5. The integration validates your credentials, sets up all sensor and control entities, and appears under your devices

---

## API Details

| Property | Value |
|----------|-------|
| Base URL | `https://openapi-eu.solaxcloud.com` |
| Auth endpoint | `/openapi/auth/oauth/token` |
| Auth method | `POST application/x-www-form-urlencoded` |
| Grant type | `client_credentials` |
| Token lifetime | ~30 days (2,591,999 seconds) |
| Token constraint | **One active token per Application** — new token immediately invalidates previous |
| Data endpoint | `/openapi/v2/device/realtime_data` |
| Poll interval | 300 seconds (5 minutes) |
| Auth success code | `code=0` (auth endpoint only) |
| Data success code | `code=10000` (data / control / poll endpoints) |

> ⚠️ **Critical:** Every new token request invalidates the previous token immediately. The integration enforces a single `ConfigEntry` (`single_config_entry: true`) and only fetches a new token when the current one is within 1 hour of expiry.

### API Error Codes (Developer Portal Appendix 1)

| Code | Meaning |
|------|---------|
| 10000 | Operation successful |
| 10001 | Operation failed |
| 11500 | System busy, please try again later |
| 10200 | Operation abnormality (observed as rate limit in live testing) |
| 10400 | Request not authenticated |
| 10401 | Username or password incorrect |
| 10402 | Request access_token authentication failed (token invalidated externally) |
| 10403 | Interface has no access rights |
| 10404 | Callback function not configured |
| 10405 | API call quota exhausted |
| 10406 | API call rate limit reached (official rate limit code) |
| 10500 | User has no device data permission |

> **NOTE:** The auth endpoint uses `code=0` for success. All other endpoints (data, control, poll) use `code=10000`. In live testing, `10200` is returned for rate limiting rather than the documented `10406`.

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full technical design including:
- Token lifecycle sequence diagram
- Module responsibilities
- Sensor device class rationale
- Phase 2/3 extension path

## Security

See [SECURITY.md](SECURITY.md) for a critical analysis of credential storage, known limitations, and hardening recommendations.

---

## Troubleshooting

### Integration not loading / `code=10402` errors

**Symptom:** HA logs show `SolaxCloud API error: unknown error (code=10402)` or
`Token expired/invalid (code=10402)`.

**Cause:** The SolaxCloud API allows only one active token per Application. If another
client (e.g., the SolaxCloud mobile app, or a manual API test) fetches a new token
using the same `client_id`/`client_secret`, the token stored in HA is immediately invalidated.

**Fix:** None required. The integration detects `10402`, clears the stale token, and
automatically fetches a fresh token on the next 5-minute poll cycle. If the error
persists beyond one poll cycle, check that no other client is continuously fetching tokens.

### Re-authentication required

**Symptom:** HA shows a "Re-authentication required" notification for SolaxCloud API.

**Cause:** The `client_secret` was rotated in the Developer Portal, or the credentials
are no longer valid (`code=10400`).

**Fix:** Click the notification → enter the new `client_secret` in the re-auth form.

### Sensors show "Unavailable"

**Symptom:** All SolaxCloud sensors show "Unavailable" in HA.

**Cause:** The last data fetch failed (any unhandled API error or network issue). HA marks
the integration unavailable until the next successful poll.

**Fix:** Wait 5 minutes for the next poll cycle. Check HA logs for the specific error code.

### Energy Dashboard shows wrong daily values

**Symptom:** Energy Dashboard shows unexpectedly high or low values after adding
`EVC Total Charge Energy`.

**Cause:** HA's Energy Dashboard uses the difference between the start-of-day and
end-of-day readings of the `totalChargeEnergy` lifetime counter. If the counter was
recently reset (device replacement / firmware update), HA may record a spike.

**Fix:** Long-press the energy sensor in the Energy Dashboard and use "Exclude from
statistics" for the affected period, then re-enable.

---

## Diagnostics

The integration supports Home Assistant's built-in diagnostics export.

**How to export:**
Settings → Devices & Services → SolaxCloud API → Download Diagnostics

The export includes:
- Integration config (token masked, EVC serial number masked)
- Coordinator state (last update success, poll interval)
- Raw API response (useful for debugging missing or unexpected sensor values)
- List of coordinator data keys

Token and serial numbers are automatically masked before export — safe to share in bug reports.

---

## License

MIT — see [LICENSE](LICENSE)
