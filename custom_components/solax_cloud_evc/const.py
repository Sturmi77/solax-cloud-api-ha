"""Constants for the SolaxCloud API integration."""

from homeassistant.helpers.entity import DeviceInfo

DOMAIN = "solax_cloud_evc"

# API endpoints
TOKEN_URL = "https://openapi-eu.solaxcloud.com/openapi/auth/oauth/token"
DATA_URL = "https://openapi-eu.solaxcloud.com/openapi/v2/device/realtime_data"
# TODO Issue #5: DEVICE_LIST_URL will be used for inverter/battery device discovery

# Token management
TOKEN_EXPIRY_MARGIN_SECONDS = 60  # Refresh token this many seconds before expiry

# Device types
DEVICE_TYPE_EVC = 4  # EV Charger

# Config entry keys
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_EVC_SN = "evc_sn"

# Data keys returned by realtime_data API for EVC (deviceType=4)
# Phase 1: EV Charger sensors
DATA_EVC_STATUS = "evcStatus"                          # int: 0=idle,1=charging,2=fault
DATA_EVC_CHARGING_POWER = "evcChargingPower"           # float: W
DATA_EVC_CHARGED_ENERGY = "evcChargedEnergy"           # float: kWh (session)
DATA_EVC_TOTAL_CHARGED_ENERGY = "evcTotalChargedEnergy" # float: kWh (lifetime)
DATA_EVC_WORK_MODE = "evcWorkMode"                     # int: 0=stop,1=fast,2=eco,3=green
DATA_EVC_ECO_MIN_CURRENT = "evcEcoMinCurrent"          # int: A (ECO mode min)
DATA_EVC_ECO_MAX_CURRENT = "evcEcoMaxCurrent"          # int: A (ECO mode max)
DATA_EVC_GREEN_MIN_CURRENT = "evcGreenMinCurrent"      # int: A (Green mode min)
DATA_EVC_GREEN_MAX_CURRENT = "evcGreenMaxCurrent"      # int: A (Green mode max)
DATA_EVC_MAX_CURRENT = "evcMaxCurrent"                 # int: A (Fast mode / device limit)

# EVC work mode mapping
EVC_WORK_MODE_STOP = 0
EVC_WORK_MODE_FAST = 1
EVC_WORK_MODE_ECO = 2
EVC_WORK_MODE_GREEN = 3

EVC_WORK_MODE_LABELS: dict[int, str] = {
    EVC_WORK_MODE_STOP: "Stop",
    EVC_WORK_MODE_FAST: "Fast",
    EVC_WORK_MODE_ECO: "ECO",
    EVC_WORK_MODE_GREEN: "Green",
}

EVC_WORK_MODE_LABEL_TO_INT: dict[str, int] = {
    v: k for k, v in EVC_WORK_MODE_LABELS.items()
}

# EVC status mapping
EVC_STATUS_LABELS: dict[int, str] = {
    0: "Idle",
    1: "Charging",
    2: "Fault",
}

# API control endpoints
CONTROL_URL_WORK_MODE = "https://openapi-eu.solaxcloud.com/openapi/v2/device/evc/setWorkMode"
CONTROL_URL_ECO_CURRENT = "https://openapi-eu.solaxcloud.com/openapi/v2/device/evc/setEcoCurrentRange"
CONTROL_URL_GREEN_CURRENT = "https://openapi-eu.solaxcloud.com/openapi/v2/device/evc/setGreenCurrentRange"
CONTROL_URL_MAX_CURRENT = "https://openapi-eu.solaxcloud.com/openapi/v2/device/evc/setMaxCurrent"

# Developer portal URL (used in config flow description)
DEVELOPER_PORTAL_URL = "https://www.solaxcloud.com/developer"


def build_device_info(evc_sn: str) -> DeviceInfo:
    """Build a DeviceInfo object for the EV Charger."""
    return DeviceInfo(
        identifiers={(DOMAIN, evc_sn)},
        name="SolaxCloud EV Charger",
        manufacturer="Solax Power",
        model="X3-EVC-22K",
    )
