"""Deterministic Q&A engine: answers questions from the live snapshot and
the decision log, and executes allowlisted window commands. This is the
offline brain — the app is fully useful with the LLM backend disabled.
The LLM layer only handles questions the rules don't match (see service.py)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import decision_log
from .fleet import FleetSnapshot, VehicleState

ALLOWED_COMMANDS = {"close_windows", "vent_windows"}

COMMAND_VERBS = ("close_windows", "vent_windows")


@dataclass
class Answer:
    text: str
    matched: bool = True
    commands_run: list[str] = field(default_factory=list)
    dry_run_notes: list[str] = field(default_factory=list)


def resolve_vehicle(snap: FleetSnapshot, text: str) -> VehicleState | None:
    t = text.lower()
    for v in snap.vehicles:
        tokens = [v.name.lower()] + ([v.display_name.lower()] if v.display_name else [])
        for tok in tokens:
            if tok and tok in t:
                return v
    for v in snap.vehicles:
        for m in re.findall(r"model\s*[3yxs]", t):
            if m[-1] in v.name.lower():
                return v
    if len(snap.vehicles) == 1:
        return snap.vehicles[0]
    return None


def _window_line(v: VehicleState) -> str:
    if not v.reachable:
        return f"{v.name}: unreachable ({v.error})."
    state_word = {"closed": "closed", "vented": "cracked open",
                  "open": "open", "unknown": "not reported"}.get(v.windows, v.windows)
    drive = "driving" if not v.parked else "parked"
    return (f"{v.name}: windows {state_word} ({drive}), "
            f"inside {v.inside_temp_c}°C." if v.inside_temp_c is not None
            else f"{v.name}: windows {state_word} ({drive}).")


def detect_command(text: str) -> str | None:
    """Map imperative phrasing to an allowlisted command, or None."""
    t = text.lower()
    if re.search(r"\b(close|shut|roll up)\b[^.]*\bwindow", t):
        return "close_windows"
    if re.search(r"\b(vent|crack)\b[^.]*\bwindow", t):
        return "vent_windows"
    return None


def execute_command(client, command: str, v: VehicleState, dry_run: bool) -> str:
    """Run one allowlisted command (already validated) against a vehicle."""
    if dry_run:
        return f"[dry-run] would run window_control({command}) on {v.name}"
    if command not in ALLOWED_COMMANDS:
        raise ValueError(f"command {command!r} is not allowlisted")
    client.window_control(v.vin, "close" if command == "close_windows" else "vent")
    return f"Ran {command} on {v.name}."


def _why_answer(log: list[dict], question: str) -> str | None:
    """Answer 'why did you close/vent the windows ...?' from the log."""
    t = question.lower()
    if not any(w in t for w in ("why", "what happened", "explain", "tell me about")):
        return None
    close_asks = "close" in t or "shut" in t or "rain" in t
    vent_asks = "vent" in t or "crack" in t or "hot" in t
    entries = list(reversed(log))
    if close_asks:
        hits = [e for e in entries if e.get("action") == "close_windows"]
    elif vent_asks:
        hits = [e for e in entries if e.get("action") == "vent_windows"]
    else:
        hits = entries
    if not hits:
        if not log:
            return ("I haven't run a guard round yet — run `train check` (or wait "
                    "for the schedule) and ask me again.")
        return ("Nothing like that in my recent decisions. Here's what I did most "
                "recently:\n" + decision_log.describe(hits[0] if hits else entries[0]))
    latest = hits[0]
    extra = ""
    for e in hits[1:3]:
        extra += "\nAlso: " + decision_log.describe(e)
    return decision_log.describe(latest) + extra


def answer_question(client, snap: FleetSnapshot, log: list[dict],
                    question: str, dry_run: bool) -> Answer:
    """Rules-first answering. Unmatched questions return matched=False so the
    service layer can escalate to the LLM (or the capability list)."""
    t = question.lower().strip()
    target = resolve_vehicle(snap, question)

    # "why did you close the windows?" is an explanation question first,
    # even though it contains the word "close".
    why = _why_answer(log, question)
    if why is not None:
        return Answer(why)

    command = detect_command(question)
    if command:
        if target is None:
            names = ", ".join(v.name for v in snap.vehicles)
            return Answer(f"Which car? You have: {names}. Say e.g. "
                          f"'{command.replace('_', ' ')} on the <name>'.")
        if not target.parked:
            return Answer(f"The {target.name} is driving — I won't touch its "
                          "windows. Your call, driver.")
        note = execute_command(client, command, target, dry_run)
        return Answer(note, commands_run=[] if dry_run else [f"{command}:{target.name}"],
                      dry_run_notes=[note] if dry_run else [])

    if "window" in t or "rain" in t:
        if target:
            return Answer(_window_line(target))
        return Answer(" ".join(_window_line(v) for v in snap.vehicles))

    if any(w in t for w in ("status", "summary", "how is", "how are", "everything")):
        return Answer(" ".join(_window_line(v) for v in snap.vehicles))

    if "weather" in t or "forecast" in t:
        return Answer("I read the weather fresh each guard round from Open-Meteo at "
                      "the car's parked location — no stored forecast. Run "
                      "`train check` to read it right now.")

    return Answer("", matched=False)
