import math

from tesla_rain_guard.config import Settings, VehicleConfig
from tesla_rain_guard.service import CAPABILITY_BLURB, RainGuardService
from tesla_rain_guard.simulator import FakeTeslaClient, demo_vehicles


def make_service(ai_mode="never", llm=None, dry_run=False):
    settings = Settings(vehicles=[VehicleConfig("Model Y", "VINY"),
                                  VehicleConfig("Model 3", "VIN3")],
                        ai_mode=ai_mode, dry_run=dry_run,
                        decision_log="/tmp/tesla-rain-guard-test-log.jsonl")
    fake = FakeTeslaClient(demo_vehicles())
    service = RainGuardService(settings, client_factory=lambda: fake,
                               llm_factory=(lambda: llm) if llm is not None else None)
    return service, fake


def chat(service, text):
    body = {"model": "tesla-rain-guard",
            "messages": [{"role": "user", "content": text}]}
    return service.chat_completions(body)


def test_chat_shape():
    service, _ = make_service()
    resp = chat(service, "what are the windows doing?")
    assert resp["object"] == "chat.completion"
    msg = resp["choices"][0]["message"]
    assert msg["role"] == "assistant" and "Model Y" in msg["content"]
    assert resp["model"] == "tesla-rain-guard"


def test_models_shape():
    service, _ = make_service()
    models = service.models_list()
    assert models["object"] == "list"
    assert models["data"][0]["id"] == "tesla-rain-guard"
    assert models["data"][0]["owned_by"] == "tesla-rain-guard"


def test_embeddings_shape_and_determinism():
    service, _ = make_service()
    r1 = service.embeddings({"input": "rain close windows decision"})
    r2 = service.embeddings({"input": "rain close windows decision"})
    vec = r1["data"][0]["embedding"]
    assert len(vec) == 384
    assert vec == r2["data"][0]["embedding"]
    assert abs(math.sqrt(sum(x * x for x in vec)) - 1.0) < 1e-6


def test_embeddings_batch():
    service, _ = make_service()
    r = service.embeddings({"input": ["a", "b"]})
    assert len(r["data"]) == 2
    assert r["data"][1]["index"] == 1


def test_chat_close_command_executes():
    service, fake = make_service()
    fake.vehicles["VINY"].shift_state = "P"
    resp = chat(service, "close the windows on the Model Y")
    assert fake.command_log == [("window_control", "VINY")]
    assert fake.vehicles["VINY"].windows == "closed"
    assert "close_windows" in resp["choices"][0]["message"]["content"]


def test_chat_command_parked_guard():
    service, fake = make_service()
    fake.vehicles["VINY"].shift_state = "D"
    resp = chat(service, "close the windows on the Model Y")
    assert fake.command_log == []
    assert "driving" in resp["choices"][0]["message"]["content"].lower()


def test_chat_command_dry_run():
    service, fake = make_service(dry_run=True)
    resp = chat(service, "close the windows on the Model Y")
    assert fake.command_log == []
    assert "dry-run" in resp["choices"][0]["message"]["content"]


def test_chat_command_needs_car():
    service, fake = make_service()
    resp = chat(service, "close the windows")
    assert "Which car" in resp["choices"][0]["message"]["content"]
    assert fake.command_log == []


def test_chat_why_explains_from_log():
    from tesla_rain_guard import decision_log
    service, fake = make_service()
    decision_log.append(service.settings, {
        "timestamp": "2026-10-23T10:05:00", "car": "Model Y",
        "action": "close_windows", "executed": True,
        "reason": "Windows are vented; rain expected in ~25 min — closing before the cabin gets wet.",
    })
    resp = chat(service, "why did you close the windows at 10am?")
    content = resp["choices"][0]["message"]["content"]
    assert "closed the windows on the Model Y" in content
    assert "25" in content


def test_chat_window_status():
    service, _ = make_service()
    resp = chat(service, "status?")
    content = resp["choices"][0]["message"]["content"]
    assert "Model Y" in content and "windows" in content.lower()


def test_chat_unmatched_returns_capabilities():
    service, _ = make_service(ai_mode="never")
    resp = chat(service, "what's the meaning of life?")
    assert "close the windows" in resp["choices"][0]["message"]["content"]


def test_chat_llm_failure_falls_back():
    class DeadBackend:
        def chat(self, context, user_message):
            raise ConnectionError("no backend here")

    service, _ = make_service(ai_mode="auto", llm=DeadBackend())
    resp = chat(service, "what's the meaning of life?")
    content = resp["choices"][0]["message"]["content"]
    assert "close the windows" in content


def test_llm_command_must_be_allowlisted():
    from tesla_rain_guard.llm import extract_command
    assert extract_command(
        '```json\n{"command": "close_windows", "vehicle": "Model Y"}\n```') == \
        ("close_windows", "Model Y")
    assert extract_command(
        '```json\n{"command": "open_frunk", "vehicle": "Model Y"}\n```') is None
    assert extract_command("no block here") is None
