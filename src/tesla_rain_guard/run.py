"""One guard round: no-wake read each car, fetch weather at its parked
location, let the decision engine decide, log it, and either preview or
execute. Used by the `check` CLI command (run it on a cron schedule) and
by the simulator in fake mode."""
from __future__ import annotations

import datetime as dt

from . import decision_log
from .fleet import snapshot_fleet
from .guard import CLOSE, NOTHING, VENT, CarGuardState, decide
from .weather import WeatherError

# per-car memory survives across rounds inside one process run
_STATES: dict[str, CarGuardState] = {}


def reset_states() -> None:
    _STATES.clear()


def _state_for(vin: str) -> CarGuardState:
    if vin not in _STATES:
        _STATES[vin] = CarGuardState()
    return _STATES[vin]


def check_round(settings, client, weather_fn, now: dt.datetime | None = None,
                preview: bool = False) -> list[dict]:
    """Run one guard round over the whole fleet. Returns per-car result dicts.
    `weather_fn(lat, lon)` returns a WeatherNow (injectable fake in tests).
    `preview=True` logs and reports decisions without sending commands."""
    now = now or dt.datetime.now()
    snap = snapshot_fleet(client, settings.vehicles)
    results = []
    for v in snap.vehicles:
        st = _state_for(v.vin)
        entry: dict = {"car": v.name, "vin": v.vin}
        if not v.reachable:
            entry.update(action="skipped",
                         reason=f"I couldn't reach the car: {v.error}. "
                                "I'll try again next round.")
            results.append(entry)
            decision_log.append(settings, entry)
            continue
        if not v.parked:
            entry.update(action="nothing",
                         reason="Car is driving — windows are the driver's business.")
            results.append(entry)
            decision_log.append(settings, entry)
            continue
        if v.lat is None or v.lon is None:
            entry.update(action="skipped",
                         reason="No location from the car, so I can't check the "
                                "weather there. I'll try again next round.")
            results.append(entry)
            decision_log.append(settings, entry)
            continue
        try:
            wx = weather_fn(v.lat, v.lon)
        except WeatherError as exc:
            entry.update(action="skipped",
                         reason=f"Weather check failed: {exc}")
            results.append(entry)
            decision_log.append(settings, entry)
            continue

        decision = decide(v, wx, now, st,
                          settings.rain_lead_minutes,
                          settings.rain_prob_threshold,
                          settings.cooldown_minutes,
                          settings.vent_enable,
                          settings.vent_cabin_temp_c,
                          settings.vent_rain_clear_hours)
        entry.update(action=decision.action, reason=decision.reason)
        if decision.rain_minutes is not None:
            entry["rain_minutes"] = round(decision.rain_minutes, 1)

        if decision.action == NOTHING:
            entry["executed"] = False
        elif preview or settings.dry_run:
            entry["executed"] = False
        else:
            command = "close" if decision.action == CLOSE else "vent"
            try:
                client.window_control(v.vin, command)
            except Exception as exc:  # human error, never a stack trace
                entry.update(action="skipped", executed=False,
                             reason=f"I decided to {command} the windows, but the "
                                    f"car said no: {exc}. Check the car has a good "
                                    "data connection and try again.")
                results.append(entry)
                decision_log.append(settings, entry)
                continue
            entry["executed"] = True
            if decision.action == CLOSE:
                st.last_close = now
                st.windows_assumed_closed = True
            elif decision.action == VENT:
                st.last_vent = now
                st.windows_assumed_closed = False
        results.append(entry)
        decision_log.append(settings, entry)
    return results


def plain_summary(results: list[dict], preview: bool) -> str:
    """Plain-English round report for the CLI."""
    lines = []
    mode = "preview" if preview else "run"
    lines.append(f"Rain Guard {mode} — {len(results)} car(s) checked:")
    for r in results:
        car = r["car"]
        action = r["action"]
        if action == "close_windows" and r.get("executed"):
            lines.append(f"  • {car}: CLOSED the windows — {r['reason']}")
        elif action == "vent_windows" and r.get("executed"):
            lines.append(f"  • {car}: CRACKED the windows — {r['reason']}")
        elif action in ("close_windows", "vent_windows"):
            lines.append(f"  • {car}: would {('close' if action == 'close_windows' else 'vent')} "
                         f"the windows — {r['reason']}")
        elif action == "skipped":
            lines.append(f"  • {car}: skipped — {r['reason']}")
        else:
            lines.append(f"  • {car}: no action — {r['reason']}")
    return "\n".join(lines)
