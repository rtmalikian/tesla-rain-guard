"""Configuration for Tesla Rain Guard. All settings come from the
environment (see .env.example). Copy .env.example to .env — .env is
git-ignored so secrets never leave your machine."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


class ConfigError(Exception):
    pass


@dataclass
class VehicleConfig:
    name: str
    vin: str


@dataclass
class Settings:
    client_id: str = ""
    client_secret: str = ""
    region: str = "na"
    redirect_uri: str = "http://localhost:8080/callback"
    token_file: str = "~/.train/tokens.json"
    vehicles: list[VehicleConfig] = field(default_factory=list)
    # Guard behaviour
    check_interval_minutes: int = 20        # cadence between weather checks
    rain_lead_minutes: int = 60             # "rain is imminent" look-ahead
    rain_prob_threshold: float = 0.5        # precipitation probability that counts as rain
    cooldown_minutes: int = 30              # don't re-issue the same command inside this window
    vent_enable: bool = True                # hot-day window crack when no rain is coming
    vent_cabin_temp_c: float = 40.0         # cabin temp that triggers the vent crack
    vent_rain_clear_hours: int = 3           # vent only if no rain expected within this window
    decision_log: str = "~/.train/decision-log.jsonl"
    # Weather backend: Open-Meteo needs no key; any compatible JSON endpoint works.
    weather_base_url: str = "https://api.open-meteo.com/v1/forecast"
    weather_timeout_seconds: float = 15.0
    # The OpenAI-compatible server this app exposes.
    server_host: str = "127.0.0.1"
    server_port: int = 8643
    server_api_key: str = ""          # optional bearer token for /v1/*
    model_id: str = "tesla-rain-guard"
    # Pluggable LLM backend (any OpenAI-compatible API; default: local Ollama).
    ai_base_url: str = "http://localhost:11434/v1"
    ai_api_key: str = ""
    ai_model: str = "llama3.1"
    ai_mode: str = "auto"             # auto | always | never
    ai_timeout_seconds: float = 20.0
    embedding_dim: int = 384
    dry_run: bool = False


def _parse_vehicles(raw: str) -> list[VehicleConfig]:
    """Format: name=VIN,name2=VIN2 (or bare VINs)."""
    vehicles = []
    for idx, item in enumerate(raw.split(",")):
        item = item.strip()
        if not item:
            continue
        name = None
        if "=" in item:
            name, item = item.split("=", 1)
            name = name.strip()
        vin = item.strip()
        if not vin:
            raise ConfigError("Empty VIN in TRG_VEHICLES")
        vehicles.append(VehicleConfig(name=name or f"car-{idx + 1}", vin=vin))
    if not vehicles:
        raise ConfigError("TRG_VEHICLES is empty — add at least one name=VIN entry")
    return vehicles


def _int(env: dict, key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc


def _float(env: dict, key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc


def _bool(env: dict, key: str, default: bool = False) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def load_settings(env: dict | None = None) -> Settings:
    env = os.environ if env is None else env
    s = Settings()
    s.client_id = env.get("TESLA_CLIENT_ID", "")
    s.client_secret = env.get("TESLA_CLIENT_SECRET", "")
    s.region = env.get("TESLA_REGION", "na").strip().lower()
    if s.region not in ("na", "eu", "cn"):
        raise ConfigError(f"TESLA_REGION must be na|eu|cn, got {s.region!r}")
    s.redirect_uri = env.get("TESLA_REDIRECT_URI", s.redirect_uri)
    s.token_file = env.get("TRG_TOKEN_FILE", s.token_file)
    raw_vehicles = env.get("TRG_VEHICLES", "")
    if raw_vehicles.strip():
        s.vehicles = _parse_vehicles(raw_vehicles)
    s.check_interval_minutes = _int(env, "TRG_CHECK_INTERVAL_MINUTES", s.check_interval_minutes)
    if not (5 <= s.check_interval_minutes <= 240):
        raise ConfigError("TRG_CHECK_INTERVAL_MINUTES must be between 5 and 240")
    s.rain_lead_minutes = _int(env, "TRG_RAIN_LEAD_MINUTES", s.rain_lead_minutes)
    if not (10 <= s.rain_lead_minutes <= 360):
        raise ConfigError("TRG_RAIN_LEAD_MINUTES must be between 10 and 360")
    s.rain_prob_threshold = _float(env, "TRG_RAIN_PROB_THRESHOLD", s.rain_prob_threshold)
    if not (0.0 < s.rain_prob_threshold <= 1.0):
        raise ConfigError("TRG_RAIN_PROB_THRESHOLD must be between 0 (exclusive) and 1")
    s.cooldown_minutes = _int(env, "TRG_COOLDOWN_MINUTES", s.cooldown_minutes)
    if s.cooldown_minutes < 0:
        raise ConfigError("TRG_COOLDOWN_MINUTES must be 0 or more")
    s.vent_enable = _bool(env, "TRG_VENT_ENABLE", s.vent_enable)
    s.vent_cabin_temp_c = _float(env, "TRG_VENT_CABIN_TEMP_C", s.vent_cabin_temp_c)
    s.vent_rain_clear_hours = _int(env, "TRG_VENT_RAIN_CLEAR_HOURS", s.vent_rain_clear_hours)
    if s.vent_rain_clear_hours < 1:
        raise ConfigError("TRG_VENT_RAIN_CLEAR_HOURS must be at least 1")
    s.decision_log = env.get("TRG_DECISION_LOG", s.decision_log)
    s.weather_base_url = env.get("TRG_WEATHER_BASE_URL", s.weather_base_url).rstrip("/")
    s.weather_timeout_seconds = _float(env, "TRG_WEATHER_TIMEOUT_SECONDS",
                                       s.weather_timeout_seconds)
    s.server_host = env.get("TRG_SERVER_HOST", s.server_host)
    s.server_port = _int(env, "TRG_SERVER_PORT", s.server_port)
    s.server_api_key = env.get("TRG_SERVER_API_KEY", "")
    s.model_id = env.get("TRG_MODEL_ID", s.model_id)
    s.ai_base_url = env.get("TRG_AI_BASE_URL", s.ai_base_url).rstrip("/")
    s.ai_api_key = env.get("TRG_AI_API_KEY", "")
    s.ai_model = env.get("TRG_AI_MODEL", s.ai_model)
    s.ai_mode = env.get("TRG_AI_MODE", s.ai_mode).strip().lower()
    if s.ai_mode not in ("auto", "always", "never"):
        raise ConfigError(f"TRG_AI_MODE must be auto|always|never, got {s.ai_mode!r}")
    s.ai_timeout_seconds = _float(env, "TRG_AI_TIMEOUT_SECONDS", s.ai_timeout_seconds)
    s.embedding_dim = _int(env, "TRG_EMBEDDING_DIM", s.embedding_dim)
    if not (8 <= s.embedding_dim <= 4096):
        raise ConfigError("TRG_EMBEDDING_DIM must be between 8 and 4096")
    s.dry_run = _bool(env, "TRG_DRY_RUN")
    return s
