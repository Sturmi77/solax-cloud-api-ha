"""User-facing feedback for EVC control commands that were not confirmed.

Builds the persistent notification shown when the SolaX Cloud reports a command
as failed (or never confirms it) and the realtime data does not show the requested
state either. The text names the command, the cloud's answer, the charger state at
that moment and a hint derived from that state, so the user does not have to dig
through the log to find out why a command had no effect.

Persistent notifications have no translation support, so German and English texts
live here and are picked by the configured Home Assistant language.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

from .const import COMMAND_STATUS_MAP, EVC_STATUS_MAP, EVC_WORKING_MODE_MAP

# Result fields already shown elsewhere in the notification
_KNOWN_RESULT_FIELDS = {"sn", "status"}

_TEXT: dict[str, dict[str, str]] = {
    "de": {
        "title_failed": "SolaX Wallbox: Befehl abgelehnt",
        "title_unconfirmed": "SolaX Wallbox: Befehl nicht bestätigt",
        "command": "Befehl",
        "sent": "Gesendet",
        "cloud": "Rückmeldung SolaX Cloud",
        "cloud_none": "keine Rückmeldung",
        "after": "nach {seconds} s",
        "evc_state": "Zustand der Wallbox",
        "evc_state_line": "Status {status}, Modus {mode}, Leistung {power} W (Daten von {time})",
        "evc_state_missing": "keine aktuellen Daten",
        "verify": "Gegenprüfung",
        "verify_line": "Modus nach {seconds} s weiterhin {actual} (angefordert: {expected})",
        "verify_skipped": "nicht möglich, Datenabruf fehlgeschlagen",
        "hint": "Mögliche Ursache",
        "details": "Request-ID `{request_id}` · Gerät `{sn}`",
        "hint_0": "Kein Fahrzeug angesteckt – die Wallbox nimmt Ladebefehle in der Regel erst mit angestecktem Fahrzeug an.",
        "hint_1": "Das Fahrzeug ist angesteckt, der Ladevorgang hat aber noch nicht begonnen. Den Befehl nach kurzer Zeit erneut senden.",
        "hint_3": "Der Ladevorgang ist bereits beendet (Fahrzeug voll oder Ladegrenze erreicht). Ein neuer Start wird abgelehnt, bis das Fahrzeug wieder Strom anfordert oder neu angesteckt wird.",
        "hint_4": "Die Wallbox meldet eine Störung – Details in der SolaxCloud-App.",
        "hint_5": "Die Wallbox ist derzeit nicht verfügbar.",
        "hint_7": "Das Fahrzeug pausiert das Laden (z. B. Ladegrenze oder Ladeplan im Fahrzeug).",
        "hint_9": "Die Wallbox installiert gerade ein Firmware-Update.",
        "hint_offline": "Es liegen keine aktuellen Wallbox-Daten vor – die Wallbox ist möglicherweise offline.",
        "hint_default": "Den Zustand der Wallbox in der SolaxCloud-App prüfen und den Befehl gegebenenfalls erneut senden.",
    },
    "en": {
        "title_failed": "SolaX EV charger: command rejected",
        "title_unconfirmed": "SolaX EV charger: command not confirmed",
        "command": "Command",
        "sent": "Sent",
        "cloud": "SolaX Cloud result",
        "cloud_none": "no result",
        "after": "after {seconds} s",
        "evc_state": "Charger state",
        "evc_state_line": "status {status}, mode {mode}, power {power} W (data from {time})",
        "evc_state_missing": "no current data",
        "verify": "Cross-check",
        "verify_line": "mode still {actual} after {seconds} s (requested: {expected})",
        "verify_skipped": "not possible, data poll failed",
        "hint": "Likely cause",
        "details": "Request ID `{request_id}` · device `{sn}`",
        "hint_0": "No vehicle connected – the charger usually accepts charging commands only with a vehicle plugged in.",
        "hint_1": "The vehicle is connected but charging has not started yet. Send the command again in a moment.",
        "hint_3": "The charging session has already finished (vehicle full or charge limit reached). A new start is rejected until the vehicle requests power again or is plugged in again.",
        "hint_4": "The charger reports a fault – see the SolaxCloud app for details.",
        "hint_5": "The charger is currently unavailable.",
        "hint_7": "The vehicle has paused charging (e.g. charge limit or charging schedule in the vehicle).",
        "hint_9": "The charger is installing a firmware update.",
        "hint_offline": "No current charger data – the charger may be offline.",
        "hint_default": "Check the charger in the SolaxCloud app and send the command again if needed.",
    },
}

_HINT_STATUSES = {0, 1, 3, 4, 5, 7, 9}


def _texts(language: str | None) -> dict[str, str]:
    """Return the text table for the HA language (German or English)."""
    return _TEXT["de" if (language or "").lower().startswith("de") else "en"]


def _local_time(value: datetime | str | None) -> str:
    """Format a datetime or ISO string as local HH:MM:SS (or '?')."""
    if isinstance(value, str):
        value = dt_util.parse_datetime(value)
    if value is None:
        return "?"
    return dt_util.as_local(value).strftime("%H:%M:%S")


def status_hint(evc_data: dict[str, Any] | None, language: str | None) -> str:
    """Return a short explanation for a rejected command based on the charger state."""
    text = _texts(language)
    if not evc_data:
        return text["hint_offline"]
    status = evc_data.get("deviceStatus")
    if status in _HINT_STATUSES:
        return text[f"hint_{status}"]
    return text["hint_default"]


def describe_evc_state(evc_data: dict[str, Any] | None, language: str | None) -> str:
    """One-line summary of the charger state (status, mode, power, data time)."""
    text = _texts(language)
    if not evc_data:
        return text["evc_state_missing"]
    status = evc_data.get("deviceStatus")
    mode = evc_data.get("deviceWorkingMode")
    return text["evc_state_line"].format(
        status=EVC_STATUS_MAP.get(status, f"Unknown ({status})"),
        mode=EVC_WORKING_MODE_MAP.get(mode, f"Unknown ({mode})"),
        power=evc_data.get("chargingPower", "?"),
        time=_local_time(evc_data.get("dataTime")),
    )


def describe_cloud_result(
    status: int | None,
    result: dict[str, Any] | None,
    elapsed: int,
    language: str | None,
) -> str:
    """Cloud result incl. any extra fields the API returned (e.g. a reason)."""
    text = _texts(language)
    if status is None:
        return f"{text['cloud_none']} ({text['after'].format(seconds=elapsed)})"
    name = COMMAND_STATUS_MAP.get(status, f"Unknown({status})")
    line = f"{name} (status {status}, {text['after'].format(seconds=elapsed)})"
    extra = {
        key: value
        for key, value in (result or {}).items()
        if key not in _KNOWN_RESULT_FIELDS and value not in (None, "")
    }
    if extra:
        line += " – " + ", ".join(f"{key}={value}" for key, value in extra.items())
    return line


def build_failure_notification(
    *,
    language: str | None,
    action: str,
    sent_at: datetime,
    status: int | None,
    result: dict[str, Any] | None,
    elapsed: int,
    evc_data: dict[str, Any] | None,
    verify: tuple[int, int] | None,
    verify_skipped: bool,
    verify_delay: int,
    request_id: str,
    sn: str,
) -> tuple[str, str]:
    """Return (title, message) for a command that did not take effect.

    verify is (expected, actual) work mode when a cross-check against fresh data
    ran; verify_skipped is True when a cross-check was due but the data poll failed.
    """
    text = _texts(language)
    title = text["title_failed"] if status == 4 else text["title_unconfirmed"]
    lines = [
        f"**{text['command']}:** {action}",
        f"**{text['sent']}:** {_local_time(sent_at)}",
        f"**{text['cloud']}:** {describe_cloud_result(status, result, elapsed, language)}",
        f"**{text['evc_state']}:** {describe_evc_state(evc_data, language)}",
    ]
    if verify is not None:
        expected, actual = verify
        lines.append(
            f"**{text['verify']}:** "
            + text["verify_line"].format(
                seconds=verify_delay,
                actual=EVC_WORKING_MODE_MAP.get(actual, f"Unknown ({actual})"),
                expected=EVC_WORKING_MODE_MAP.get(expected, f"Unknown ({expected})"),
            )
        )
    elif verify_skipped:
        lines.append(f"**{text['verify']}:** {text['verify_skipped']}")
    lines.append(f"**{text['hint']}:** {status_hint(evc_data, language)}")
    lines.append("")
    lines.append(text["details"].format(request_id=request_id, sn=sn))
    return title, "\n".join(lines)
