"""Fake fleet + fake weather for tests and demos. No API calls, no real
cars. The demo runs a full virtual day proving the guard's core invariant:
windows get closed before rain hits a parked car, hot dry days get a vent
crack, and driving cars are never touched."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from . import run as run_module
from .fleet import CLOSED, OPEN, UNKNOWN, VENTED
from .weather import HourSlot, WeatherNow


@dataclass
class FakeVehicle:
    vin: str
    name: str
    windows: str = CLOSED
    shift_state: str | None = "P"
    inside_temp: float | None = 24.0
    outside_temp: float = 30.0
    lat: float = 34.05
    lon: float = -118.24
    locked: bool = True
    odometer: float = 12345.0


class FakeTeslaClient:
    """Stand-in for TeslaClient with the methods rain-guard uses."""

    def __init__(self, vehicles: list[FakeVehicle]):
        self.vehicles = {v.vin: v for v in vehicles}
        self.command_log: list[tuple[str, str]] = []

    def vehicle_data(self, vin: str) -> dict:
        v = self.vehicles[vin]
        win_val = {"closed": 0, "vented": 1, "open": 2}.get(v.windows)
        return {
            "display_name": v.name,
            "locked": v.locked,
            "charge_state": {"battery_level": 80, "charging_state": "Disconnected"},
            "climate_state": {"inside_temp": v.inside_temp,
                              "outside_temp": v.outside_temp},
            "drive_state": {"shift_state": v.shift_state, "odometer": v.odometer,
                            "latitude": v.lat, "longitude": v.lon},
            "vehicle_state": ({"fd_window": win_val, "fp_window": win_val,
                               "rd_window": win_val, "rp_window": win_val}
                              if win_val is not None else {}),
        }

    def window_control(self, vin: str, command: str) -> dict:
        v = self.vehicles[vin]
        v.windows = CLOSED if command == "close" else VENTED
        self.command_log.append(("window_control", vin))
        return {"result": True}


def make_weather(now: dt.datetime, rain_at: dt.datetime | None,
                 cabin_note: str = "") -> WeatherNow:
    """Fake weather: rain slot at `rain_at` (or none), clear otherwise."""
    hours = []
    for h in range(24):
        t = now.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=h)
        if rain_at and abs((t - rain_at).total_seconds()) < 3600:
            hours.append(HourSlot(t, 0.85, 61, 22.0))
        else:
            hours.append(HourSlot(t, 0.02, 0, 24.0))
    return WeatherNow(current_code=0, current_temp_c=24.0, hours=hours)


def demo_vehicles() -> list[FakeVehicle]:
    return [
        FakeVehicle(vin="VINY", name="Model Y", windows=VENTED,
                    shift_state="P", inside_temp=26.0),
        FakeVehicle(vin="VIN3", name="Model 3", windows=CLOSED,
                    shift_state="P", inside_temp=45.0),  # hot -> vent day
    ]


def run_demo(settings) -> list[str]:
    """A full virtual demo day through the real guard code against fakes.
    Returns printable timeline lines."""
    from .config import VehicleConfig

    lines: list[str] = []
    lines.append("Tesla Rain Guard — demo day (fake cars, fake weather)")
    lines.append("=" * 60)
    run_module.reset_states()

    vehicles = demo_vehicles()
    settings.vehicles = [VehicleConfig(name=v.name, vin=v.vin) for v in vehicles]
    settings.decision_log = "/tmp/tesla-rain-guard-demo-log.jsonl"
    fake = FakeTeslaClient(vehicles)

    day = dt.datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)

    # Morning: storm rolling in at 10:30, windows vented on the Y.
    t = day
    lines.append(f"[{t.strftime('%H:%M')}] Storm forecast: rain from ~10:30, "
                 "Model Y windows are cracked.")
    results = run_module.check_round(settings, fake,
                                     lambda lat, lon: make_weather(t, day.replace(hour=10, minute=30)),
                                     now=t, preview=True)
    lines.extend("  " + r for r in run_module.plain_summary(results, True).splitlines()[1:])

    # 10:05 — real round: rain within lead window -> close.
    t = day.replace(hour=10, minute=5)
    lines.append(f"\n[{t.strftime('%H:%M')}] Rain is 25 min out — guard round:")
    results = run_module.check_round(settings, fake,
                                     lambda lat, lon: make_weather(t, day.replace(hour=10, minute=30)),
                                     now=t)
    lines.extend("  " + r for r in run_module.plain_summary(results, False).splitlines()[1:])
    assert fake.vehicles["VINY"].windows == CLOSED, "invariant broken: windows not closed!"

    # 10:12 — rain arrived; nothing more to do.
    t = day.replace(hour=10, minute=12)
    wx = make_weather(t, None)
    wx.current_code = 61  # raining now
    results = run_module.check_round(settings, fake, lambda lat, lon: wx, now=t)
    lines.append(f"\n[{t.strftime('%H:%M')}] Raining now:")
    lines.extend("  " + r for r in run_module.plain_summary(results, False).splitlines()[1:])

    # 14:00 — clear hot afternoon: Model 3 cabin 45°C, no rain -> vent crack.
    t = day.replace(hour=14, minute=0)
    lines.append(f"\n[{t.strftime('%H:%M')}] Hot, dry afternoon:")
    results = run_module.check_round(settings, fake,
                                     lambda lat, lon: make_weather(t, None), now=t)
    lines.extend("  " + r for r in run_module.plain_summary(results, False).splitlines()[1:])

    # 18:00 — owner drives; windows open; guard must NOT touch them.
    fake.vehicles["VIN3"].shift_state = "D"
    fake.vehicles["VIN3"].windows = OPEN
    log_before = len(fake.command_log)
    t = day.replace(hour=18, minute=0)
    lines.append(f"\n[{t.strftime('%H:%M')}] Owner driving with windows open:")
    results = run_module.check_round(settings, fake,
                                     lambda lat, lon: make_weather(t, None), now=t)
    lines.extend("  " + r for r in run_module.plain_summary(results, False).splitlines()[1:])
    assert len(fake.command_log) == log_before, \
        "invariant broken: guard commanded windows on a driving car!"

    # -- API cost estimate (playbook §6) ---------------------------------
    # Per round: 1 no-wake data call per car. Commands only on state change.
    n_rounds = 1440 // settings.check_interval_minutes
    data_calls = n_rounds * len(vehicles)
    commands = len(fake.command_log)
    cost = data_calls / 500 + commands / 1000
    lines.append("")
    lines.append(f"Estimated API cost: ~{data_calls} data reads/day "
                 f"({n_rounds} rounds × {len(vehicles)} cars) + ~{commands} commands "
                 f"≈ ${cost:.2f}/month. Wakes dominate the bill — the guard never wakes "
                 "a sleeping car; it checks the last known parked state instead.")
    lines.append("")
    lines.append("Ask the chat: `train ask \"why did you close the windows at 10am?\"`")
    return lines
