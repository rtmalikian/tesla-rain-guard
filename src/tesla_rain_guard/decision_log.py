"""Decision log: every check round appends what the guard decided and why
(plus any commands sent) to a JSONL file. The chat endpoint answers
"why did you close the windows at 3pm?" from this log — the explanation
layer, no AI required."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


def _path(settings) -> Path:
    return Path(settings.decision_log).expanduser()


def append(settings, entry: dict) -> None:
    entry = dict(entry)
    entry.setdefault("timestamp", dt.datetime.now().isoformat(timespec="seconds"))
    p = _path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


def read_tail(settings, limit: int = 50) -> list[dict]:
    p = _path(settings)
    if not p.exists():
        return []
    lines = p.read_text().splitlines()
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def describe(entry: dict) -> str:
    """One plain-English line per log entry, for the chat answers."""
    ts = str(entry.get("timestamp", ""))[:16].replace("T", " ")
    car = entry.get("car", "car")
    action = entry.get("action", "nothing")
    reason = entry.get("reason", "")
    executed = entry.get("executed")
    if action == "close_windows":
        base = f"At {ts} I closed the windows on the {car}."
    elif action == "vent_windows":
        base = f"At {ts} I cracked the windows on the {car} for heat."
    elif action == "skipped":
        base = f"At {ts} I skipped a check on the {car}."
    else:
        base = f"At {ts} the {car} was checked — no action needed."
    if reason:
        base += f" Reason: {reason}"
    if executed is False:
        base += " (dry-run — I didn't touch the car.)"
    return base
