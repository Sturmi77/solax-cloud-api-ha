# Architecture — SolaxCloud API Home Assistant Integration

**Domain:** `solax_cloud_api`  
**iot_class:** `cloud_polling`  
**Target HA version:** 2024.1+

---

## 1. Background & API Discovery

Three SolaxCloud API variants were evaluated:

| API | Base URL | Verdict |
|-----|----------|---------|
| V1 (legacy) | `www.solaxcloud.com/proxyApp/proxy/api/` | ❌ Requires old token format; 24-char tokens return `token invalid!` |
| V2 | `global.solaxcloud.com/proxyApp/proxy/api/` | ❌ Returns code `2001` for end-user accounts; requires installer/distributor scope |
| **Developer Portal** | `openapi-eu.solaxcloud.com` | ✅ Works for end-users with a Developer Portal account |

The Developer Portal endpoint was discovered via JavaScript bundle analysis of `developer.solaxcloud.com` and confirmed by user-provided API documentation.

### Why YAML REST sensors fail

Four fundamental limitations of the YAML `rest` platform approach:

1. **Jinja2 not evaluated in `headers`** — HA does not render Jinja2 templates inside the `headers` block of REST sensors; a static `!secret` value is the only option
2. **Token invalidation on every fetch** — Each call to the token endpoint immediately invalidates the previous token; a YAML `rest` token sensor polling every N seconds would continuously invalidate itself
3. **No persistent token storage** — HA restarts would require a new token fetch, again invalidating any token another system might rely on
4. **Manual 30-day rotation** — Without automation, the static Bearer token in `secrets.yaml` must be updated manually every 30 days

---

## 2. Authentication Flow

```
POST https://openapi-eu.solaxcloud.com/openapi/auth/oauth/token
Content-Type: application/x-www-form-urlencoded

client_id=<id>&client_secret=<secret>&grant_type=client_credentials
```

**Response:**
```json
{
  "code": 0,
  "result": {
    "access_token": "YJW_GgioKlaEXd2pzjt0dswWgDY",
    "token_type": "bearer",
    "expires_in": 2591999,
    "scope": "...",
    "grant_type": "client_credentials"
  }
}
```

> **NOTE:** The auth endpoint uses `code=0` for success. All other endpoints (data, control, poll)
> use `code=10000`. The `access_token` is flat inside `result` (i.e. `result.access_token`),
> not nested in `result.result`.

**Critical constraints:**
- `expires_in` ≈ 30 days — no `refresh_token` returned
- **One active token per Application** — requesting a new token immediately invalidates any previously issued token for that Application
- To renew: re-fetch via `client_credentials` (not a refresh flow)

---

## 3. EVC Realtime Data Endpoint

```
GET https://openapi-eu.solaxcloud.com/openapi/v2/device/realtime_data
    ?snList=C32203J3501037&deviceType=4&businessType=1
Authorization: bearer <access_token>
```

**Response structure:**
```json
{
  "code": 10000,
  "msg": "success",
  "result": [
    {
      "deviceStatus": 1,
      "chargingPower": 11000,
      "totalChargeEnergy": 8739.8,
      "chargingEnergyThisSession": 12.3,
      "l1Current": 16.0,
      "l2Current": 16.0,
      "l3Current": 16.0,
      "dataTime": "2026-04-26 11:30:00"
    }
  ]
}
```

**Field mapping:**

| API Field | HA Entity Name | Unit | Notes |
|-----------|---------------|------|-------|
| `deviceStatus` | EVC Charging Status | — | 0=Waiting, 1=Charging, 2=Finished, 3=Error |
| `chargingPower` | EVC Charging Power | W | Instantaneous; verified in W for `businessType=1` |
| `totalChargeEnergy` | EVC Total Charge Energy | kWh | Monotonically increasing; verified value 8739.8 kWh |
| `chargingEnergyThisSession` | EVC Session Energy | kWh | Resets to 0 on new session start |
| `l1Current` | EVC Current L1 | A | Phase 1 current |
| `l2Current` | EVC Current L2 | A | Phase 2 current |
| `l3Current` | EVC Current L3 | A | Phase 3 current |

---

## 4. Known Devices (Verified)

| Device | deviceType | Serial Number | Register No | Status |
|--------|-----------|---------------|-------------|--------|
| Inverter X3 | 1 | H34A08IB718018 | SNKPY3DXVC | Online |
| Battery | 2 | 6S58AIC09AC204 | SNKPY3DXVC | Online |
| EV Charger X3-EVC-22K | 4 | C32203J3501037 | SQBY5SXCXR | Online |

---

## 5. File Structure

```
custom_components/solax_cloud_api/
├── __init__.py              # async_setup_entry, async_unload_entry
├── manifest.json            # domain, version, requirements, single_config_entry
├── const.py                 # DOMAIN, endpoint URLs, deviceType map, scan interval
├── config_flow.py           # UI setup flow + re-auth flow
├── coordinator.py           # DataUpdateCoordinator + token lifecycle
├── sensor.py                # SensorEntity subclasses, 7 EVC entities
├── strings.json             # UI strings (EN)
├── translations/
│   └── en.json              # Translated UI strings
└── devices/
    ├── evc.py               # EVC sensor definitions (deviceType=4) — Phase 1
    ├── inverter.py          # Inverter sensor definitions (deviceType=1) — Phase 2
    └── battery.py           # Battery sensor definitions (deviceType=2) — Phase 3
```

---

## 6. Core Module: `coordinator.py`

### Token Lifecycle

```
HA Start
  │
  ├─ async_setup_entry() → SolaxCoordinator.__init__()
  │    self._token = None, self._token_expires = 0.0
  │
  ├─ First _async_update_data() call
  │    └─ _ensure_token()
  │         ├─ _load_token_from_entry()   ← load from ConfigEntry (survives restart)
  │         │    Token valid?  → proceed to data fetch
  │         │    Token missing/expired? → _fetch_new_token()
  │         └─ [only when needed] _fetch_new_token()
  │              POST /openapi/auth/oauth/token
  │              Store token + expiry in ConfigEntry
  │
  ├─ Every 300s: _async_update_data()
  │    └─ _ensure_token()
  │         Token expires in > 1h? → reuse
  │         Token expires in < 1h? → _fetch_new_token()
  │
  └─ ~29 days later: token within 1h of expiry
       └─ _ensure_token() detects expiry → _fetch_new_token()
            New token stored → old token invalidated by Solax API
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Token stored in `ConfigEntry.data` | Survives HA restart without triggering a new token fetch |
| 1-hour refresh buffer | Avoids token expiry mid-polling-cycle |
| `single_config_entry: true` | Enforces one-token-per-application constraint at the HA level |
| `_ensure_token()` called before every data fetch | Guarantees token validity without a separate refresh loop |
| `_postpone_poll_until` timestamp | Prevents data poll from colliding with a command in the same burst window — replaces earlier `update_interval` manipulation which was fragile |
| `asyncio.sleep` task for command poll | Command result is polled via `asyncio.sleep` + `async_create_task` — `hass.loop.call_later` and `hass.async_call_later` are not used (incompatible with HA async model) |

### Token Invalidation Handling

#### HTTP 401
The data endpoint occasionally returns HTTP 401 when the token was invalidated externally
(e.g., a new token was fetched by another client). On 401:
- Token cleared from memory
- Token cleared from ConfigEntry
- `UpdateFailed` raised → HA waits for next 5-min interval
- `_ensure_token()` on next cycle fetches a fresh token automatically

#### code=10402 — "Request access_token authentication failed"
The data endpoint returns `code=10402` when the bearer token is rejected at the API layer
(same cause as HTTP 401 but surfaced via JSON instead of HTTP status). On 10402:
- Token cleared from memory (`self._token = None`)
- Token cleared from ConfigEntry (`CONF_ACCESS_TOKEN=None`, `CONF_TOKEN_EXPIRES=0.0`)
- `UpdateFailed` raised
- Self-heals on next poll cycle — no user action required

**Critical:** ConfigEntry must also be cleared, not just the in-memory token.
If only `self._token` is cleared, `_load_token_from_entry()` will reload the dead
token from ConfigEntry on the next cycle, `_ensure_token` will see a non-None token
with a future expiry, and skip `_fetch_new_token()` — causing an infinite 10402 loop.

#### Why no update_listener
`add_update_listener()` fires on ANY `async_update_entry()` call — including the coordinator's
internal token saves (every ~29 days and on 10402 recovery). Registering a listener
that calls `async_reload()` would cause the integration to reload on every token persistence,
triggering another token fetch, which invalidates the just-saved token, causing 10402 again.
No update_listener is registered until an options flow exists that separates options updates
from token saves.

### Implementation Sketch

```python
TOKEN_URL = "https://openapi-eu.solaxcloud.com/openapi/auth/oauth/token"
DATA_URL  = "https://openapi-eu.solaxcloud.com/openapi/v2/device/realtime_data"
TOKEN_REFRESH_BUFFER = 3600  # seconds before expiry to trigger refresh

class SolaxCoordinator(DataUpdateCoordinator):

    async def _ensure_token(self):
        """Fetch a new token only when needed."""
        if self._token is None:
            self._load_token_from_entry()  # sync — reads from ConfigEntry only
        if self._token is None or time.time() >= (self._token_expires - TOKEN_REFRESH_BUFFER):
            await self._fetch_new_token()

    async def _fetch_new_token(self):
        """Fetch token and persist to ConfigEntry immediately."""
        payload = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "grant_type": "client_credentials",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(TOKEN_URL, data=payload) as resp:
                resp.raise_for_status()
                result = await resp.json()
        self._token = result["access_token"]
        self._token_expires = time.time() + result.get("expires_in", 2591999)
        # Persist — survives HA restart
        self.hass.config_entries.async_update_entry(
            self._entry,
            data={**self._entry.data, "access_token": self._token, "token_expires": self._token_expires}
        )

    async def _async_update_data(self):
        await self._ensure_token()
        params = {"snList": EVC_SN, "deviceType": "4", "businessType": "1"}
        headers = {"Authorization": f"bearer {self._token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(DATA_URL, params=params, headers=headers) as resp:
                resp.raise_for_status()
                data = await resp.json()
        if data.get("code") != 10000:
            raise UpdateFailed(f"API error: {data.get('msg')} (code={data.get('code')})")
        return data["result"][0]
```

---

## 7. EVC Control Commands

### Endpoints

| Action | URL | Key Payload Fields |
|--------|-----|--------------------|
| Set Work Mode | `POST /openapi/v2/device/evc_control/set_evc_work_mode` | `snList`, `workMode`, `current` (ECO/Green only), `businessType` |
| Set Start Mode | `POST /openapi/v2/device/evc_control/set_evc_start_mode` | `snList`, `startMode`, `businessType` |
| Set Charge Scene | `POST /openapi/v2/device/evc_control/set_charge_scene` | `snList`, `chargerScene`, `businessType` |

### Work Mode Payload

```json
{
  "snList": ["C32203J3501037"],
  "workMode": 2,
  "current": 16,
  "businessType": 1
}
```

**workMode values:** `0=Stop`, `1=Fast`, `2=ECO`, `3=Green`

**`current` field** (⚠️ critical — often misnamed in third-party docs):
- The API field is **`"current"`** — NOT `"currentGear"`
- Only required for ECO (`workMode=2`) and Green (`workMode=3`)
- Not sent for Stop or Fast
- Valid values: ECO → `[6, 10, 16, 20, 25]` A; Green → `[3, 6]` A

### Command Delivery Architecture

Commands go through a two-phase delivery:

```
1. POST control endpoint → code=10000 + requestId
2. asyncio.sleep(COMMAND_POLL_DELAY=5s)
3. POST /openapi/apiRequestLog/listByCondition {requestId}
   → status: 1=Pending, 2=Success, 3=Delivered, 4=Failed
4. On status=4: fire persistent HA notification
```

**Why `asyncio.sleep` instead of `hass.async_call_later`:**  
`hass.loop.call_later` does not exist in modern HA. `hass.async_call_later` requires
a specific callback signature and does not integrate cleanly with coroutines.
The correct pattern is `hass.async_create_task(async_sleep_then_poll())`.

### Poll-Skip Guard

After a command is sent, `_postpone_poll_until` is set to `time.monotonic() + COMMAND_MIN_INTERVAL`.  
`_async_update_data` skips the API call and returns cached data until the timestamp has passed.  
This prevents poll + command collision in the same API burst window.

> **Previous approach (removed):** Temporarily modifying `self.update_interval` via `setattr`
> inside an `async_call_later` lambda. This failed because `update_interval` is a HA property,
> not a plain attribute — `setattr` raised `AttributeError`.

### Rate Limit Handling

| Code | Official meaning | Handling |
|------|-----------------|----------|
| `10200` | "Operation abnormality — see message field" | **Not a rate limit.** Logs the full `message` and `exception` fields verbatim, raises `HomeAssistantError` with the real Solax message |
| `10406` | "API call rate limit reached" | True rate limit — retry with exponential backoff (15s, 30s, max 3 attempts) |

> **⚠️ Common misconception:** Early versions treated `code=10200` as a rate limit based on
> observed behaviour. Per Solax Developer Portal Appendix 1, `10200` is a generic
> "Operation abnormality" code whose real cause is in the `message` field.
> `10406` is the only official rate-limit code. Treating `10200` as a rate limit and
> silently retrying masked real errors (e.g. wrong payload field names).

---

## 8. Sensor Device Classes — Critical Analysis

### Energy Dashboard Requirements

Per [HA Developer Docs — Sensor Entity](https://developers.home-assistant.io/docs/core/entity/sensor/):

| Sensor Type | Required `device_class` | Required `state_class` | Unit | Energy Dashboard |
|-------------|------------------------|----------------------|------|-----------------|
| Accumulated energy (counter) | `ENERGY` | `TOTAL_INCREASING` or `TOTAL` | kWh | ✅ primary metric |
| Instantaneous power | `POWER` | `MEASUREMENT` | W | ✅ individual device |
| Electric current | `CURRENT` | `MEASUREMENT` | A | ❌ monitoring only |
| Status / text enum | none | none | — | ❌ |

> `device_class: ENERGY` combined with `state_class: MEASUREMENT` is **invalid** and produces a HA log warning. ENERGY sensors must use `TOTAL` or `TOTAL_INCREASING`.

### Final Sensor Table

| Sensor | `device_class` | `state_class` | Unit | Energy Dashboard | Notes |
|--------|---------------|--------------|------|-----------------|-------|
| `deviceStatus` | — | — | — | ❌ | Text enum |
| `chargingPower` | `POWER` | `MEASUREMENT` | W | ✅ Individual | Direct, no Riemann sum needed |
| `totalChargeEnergy` | `ENERGY` | `TOTAL_INCREASING` | kWh | ✅ **Primary** | Main Energy Dashboard sensor |
| `chargingEnergyThisSession` | `ENERGY` | `TOTAL` | kWh | ❌ | Resets per session; use `last_reset` |
| `l1Current` | `CURRENT` | `MEASUREMENT` | A | ❌ | Monitoring / automation |
| `l2Current` | `CURRENT` | `MEASUREMENT` | A | ❌ | Monitoring / automation |
| `l3Current` | `CURRENT` | `MEASUREMENT` | A | ❌ | Monitoring / automation |

---

## 9. `config_flow.py` Design

### Initial Setup Flow

```
User: Settings → Integrations → Add → "SolaxCloud API"
  │
  ├─ async_step_user()
  │    Form: client_id (required), client_secret (required), evc_sn (required)
  │    Validate: POST token endpoint → success?
  │         ✅ create_entry(data={client_id, client_secret, access_token, token_expires, evc_sn})
  │         ❌ show error "cannot_connect"
```

### Re-auth Flow (for secret rotation)

```
Token fetch fails with 401 / invalid credentials
  │
  └─ async_initiate_reauth() triggered by coordinator
       │
       └─ async_step_reauth()
            Form: client_id (readonly), client_secret (new value)
            Validate: POST token endpoint
            ✅ update ConfigEntry, reload integration
```

---

## 10. `manifest.json`

```json
{
  "domain": "solax_cloud_api",
  "name": "SolaxCloud API",
  "version": "1.0.0",
  "documentation": "https://github.com/Sturmi77/solax-cloud-api-ha",
  "issue_tracker": "https://github.com/Sturmi77/solax-cloud-api-ha/issues",
  "requirements": ["aiohttp>=3.8.0"],
  "config_flow": true,
  "iot_class": "cloud_polling",
  "codeowners": ["@Sturmi77"],
  "single_config_entry": true
}
```

> `single_config_entry: true` prevents multiple instances of the integration — enforcing the Solax API's one-active-token-per-application constraint at the HA integration level.

---

## 11. Phase 2 / 3 Extension Path

The `coordinator.py` is designed to be device-agnostic. In Phase 2+, the coordinator will:

1. Call `/openapi/v2/device/list` during setup to discover all registered devices
2. Dynamically load sensor sets based on `deviceType` found
3. Register sensors under a shared HA Device per physical unit (inverter, battery, EVC)

```
custom_components/solax_cloud_api/
└── devices/
    ├── evc.py        # deviceType=4 — Phase 1 (current)
    ├── inverter.py   # deviceType=1 — Phase 2
    └── battery.py    # deviceType=2 — Phase 3
```

---

## 12. Known Limitations

| Item | Description | Mitigation |
|------|-------------|------------|
| One active token per Application | New token request invalidates the previous immediately | `single_config_entry`, 1h refresh buffer |
| Rate limit: 10 req/min | Maximum 10 requests per minute per account | Poll interval set to 300s (5 min); client-side `COMMAND_MIN_INTERVAL` guard |
| EU endpoint only | Only `openapi-eu.solaxcloud.com` tested | May need region configuration in Phase 2 |
| Counter reset on device replacement | `TOTAL_INCREASING` will flag anomaly | Switch to `TOTAL` + `last_reset` on replacement |
| HA offline >30 days | Token expired — cannot be refreshed | `_ensure_token()` auto-fetches new token on next HA start |
| `code=10402` self-healing | Token invalidated externally; coordinator auto-recovers on next poll (ConfigEntry cleared, fresh token fetched) | None — fully automatic |
| No update_listener | Registering would cause reload loop on token saves | Re-add in Issue #7 when options flow separates token saves from options updates |

### Diagnostics

`diagnostics.py` implements HA's native diagnostics interface. On export:
- Token is fully redacted
- EVC serial number is masked (first 3 + last 3 chars)
- Raw API response is included for debugging null/missing sensor values
- Coordinator data keys are listed to verify which fields are being polled

### Post-Command Refresh

After sending an EVC command, the integration does NOT immediately refresh coordinator data.
Calling `async_request_refresh()` directly after a command triggers a second API call within
milliseconds of the first, which can hit the SolaxCloud rate limit.
State updates arrive on the next regular poll (every 300 seconds).
