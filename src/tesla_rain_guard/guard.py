"""Rain-guard decision engine: pure logic, no network, `now` injected —
fully unit-testable. Given a car's state and the weather at its parked
location, decide whether to close the windows (rain coming), crack them
open a touch (hot day, no rain), or do nothing.

Invariant: the guard NEVER commands windows on a car that is driving, and
it never re-issues the same command inside the cooldown window.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .fleet import CLOSED, OPEN, UNKNOWN, VENTED, VehicleState
from .weather import WeatherNow

CLOSE, VENT, NOTHING = "close_windows", "vent_windows", "nothing"


@dataclass
class Decision:
    action: str                     # close_windows | vent_windows | nothing
    reason: str                     # plain-English explanation for the log/chat
    rain_minutes: float | None = None


@dataclass
class CarGuardState:
    """Per-car memory across check rounds."""
    last_close: dt.datetime | None = None
    last_vent: dt.datetime | None = None
    windows_assumed_closed: bool = False   # set True after a successful close

    def effective_windows(self, car: VehicleState) -> str:
        """Window state the guard reasons over: the car's own reading wins;
        when the car doesn't report (unknown), remember our last close."""
        if car.windows != UNKNOWN:
            return car.windows
        if self.windows_assumed_closed:
            return CLOSED
        return UNKNOWN


def decide(car: VehicleState, wx: WeatherNow, now: dt.datetime,
           state: CarGuardState, rain_lead_minutes: int,
           rain_prob_threshold: float, cooldown_minutes: int,
           vent_enable: bool, vent_cabin_temp_c: float,
           vent_rain_clear_hours: int) -> Decision:
    windows = state.effective_windows(car)

    # Safety first: never touch windows on a moving car.
    if not car.parked:
        return Decision(NOTHING,
                        "Car is driving — windows are the driver's business, not mine.")

    rain_min = wx.minutes_until_rain(now, rain_prob_threshold, rain_lead_minutes)
    raining_now = wx.observed_rain_now(rain_prob_threshold)

    # Case 1 — rain is falling or about to: close anything not confirmed shut.
    if raining_now or rain_min is not None:
        if windows == CLOSED:
            return Decision(NOTHING, "Windows already closed; rain handled.")
        if state.last_close is not None and \
                (now - state.last_close).total_seconds() / 60 < cooldown_minutes:
            return Decision(NOTHING,
                            "Rain expected but I already closed the windows recently "
                            "(cooldown).")
        if raining_now:
            what = "rain is falling now"
        elif rain_min is not None and rain_min < 15:
            what = "rain is about to start"
        else:
            what = f"rain expected in ~{rain_min:.0f} min"
        return Decision(
            CLOSE,
            f"Windows are {windows}; {what} — closing before the cabin gets wet.",
            rain_minutes=rain_min if not raining_now else 0.0,
        )

    # Case 2 — hot day, no rain on the horizon: crack the windows for airflow.
    if vent_enable and windows == CLOSED and car.inside_temp_c is not None \
            and car.inside_temp_c >= vent_cabin_temp_c:
        if wx.clear_for_hours(now, vent_rain_clear_hours, rain_prob_threshold):
            if state.last_vent is not None and \
                    (now - state.last_vent).total_seconds() / 60 < cooldown_minutes:
                return Decision(NOTHING, "Hot cabin but I already vented recently (cooldown).")
            return Decision(
                VENT,
                f"Cabin is {car.inside_temp_c:.0f}°C and no rain expected for "
                f"{vent_rain_clear_hours}h — cracking the windows to let heat out.",
            )
        return Decision(NOTHING,
                        "Cabin is hot, but rain is forecast soon — keeping windows shut.")

    # Case 3 — nothing to do.
    if windows in (VENTED, OPEN, UNKNOWN):
        return Decision(NOTHING,
                        f"Windows are {windows} but no rain is expected — leaving them alone.")
    return Decision(NOTHING, "No rain expected and the cabin is fine.")
