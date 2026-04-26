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

---

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **SolaxCloud API**
3. Enter your `client_id` and `client_secret`
4. The integration fetches a token, validates it, and discovers your devices automatically

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

> ⚠️ **Critical:** Every new token request invalidates the previous token immediately. The integration enforces a single `ConfigEntry` (`single_config_entry: true`) and only fetches a new token when the current one is within 1 hour of expiry.

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

## License

MIT — see [LICENSE](LICENSE)
