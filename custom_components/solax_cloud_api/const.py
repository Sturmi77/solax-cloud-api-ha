"""Constants for the SolaxCloud API integration."""

from homeassistant.helpers.entity import DeviceInfo

DOMAIN = "solax_cloud_api"

# API endpoints
TOKEN_URL = "https://openapi-eu.solaxcloud.com/openapi/auth/oauth/token"
DATA_URL = "https://openapi-eu.solaxcloud.com/openapi/v2/device/realtime_data"
# TODO Issue #5: DEVICE_LIST_URL will be used for inverter/battery device discovery

# Token management
TOKEN_REFRESH_BUFFER = 3600  # seconds before expiry to trigger refresh (1 hour)
DEFAULT_TOKEN_LIFETIME = 2591999  # ~30 days in seconds

# Polling
DEFAULT_SCAN_INTERVAL = 300  # 5 minutes (API rate limit: 10 req/min)

# ConfigEntry keys
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_ACCESS_TOKEN = "access_token"
CONF_TOKEN_EXPIRES = "token_expires"
CONF_EVC_SN = "evc_sn"

# Device types
DEVICE_TYPE_INVERTER = 1
DEVICE_TYPE_BATTERY = 2
DEVICE_TYPE_EVC = 4

# EVC deviceStatus mapping (from X3-EVC User Manual + Developer API docs)
EVC_STATUS_MAP = {
    0: "Waiting",      # Idle — no EV connected or ready
    1: "Charging",     # EV actively charging
    2: "Complete",     # Charging finished (EV full or target reached)
    3: "Fault",        # Error state — fault light on
    4: "Unavailable",  # Remote updating / not available
    5: "Stop",         # EV connected but not charging (manually stopped)
}

# EVC deviceWorkingMode mapping (charging mode)
EVC_WORKING_MODE_MAP = {
    0: "Stop",
    1: "Fast",
    2: "ECO",
    3: "Green",
}

# API response codes — official meanings from Developer Portal Appendix 1
# Auth endpoint uses code=0 for success; all other endpoints use code=10000
API_SUCCESS_CODE = 10000          # data / control / poll endpoints — "Operation successful"
API_AUTH_SUCCESS_CODE = 0         # auth endpoint only
API_RATE_LIMIT_CODE = 10200       # "Operation abnormality" — observed as rate limit in live testing
                                  # NOTE: Official docs list 10406 as the rate limit code.
                                  # In practice, 10200 is what the API returns when rate-limited.
API_TOKEN_EXPIRED_CODE = 10402    # "Request access_token authentication failed" — token invalidated externally

# Full error code reference (Developer Portal Appendix 1):
# 10000  Operation successful
# 10001  Operation failed
# 11500  System busy, please try again later
# 10200  Operation abnormality (see message for details)  ← observed as rate limit in live testing
# 10400  Request not authenticated
# 10401  Username or password incorrect
# 10402  Request access_token authentication failed       ← token invalidated externally
# 10403  Interface has no access rights
# 10404  Callback function not configured
# 10405  API call quota exhausted
# 10406  API call rate limit reached                      ← official rate limit code
# 10500  User has no device data permission

# Business types
BUSINESS_TYPE_RESIDENTIAL = 1     # businessType=1 per Developer Portal — used for all EVC requests

# Command result polling
COMMAND_POLL_URL = "https://openapi-eu.solaxcloud.com/openapi/apiRequestLog/listByCondition"
COMMAND_POLL_DELAY = 5            # seconds to wait before polling

# Command delivery status codes (Appendix 8)
COMMAND_STATUS_MAP: dict[int, str] = {
    1: "Pending",
    2: "Success",
    3: "Delivered",
    4: "Failed",
}

# EVC Control endpoints
EVC_CONTROL_BASE_URL = "https://openapi-eu.solaxcloud.com/openapi/v2/device/evc_control"
EVC_CONTROL_WORK_MODE_URL  = f"{EVC_CONTROL_BASE_URL}/set_evc_work_mode"
EVC_CONTROL_START_MODE_URL = f"{EVC_CONTROL_BASE_URL}/set_evc_start_mode"
EVC_CONTROL_SCENE_URL      = f"{EVC_CONTROL_BASE_URL}/set_charge_scene"

# EVC Start Mode
EVC_START_MODE_TO_INT: dict[str, int] = {
    "Plug & Charge": 0,
    "Swipe Card":    1,
    "APP":           2,
}

# EVC Charge Scene
EVC_CHARGE_SCENE_TO_INT: dict[str, int] = {
    "Home":     0,
    "OCPP":     1,
    "Standard": 2,
}

# Work mode: int → API value (same as EVC_WORKING_MODE_MAP keys)
EVC_WORK_MODE_TO_INT: dict[str, int] = {
    "Stop": 0,
    "Fast": 1,
    "ECO": 2,
    "Green": 3,
}

# Valid currentGear values per workMode (None = not applicable)
EVC_CURRENT_GEAR_OPTIONS: dict[str, list[int] | None] = {
    "Stop":  None,
    "Fast":  None,
    "ECO":   [6, 10, 16, 20, 25],
    "Green": [3, 6],
}

# Default currentGear when switching into a mode that requires it
EVC_DEFAULT_CURRENT_GEAR: dict[str, int] = {
    "ECO":   16,
    "Green": 6,
}


def _evc_device_info(domain: str, evc_sn: str) -> DeviceInfo:
    """Return shared DeviceInfo for the EVC entity."""
    return DeviceInfo(
        identifiers={(domain, evc_sn)},
        name="SolaxCloud EV Charger",
        manufacturer="Solax Power",
        model="X3-EVC-22K",
        serial_number=evc_sn,
        configuration_url="https://developer.solaxcloud.com",
    )
