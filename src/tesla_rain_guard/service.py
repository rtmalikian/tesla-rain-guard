"""Transport-independent guard service: takes OpenAI-shaped request dicts,
returns OpenAI-shaped response dicts. The HTTP server (server.py) is a thin
wrapper; tests drive this class directly.

Flow for a user message:
  1. Rules engine (qa.py) answers from the decision log + live window state.
  2. If rules don't match and ai_mode is auto|always → LLM backend with the
     fleet brief. An allowlisted JSON command block is validated and executed;
     otherwise the model's text is returned.
  3. ai_mode=never (or LLM failure) → capability-list answer.

Embeddings embed decision-log lines so clients can search "rain close"
history semantically. Everything works with the backend off.
"""
from __future__ import annotations

import itertools
import time

from . import decision_log
from .embeddings import embed_text
from .fleet import FleetSnapshot, find_vehicle, snapshot_fleet
from .llm import LlmBackend, extract_command
from .qa import ALLOWED_COMMANDS, answer_question, execute_command

CAPABILITY_BLURB = (
    "I watch the weather where your cars are parked and close the windows "
    "before rain hits — and crack them on scorching days. Ask me \"why did you "
    "close the windows at 3pm?\" or \"what are the windows doing?\", or say "
    "\"close the windows on the Model Y\" / \"vent the windows\". "
    "Connect an LLM backend (TRG_AI_BASE_URL) and I can handle free-form "
    "questions too."
)


class RainGuardService:
    def __init__(self, settings, client_factory=None, llm_factory=None):
        self.settings = settings
        self.client_factory = client_factory or (lambda: None)
        self.llm_factory = llm_factory
        self._ids = itertools.count(1)

    # -- plumbing ------------------------------------------------------
    def _client(self):
        return self.client_factory()

    def _llm(self):
        if self.llm_factory is not None:
            return self.llm_factory()
        if self.settings.ai_mode == "never":
            return None
        if not self.settings.ai_base_url:
            return None
        return LlmBackend(self.settings.ai_base_url, self.settings.ai_api_key,
                          self.settings.ai_model, self.settings.ai_timeout_seconds)

    def _snapshot(self, client) -> FleetSnapshot:
        return snapshot_fleet(client, self.settings.vehicles)

    def _next_id(self, prefix: str) -> str:
        return f"{prefix}-{int(time.time())}-{next(self._ids)}"

    # -- OpenAI surface ------------------------------------------------
    def models_list(self) -> dict:
        return {"object": "list", "data": [{
            "id": self.settings.model_id,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "tesla-rain-guard",
        }]}

    def chat_completions(self, body: dict) -> dict:
        messages = body.get("messages") or []
        user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_text = str(m.get("content") or "")
                break
        client = self._client()
        snap = self._snapshot(client)
        log = decision_log.read_tail(self.settings, limit=50)

        answer = answer_question(client, snap, log, user_text, self.settings.dry_run)
        if answer.matched:
            text = answer.text
        else:
            text = self._llm_answer(user_text, snap, log)

        return {
            "id": self._next_id("chatcmpl"),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.settings.model_id,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    def embeddings(self, body: dict) -> dict:
        raw = body.get("input", "")
        texts = raw if isinstance(raw, list) else [raw]
        data = []
        for i, t in enumerate(texts):
            data.append({
                "object": "embedding",
                "index": i,
                "embedding": embed_text(str(t), self.settings.embedding_dim),
            })
        return {
            "object": "list",
            "data": data,
            "model": self.settings.model_id,
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

    # -- internals -----------------------------------------------------
    def _llm_answer(self, user_text: str, snap: FleetSnapshot, log: list[dict]) -> str:
        llm = self._llm()
        if llm is None:
            return CAPABILITY_BLURB
        context = snap.brief() + "\n\nRecent decisions:\n" + "\n".join(
            decision_log.describe(e) for e in log[-10:]) or "(no decisions yet)"
        try:
            reply = llm.chat(context, user_text)
        except Exception:
            return CAPABILITY_BLURB + " (The AI backend didn't respond just now.)"
        cmd = extract_command(reply)
        if cmd is None:
            return reply
        command, vehicle_name = cmd
        vehicle = find_vehicle(snap, vehicle_name)
        if vehicle is None or not vehicle.reachable:
            names = ", ".join(v.name for v in snap.vehicles)
            return (f"I couldn't match \"{vehicle_name}\" to a reachable car. "
                    f"Your cars: {names}.")
        note = execute_command(self._client(), command, vehicle, self.settings.dry_run)
        return note
