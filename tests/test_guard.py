"""Decision-logic unit tests for the rain-guard engine, incl. edge cases."""
import datetime as dt

import pytest

from tesla_rain_guard.fleet import CLOSED, OPEN, UNKNOWN, VENTED, VehicleState
from tesla_rain_guard.guard import CLOSE, NOTHING, VENT, CarGuardState, decide
from tesla_rain_guard.weather import HourSlot, WeatherNow

NOW = dt.datetime(2026, 10, 23, 10, 0)


def _car(windows=VENTED, parked=True, inside_temp=24.0):
    return VehicleState(name="Model Y", vin="VINY", windows=windows,
                       shift_state="P" if parked else "D",
                       inside_temp_c=inside_temp)


def _wx(rain_at: dt.datetime | None = None, current_raining: bool = False):
    hours = []
    for h in range(24):
        t = NOW.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=h)
        rainy = rain_at is not None and abs((t - rain_at).total_seconds()) < 3600
        hours.append(HourSlot(t, 0.85 if rainy else 0.02, 61 if rainy else 0))
    return WeatherNow(current_code=61 if current_raining else 0,
                      current_temp_c=22.0, hours=hours)


def _decide(car, wx, state=None, **kw):
    defaults = dict(rain_lead_minutes=60, rain_prob_threshold=0.5,
                    cooldown_minutes=30, vent_enable=True,
                    vent_cabin_temp_c=40.0, vent_rain_clear_hours=3)
    defaults.update(kw)
    return decide(car, wx, NOW, state or CarGuardState(), **defaults)


def test_close_vented_windows_before_rain():
    # rain slot at the top of the next hour -> "~60 min" in the reason
    d = _decide(_car(windows=VENTED), _wx(rain_at=NOW + dt.timedelta(minutes=60)))
    assert d.action == CLOSE
    assert "60" in d.reason


def test_rain_falling_now_closes_open_windows():
    d = _decide(_car(windows=OPEN), _wx(current_raining=True))
    assert d.action == CLOSE
    assert "falling" in d.reason


def test_already_closed_no_action():
    d = _decide(_car(windows=CLOSED), _wx(rain_at=NOW + dt.timedelta(minutes=25)))
    assert d.action == NOTHING


def test_distant_rain_no_action():
    d = _decide(_car(windows=VENTED), _wx(rain_at=NOW + dt.timedelta(hours=5)))
    assert d.action == NOTHING


def test_never_touch_driving_car():
    d = _decide(_car(windows=OPEN, parked=False),
                _wx(rain_at=NOW + dt.timedelta(minutes=10)))
    assert d.action == NOTHING
    assert "driving" in d.reason


def test_unknown_windows_treated_as_not_closed():
    d = _decide(_car(windows=UNKNOWN), _wx(rain_at=NOW + dt.timedelta(minutes=20)))
    assert d.action == CLOSE  # close is a harmless no-op when already shut


def test_cooldown_prevents_repeated_close():
    state = CarGuardState(last_close=NOW - dt.timedelta(minutes=5))
    d = _decide(_car(windows=UNKNOWN), _wx(rain_at=NOW + dt.timedelta(minutes=20)),
                state=state)
    assert d.action == NOTHING
    assert "cooldown" in d.reason


def test_assumed_closed_after_own_close():
    state = CarGuardState(last_close=NOW - dt.timedelta(hours=1),
                         windows_assumed_closed=True)
    d = _decide(_car(windows=UNKNOWN), _wx(rain_at=NOW + dt.timedelta(minutes=20)),
                state=state)
    assert d.action == NOTHING  # we remember closing them


def test_hot_dry_day_vents():
    d = _decide(_car(windows=CLOSED, inside_temp=45.0), _wx())
    assert d.action == VENT
    assert "45" in d.reason


def test_hot_day_no_vent_if_disabled():
    d = _decide(_car(windows=CLOSED, inside_temp=45.0), _wx(), vent_enable=False)
    assert d.action == NOTHING


def test_hot_day_no_vent_if_rain_coming():
    d = _decide(_car(windows=CLOSED, inside_temp=45.0),
                _wx(rain_at=NOW + dt.timedelta(hours=2)))
    # rain at +2h is outside the 60-min lead -> close not triggered;
    # but vent must not fire since rain is inside the clear window.
    assert d.action == NOTHING
    assert "rain" in d.reason.lower()


def test_no_vent_below_threshold():
    d = _decide(_car(windows=CLOSED, inside_temp=39.9), _wx())
    assert d.action == NOTHING


def test_vent_not_repeated_within_cooldown():
    state = CarGuardState(last_vent=NOW - dt.timedelta(minutes=5))
    d = _decide(_car(windows=CLOSED, inside_temp=45.0), _wx(), state=state)
    assert d.action == NOTHING
    assert "cooldown" in d.reason


def test_vent_needs_closed_windows():
    d = _decide(_car(windows=VENTED, inside_temp=45.0), _wx())
    assert d.action == NOTHING  # already cracked; nothing to gain


def test_rain_probability_below_threshold_ignored():
    hours = [HourSlot(NOW + dt.timedelta(minutes=20), 0.10, 0)]
    wx = WeatherNow(current_code=0, current_temp_c=22.0, hours=hours)
    d = _decide(_car(windows=VENTED), wx)
    assert d.action == NOTHING


def test_minutes_until_rain_boundary():
    """Rain exactly at the lead boundary counts; just past it doesn't."""
    def wx_at(minutes):
        slot = HourSlot(NOW + dt.timedelta(minutes=minutes), 0.9, 61)
        return WeatherNow(current_code=0, current_temp_c=22.0, hours=[slot])
    d = _decide(_car(windows=VENTED), wx_at(60))
    assert d.action == CLOSE
    d = _decide(_car(windows=VENTED), wx_at(61))
    assert d.action == NOTHING
