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
| EV Charger (X3-EVC-22K) | `deviceType=4` | Phase 1 (available) |
| Inverter (X3) | `deviceType=1` | Phase 2 (planned) |
| Battery | `deviceType=2` | Phase 3 (planned) |

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
| EVC Charging Status | — | — | — | No (text enum) |
| EVC Charging Power | `power` | `measurement` | W | Yes — individual device |
| EVC Total Charge Energy | `energy` | `total_increasing` | kWh | Yes — **primary** |
| EVC Session Energy | `energy` | `total` | kWh | No (resets per session) |
| EVC Current L1 | `current` | `measurement` | A | No |
| EVC Current L2 | `current` | `measurement` | A | No |
| EVC Current L3 | `current` | `measurement` | A | No |
| EVC Data Time *(diagnostic)* | `timestamp` | — | — | No — time of the measurement in the cloud |
| EVC Poll Failures *(diagnostic)* | — | `measurement` | — | No — consecutive failed polls, always available |

---

## Prerequisites

- A [SolaxCloud Developer Portal](https://developer.solaxcloud.com) account
- An Application created in the Developer Portal with your EVC registered
- `client_id` and `client_secret` from the Developer Portal
- Your EV Charger's Device Serial Number (`deviceSn`) — visible in Developer Portal → Device → Device List

---

## Installation

### Via HACS (recommended)

1. Open HACS → Integrations → menu (three dots) → Custom repositories
2. Add `https://github.com/Sturmi77/solax-cloud-api-ha` as type **Integration**
3. Install **SolaxCloud EV Charger**
4. Restart Home Assistant

### Manual

1. Copy `custom_components/solax_cloud_evc/` to your HA `config/custom_components/` directory
2. Restart Home Assistant

### Supported Languages

The integration UI is available in **English** and **German**. Home Assistant automatically uses your configured language.

---

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **SolaxCloud EV Charger**
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

> **Critical:** Every new token request invalidates the previous token immediately. The integration enforces a single `ConfigEntry` (`single_config_entry: true`) and only fetches a new token when the current one is within 1 hour of expiry.

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

### Icon missing in HACS store

**Symptom:** HACS shows a gray "icon not available" placeholder for SolaxCloud EV Charger in the store or downloads list.

**Cause:** Starting with Home Assistant 2026.3, custom integrations ship brand icons locally under `custom_components/<domain>/brand/`. Home Assistant serves them through `/api/brands/integration/<domain>/icon.png`. The `home-assistant/brands` CDN no longer accepts icons for new custom integrations. HACS still resolves store icons via the legacy CDN path in some views, so inline brand assets may not appear there yet. This is a known HACS limitation — see [hacs/integration#5223](https://github.com/hacs/integration/issues/5223).

**Fix:** No action required on your side. The integration bundles the correct brand assets under `custom_components/solax_cloud_evc/brand/`. After installation on Home Assistant 2026.3 or newer, the Solax icon should appear in **Settings → Devices & Services** and during the config flow.

To verify that Home Assistant serves the bundled icon:

```bash
curl -H "Authorization: Bearer <HA_LONG_LIVED_TOKEN>" \
  http://<HA_HOST>:8123/api/brands/integration/solax_cloud_evc/icon.png
```

A `200` response with `image/png` confirms the brand assets are working in Home Assistant.

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

**Symptom:** HA shows a "Re-authentication required" notification for SolaxCloud EV Charger.

**Cause:** The `client_secret` was rotated in the Developer Portal, or the credentials
are no longer valid (`code=10400`).

**Fix:** Click the notification → enter the new `client_secret` in the re-auth form.

### Sensors show "Unavailable"

**Symptom:** All SolaxCloud sensors show "Unavailable" in HA.

**Cause:** Data polls have been failing for more than 30 minutes. Shorter outages are
bridged: transient errors (network, timeout, `10001`, `10200`, `11500`, token refresh)
are retried twice within the same poll, and if the poll still fails the last good data
is kept for up to 30 minutes.

**Fix:** Check the diagnostic sensor **EVC Poll Failures** — its attributes show
`last_error`, `last_error_time` and `last_success_time`. Rate limits (`10406`) are not
retried and recover on the next poll cycle.

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
Settings → Devices & Services → SolaxCloud EV Charger → Download Diagnostics

The export includes:
- Integration config (token masked, EVC serial number masked)
- Coordinator state (last update success, poll interval, consecutive/total failures,
  last error, last success)
- Raw API response (useful for debugging missing or unexpected sensor values)
- List of coordinator data keys

Token and serial numbers are automatically masked before export — safe to share in bug reports.

---

## License

MIT — see [LICENSE](LICENSE)
