# Security Analysis — SolaxCloud API Integration

This document provides a critical analysis of credential storage, token management, and hardening recommendations for the `solax_cloud_api` Home Assistant integration.

---

## 1. Credential Storage: ConfigEntry vs. `secrets.yaml`

The integration stores `client_id`, `client_secret`, and the active `access_token` in HA's **ConfigEntry** (`/config/.storage/core.config_entries`).

### Honest Assessment

| Aspect | ConfigEntry | `secrets.yaml` |
|--------|------------|----------------|
| Protection against GitHub push | ✅ `.storage/` never in repo | ✅ with `.gitignore` |
| Protection against accidental sharing | ✅ Separate from `configuration.yaml` | ⚠️ Must be excluded manually |
| Encryption at rest on disk | ❌ Plain-text JSON | ❌ Plain-text YAML |
| Protection against physical device theft | ❌ Readable on NAS filesystem | ❌ Same |
| Protection against compromised HA process | ❌ Any HA-level access can read `.storage/` | ❌ Same |
| Automatic token renewal | ✅ Handled by coordinator | ❌ Manual every 30 days |
| HACS/UI compatible | ✅ | ❌ |

**Conclusion:** ConfigEntry is **not a secure vault**. Neither is `secrets.yaml`. Both store credentials in plain text on disk. ConfigEntry is the [recommended approach per HA Developer Docs](https://developers.home-assistant.io/docs/core/platform/application_credentials) and provides the additional benefit of automatic token lifecycle management.

> The primary security benefit of ConfigEntry over `secrets.yaml` is preventing **accidental credential exposure** (e.g., sharing your config directory or pushing to GitHub) — not protection against a determined attacker with filesystem access.

---

## 2. HA Backup Encryption (2026.4+)

Since Home Assistant 2026.4, backups use [SecureTar v3](https://www.home-assistant.io/blog/2026/03/26/modernizing-encryption-of-home-assistant-backups/) with:
- **Key derivation:** Argon2id (memory-hard)
- **Encryption:** XChaCha20-Poly1305 via libsodium (256-bit key)

This means **exported HA backups containing ConfigEntry data are strongly encrypted**. The plain-text risk is limited to the live filesystem on the host.

---

## 3. Known Risks

### R1 — Plain-text ConfigEntry on host filesystem
- **Risk:** An attacker with NAS/filesystem access can read `client_id`, `client_secret`, and `access_token` from `/config/.storage/core.config_entries`
- **Severity:** Medium (requires local system access)
- **Mitigation:** File permissions on `/config/.storage/` should be restricted to the HA process user; Synology NAS access controls should be enforced

### R2 — Token logging
- **Risk:** If `access_token` is logged at DEBUG level, it appears in HA log files which may be shared for troubleshooting
- **Severity:** Low–Medium
- **Mitigation:** Never log the full token; log only the first 4 characters

```python
# WRONG
_LOGGER.debug("Token: %s", self._token)

# CORRECT
_LOGGER.debug("Token active: %s...", self._token[:4] if self._token else "None")
```

### R3 — Token invalidation by parallel requests
- **Risk:** If a second instance somehow triggers a token fetch (e.g., during development/testing), the live token is immediately invalidated
- **Severity:** High (causes integration outage until next token fetch)
- **Mitigation:** `single_config_entry: true` in `manifest.json`; avoid manual API calls with the same `client_id`/`client_secret` while the integration is active

### R4 — `client_secret` exposure in HA logs on config flow error
- **Risk:** Error messages during config flow setup might inadvertently include credential values
- **Severity:** Low
- **Mitigation:** Catch and re-raise auth errors as generic `"cannot_connect"` without including credential values in the error message

### R5 — No `client_secret` rotation mechanism (v1.0)
- **Risk:** If `client_secret` is compromised, the integration must be removed and reinstalled
- **Severity:** Low (requires Solax Developer Portal access to obtain `client_secret`)
- **Mitigation:** Implement Re-auth Flow (see below) — planned for v1.1

---

## 4. Hardening Recommendations

### For Synology NAS / HA VM

```bash
# Restrict .storage/ to HA process user only
chmod 700 /config/.storage/
chmod 600 /config/.storage/core.config_entries
```

### For the Integration

1. **Separate Developer Portal Application per environment** — do not share `client_id`/`client_secret` between development/testing and production
2. **Never log the full token** — use the 4-character prefix pattern shown above
3. **Re-auth Flow** — allow `client_secret` rotation via HA UI without reinstalling (planned v1.1)
4. **HA external access** — use VPN or Nabu Casa rather than direct port-forwarding; this reduces the attack surface for any stored credentials

### For Development / Testing

> ⚠️ **Critical:** The Solax API issues only one active token per Application. Calling the token endpoint during testing immediately invalidates the token used by the live integration. Always use a **separate Developer Portal Application** (separate `client_id`/`client_secret`) for testing.

---

## 5. Re-auth Flow (Planned v1.1)

HA supports `async_step_reauth()` in Config Flow, enabling `client_secret` rotation via HA UI:

```python
async def async_step_reauth(self, entry_data):
    """Handle re-authentication (e.g., after client_secret rotation)."""
    return await self.async_step_reauth_confirm()

async def async_step_reauth_confirm(self, user_input=None):
    errors = {}
    if user_input:
        try:
            token, expires = await self._test_credentials(
                self._entry.data["client_id"],
                user_input["client_secret"]
            )
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={
                    **self._entry.data,
                    "client_secret": user_input["client_secret"],
                    "access_token": token,
                    "token_expires": expires,
                }
            )
            return self.async_abort(reason="reauth_successful")
        except Exception:
            errors["base"] = "invalid_auth"
    schema = vol.Schema({vol.Required("client_secret"): str})
    return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors)
```

This flow is triggered automatically by the coordinator when a token fetch returns a 401/authentication error.

---

## 6. What This Integration Does NOT Store

- Usernames or passwords (uses OAuth2 `client_credentials` — no user login involved)
- Energy data or sensor values (stored by HA's recorder, not by this integration)
- Device serial numbers in credentials (SNs are hardcoded constants, not secrets)
