"""Constants for the SolaxCloud API integration."""

from homeassistant.helpers.entity import DeviceInfo

DOMAIN = "solax_cloud_evc"

# API endpoints
TOKEN_URL = "https://openapi-eu.solaxcloud.com/openapi/auth/oauth/token"
DATA_URL = "https://openapi-eu.solaxcloud.com/openapi/v2/device/realtime_data"
# TODO Issue #5: DEVICE_LIST_URL will be used for inverter/battery device discovery

# Token management
TOKEN_REFRESH_BUFFER = 3600  # seconds before expiry to trigger refresh (1 hour)
DEFAULT_TOKEN_LIFETIME = 2591999  # ~30 days in seconds

# Polling
DEFAULT_SCAN_INTERVAL = 300  # 5 minutes (API rate limit: 10 req/min)

# Robustness of data polls
REQUEST_TIMEOUT = 30            # seconds — total timeout per HTTP request
DATA_RETRY_DELAYS = (10, 30)    # seconds — waits before the 2nd and 3rd attempt of a data poll
# How long after the last successful poll an entity keeps showing the last good value.
# Counters and states stay meaningful for a while; live values (power, current) must
# not freeze, so they turn unavailable much earlier.
STALE_TOLERANCE_LONG = 7200     # seconds — energy counters, status, mode, timestamps
STALE_TOLERANCE_SHORT = 900     # seconds — live values: power, currents, session duration
# API codes treated as transient in data polls (retried, then bridged with last good data)
TRANSIENT_API_CODES = frozenset({10001, 10200, 11500})

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

# EVC deviceStatus mapping — OCPP-style connector states, same numbering as the
# EVC's own Modbus register (solax_modbus plugin_solax_ev_charger). Verified live
# 2026-10-16: 2 while the car draws 8 kW, 3 right after the car reached 100 %.
# Releases up to 1.3.0 used a mapping shifted by one (2 = "Complete", 3 = "Fault").
EVC_STATUS_MAP = {
    0: "Available",             # no vehicle connected
    1: "Preparing",             # vehicle connected, charging not started yet
    2: "Charging",              # vehicle is charging
    3: "Finishing",             # session finished (vehicle full / limit reached)
    4: "Faulted",               # charger reports a fault
    5: "Unavailable",
    6: "Reserved",
    7: "Suspended EV",          # vehicle paused charging
    8: "Suspended EVSE",        # charger paused charging (e.g. no PV surplus)
    9: "Update",                # firmware update running
    10: "Card Activation",
    11: "Start Delay",
    12: "Charge Paused",
    13: "Stopping",
    14: "Occupied",
    15: "Waiting for Response",
    16: "Discharging",
    17: "Phase Switching",
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
API_RATE_LIMIT_CODE = 10200       # "Operation abnormality, see message field" — generic error.
                                  # NOT a rate limit! Logged verbatim in coordinator to reveal real cause.
                                  # Previous hypothesis (observed as rate-limit) was WRONG.
API_RATE_LIMIT_CODE_OFFICIAL = 10406  # "API call rate limit reached" — ONLY true rate-limit code per docs
API_TOKEN_EXPIRED_CODE = 10402    # "Request access_token authentication failed" — token invalidated externally

# Full error code reference (Developer Portal Appendix 1):
# 10000  Operation successful
# 10001  Operation failed
# 11500  System busy, please try again later
# 10200  Operation abnormality (see message for details)  ← generic error, NOT rate limit! Log message field.
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

# Client-side command rate-limit guard
COMMAND_MIN_INTERVAL = 6.0  # seconds — conservative guard for 10 commands/min API limit

# Command result polling
COMMAND_POLL_URL = "https://openapi-eu.solaxcloud.com/openapi/apiRequestLog/listByCondition"
COMMAND_POLL_DELAY = 5            # seconds to wait before the first result poll
# Further result polls (seconds after the previous one) while the cloud still reports
# "Pending" — the first "Failed" after 5 s turned out to be unreliable (2026-10-16:
# reported Failed although the EVC switched to ECO and started charging).
COMMAND_POLL_RETRY_DELAYS = (10, 20)
# After a negative or missing confirmation: wait this long, refresh the realtime
# data and check whether the EVC adopted the requested state anyway.
COMMAND_VERIFY_DELAY = 20
# One notification for command problems — a newer one replaces the older
COMMAND_NOTIFICATION_ID = f"{DOMAIN}_command_failed"

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

# Valid values for the "current" field per workMode (None = not applicable)
# NOTE: The API field name is "current" (not "currentGear") per Developer Portal docs.
EVC_CURRENT_GEAR_OPTIONS: dict[str, list[int] | None] = {
    "Stop":  None,
    "Fast":  None,
    "ECO":   [6, 10, 16, 20, 25],
    "Green": [3, 6],
}

# Default "current" value when switching into a mode that requires it
EVC_DEFAULT_CURRENT_GEAR: dict[str, int] = {
    "ECO":   16,
    "Green": 6,
}

# The realtime data does not report the charging current — the value last set from
# HA is kept per mode in .storage/solax_cloud_evc.<entry_id>.current_gear
CURRENT_GEAR_STORAGE_VERSION = 1


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
