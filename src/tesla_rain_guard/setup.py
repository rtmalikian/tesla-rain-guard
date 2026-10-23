"""Guided first-run setup: plain-language questions, writes .env itself.

Built for Tesla owners, not developers — defaults everywhere, numbered
choices, and every value explained in everyday words. Testable: input and
output are injectable.
"""
from __future__ import annotations

import os
from pathlib import Path


def _ask(input_fn, out, prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    out(f"{prompt}{suffix}")
    answer = input_fn("> ").strip()
    return answer or default


def _ask_required(input_fn, out, prompt: str, help_text: str) -> str:
    while True:
        out(help_text)
        answer = input_fn(f"{prompt} ").strip()
        if answer:
            return answer
        out("That one's required — without it the app can't talk to your car.")


def run_setup(input_fn=input, out=print, env_path: str = ".env") -> Path:
    out("")
    out("Welcome to Tesla Rain Guard setup — I'll ask a few questions and")
    out("write the settings file for you. Press Enter to accept a [default].")
    out("")

    path = Path(env_path)
    if path.exists():
        again = _ask(input_fn, out,
                     "A settings file (.env) already exists. Replace it? (y/n)", "n")
        if again.lower() not in ("y", "yes"):
            out("Keeping your existing settings. Nothing changed.")
            return path

    out("STEP 1 of 5 — Connect to Tesla")
    out("Create a free developer app at developer.tesla.com (sign in, then")
    out("'Create App'). Tesla will show you a Client ID and Client Secret.")
    client_id = _ask_required(input_fn, out, "Paste your Client ID:",
                              "First, the Client ID:")
    client_secret = _ask_required(input_fn, out, "Paste your Client Secret:",
                                  "Now the Client Secret (it stays on this computer):")

    out("")
    out("STEP 2 of 5 — Your cars")
    out("For each car: a nickname and its VIN. Find the VIN in your Tesla")
    out("app under Controls → Software (or on the car's touchscreen).")
    vehicles: list[tuple[str, str]] = []
    while True:
        n = len(vehicles) + 1
        name = _ask(input_fn, out,
                    f"Car {n} nickname (e.g. Model Y) — or press Enter if done:",
                    "" if vehicles else "")
        if not name:
            if vehicles:
                break
            out("Add at least one car to continue.")
            continue
        vin = _ask_required(input_fn, out, f"VIN for {name}:",
                            f"Now the VIN for {name}:")
        vehicles.append((name, vin))

    out("")
    out("STEP 3 of 5 — Rain rules (plain words)")
    out("The guard checks the weather where each parked car is and closes the")
    out("windows before rain hits. It uses a free weather service — no key needed.")
    lead = _ask(input_fn, out,
                "How far ahead should rain trigger a close? (minutes)", "60")
    interval = _ask(input_fn, out,
                    "How often should I check the weather? (minutes)", "20")
    heat = _ask(input_fn, out,
                "On hot, dry days I can crack the windows to let heat out. "
                "Turn this on? (y/n)", "y")
    vent_c = ""
    if heat.lower() in ("y", "yes"):
        vent_c = _ask(input_fn, out,
                      "Crack the windows when the cabin is above (Celsius):", "40")

    out("")
    out("STEP 4 of 5 — The AI brain (optional)")
    out("  1) Built-in answers only — no AI, nothing leaves this computer")
    out("  2) Ollama on this computer — free local AI (default)")
    out("  3) Another AI service (OpenAI, Groq, LM Studio…) — you'll paste its details")
    choice = _ask(input_fn, out, "Pick 1, 2 or 3:", "2")
    ai_mode, ai_url, ai_key, ai_model = "auto", "http://localhost:11434/v1", "", "llama3.1"
    if choice == "1":
        ai_mode = "never"
    elif choice == "3":
        ai_url = _ask_required(input_fn, out, "Service address (base URL):",
                               "Paste the service's API address:")
        ai_key = _ask(input_fn, out, "API key for that service (stored only here):")
        ai_model = _ask(input_fn, out, "Model name:", "gpt-4o-mini")

    out("")
    out("STEP 5 of 5 — The chat server")
    port = _ask(input_fn, out, "Port for the chat address:", "8643")
    server_key = _ask(input_fn, out,
                      "Optional password for the chat address (blank = none):")

    lines = [
        "# Written by `train setup` — edit by re-running setup any time.",
        f"TESLA_CLIENT_ID={client_id}",
        f"TESLA_CLIENT_SECRET={client_secret}",
        "TESLA_REGION=na",
        "TESLA_REDIRECT_URI=http://localhost:8080/callback",
        "TRG_VEHICLES=" + ",".join(f"{name}={vin}" for name, vin in vehicles),
        f"TRG_CHECK_INTERVAL_MINUTES={interval or '20'}",
        f"TRG_RAIN_LEAD_MINUTES={lead or '60'}",
        "TRG_RAIN_PROB_THRESHOLD=0.5",
        "TRG_COOLDOWN_MINUTES=30",
        f"TRG_VENT_ENABLE={'1' if heat.lower() in ('y', 'yes') else '0'}",
        f"TRG_VENT_CABIN_TEMP_C={vent_c or '40'}",
        "TRG_VENT_RAIN_CLEAR_HOURS=3",
        "TRG_DECISION_LOG=~/.train/decision-log.jsonl",
        "TRG_WEATHER_BASE_URL=https://api.open-meteo.com/v1/forecast",
        "TRG_SERVER_HOST=127.0.0.1",
        f"TRG_SERVER_PORT={port}",
        f"TRG_SERVER_API_KEY={server_key}",
        "TRG_MODEL_ID=tesla-rain-guard",
        f"TRG_AI_BASE_URL={ai_url}",
        f"TRG_AI_API_KEY={ai_key}",
        f"TRG_AI_MODEL={ai_model}",
        f"TRG_AI_MODE={ai_mode}",
        "TRG_AI_TIMEOUT_SECONDS=20",
        "TRG_EMBEDDING_DIM=384",
        "TRG_TOKEN_FILE=~/.train/tokens.json",
        "TRG_DRY_RUN=0",
    ]
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)

    out("")
    out("All set — settings saved to .env (only readable by you).")
    out("Next steps:")
    out("  1) train auth       — sign in to Tesla once (opens a link)")
    out("  2) train simulate   — see a demo day with pretend cars and weather")
    out("  3) train preview    — see what the guard would do right now (no commands)")
    out("  4) train check      — run one real guard round")
    out("")
    out(f"Put `train check` on a schedule (cron/systemd) every {interval or '20'} minutes.")
    return path
