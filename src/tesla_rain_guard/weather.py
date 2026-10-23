"""Weather reads via Open-Meteo (free, no key required): current +
hourly forecast at the car's parked location. Deterministic parsing —
the guard treats missing/failed weather as "unknown" and skips the round
rather than guessing."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import requests

# WMO weather codes that mean rain or drizzle.
RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}
# Thunderstorms / freezing rain — also a "close the windows now" signal.
SEVERE_RAIN_CODES = {66, 67, 80, 81, 82, 95, 96, 99}


class WeatherError(Exception):
    pass


@dataclass
class HourSlot:
    time: dt.datetime
    precip_prob: float          # 0..1
    weather_code: int
    temperature_c: float | None = None

    @property
    def raining(self) -> bool:
        return self.weather_code in RAIN_CODES


@dataclass
class WeatherNow:
    """What the sky is doing now + the forecast the guard reasons over."""
    current_code: int
    current_temp_c: float | None
    hours: list[HourSlot]

    def minutes_until_rain(self, now: dt.datetime, prob_threshold: float,
                           lead_minutes: int) -> float | None:
        """Minutes from `now` until the first forecast hour that looks like
        rain (probability at/above threshold, or an explicitly rainy code
        even at lower probability). None if no rain inside the lead window."""
        for slot in self.hours:
            delta = (slot.time - now).total_seconds() / 60
            if delta < -30 or delta > lead_minutes:
                continue
            if slot.precip_prob >= prob_threshold or slot.weather_code in RAIN_CODES:
                return max(delta, 0)
        return None

    def clear_for_hours(self, now: dt.datetime, hours: int,
                        prob_threshold: float) -> bool:
        """True when no rain is expected within the next `hours`."""
        limit = now + dt.timedelta(hours=hours)
        for slot in self.hours:
            if now - dt.timedelta(minutes=30) <= slot.time <= limit:
                if slot.precip_prob >= prob_threshold or slot.weather_code in RAIN_CODES:
                    return False
        return True

    def observed_rain_now(self, prob_threshold: float) -> bool:
        """Rain falling right now (observed current conditions)."""
        return self.current_code in RAIN_CODES


def fetch_weather(lat: float, lon: float, base_url: str,
                  timeout: float = 15.0) -> WeatherNow:
    """Pull current + 24h hourly from Open-Meteo. Raises WeatherError on
    any failure so callers skip the round with a human-readable message."""
    try:
        resp = requests.get(
            base_url,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,weather_code",
                "hourly": "precipitation_probability,weather_code,temperature_2m",
                "forecast_days": 2,
                "timezone": "auto",
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise WeatherError(
            "Couldn't reach the weather service — check your internet "
            f"connection and try again ({exc.__class__.__name__}).") from exc
    if resp.status_code != 200:
        raise WeatherError(
            f"The weather service returned HTTP {resp.status_code} — "
            "it's probably having a moment. Try again in a bit.")
    try:
        data = resp.json()
    except ValueError as exc:
        raise WeatherError("The weather service sent back something "
                           "unreadable — try again in a bit.") from exc

    current = data.get("current") or {}
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    probs = hourly.get("precipitation_probability") or []
    codes = hourly.get("weather_code") or []
    temps = hourly.get("temperature_2m") or []

    hours = []
    for i, t in enumerate(times):
        try:
            ts = dt.datetime.fromisoformat(t)
        except ValueError:
            continue
        hours.append(HourSlot(
            time=ts,
            precip_prob=(probs[i] or 0) / 100.0 if i < len(probs) else 0.0,
            weather_code=int(codes[i]) if i < len(codes) and codes[i] is not None else 0,
            temperature_c=temps[i] if i < len(temps) else None,
        ))
    return WeatherNow(
        current_code=int(current.get("weather_code") or 0),
        current_temp_c=current.get("temperature_2m"),
        hours=hours,
    )
