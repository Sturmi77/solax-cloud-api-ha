"""Constants for the SolaxCloud API integration."""

DOMAIN = "solax_cloud_api"

# API endpoints
TOKEN_URL = "https://openapi-eu.solaxcloud.com/openapi/auth/oauth/token"
DATA_URL = "https://openapi-eu.solaxcloud.com/openapi/v2/device/realtime_data"
DEVICE_LIST_URL = "https://openapi-eu.solaxcloud.com/openapi/v2/device/list"

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

# EVC status mapping
EVC_STATUS_MAP = {
    0: "Waiting",
    1: "Charging",
    2: "Finished",
    3: "Error",
}

# API success code
API_SUCCESS_CODE = 10000
