"""End-to-end: the demo day asserts the guard's core invariant — windows
are closed before rain hits a parked car, and a driving car is never
touched. Plus the HTTP smoke test of the OpenAI-compatible surface."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from tesla_rain_guard import run as run_module
from tesla_rain_guard.config import Settings, VehicleConfig
from tesla_rain_guard.server import make_handler
from tesla_rain_guard.service import RainGuardService
from tesla_rain_guard.simulator import FakeTeslaClient, demo_vehicles, run_demo


def _settings(**kw):
    settings = Settings(vehicles=[VehicleConfig("Model Y", "VINY"),
                                  VehicleConfig("Model 3", "VIN3")],
                        ai_mode="never",
                        decision_log="/tmp/tesla-rain-guard-test-log.jsonl", **kw)
    return settings


def test_demo_day_core_invariant():
    settings = _settings(dry_run=False)
    run_module.reset_states()
    lines = run_demo(settings)
    text = "\n".join(lines)
    assert "CLOSED the windows" in text
    assert "CRACKED the windows" in text
    assert "driver's business" in text
    assert "Estimated API cost" in text


def test_http_chat_roundtrip():
    fake = FakeTeslaClient(demo_vehicles())
    service = RainGuardService(_settings(), client_factory=lambda: fake)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        body = json.dumps({"model": "tesla-rain-guard", "messages": [
            {"role": "user", "content": "what are the windows doing?"}]}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions",
                                     data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        assert "Model Y" in data["choices"][0]["message"]["content"]

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models",
                                    timeout=10) as resp:
            models = json.loads(resp.read())
        assert models["data"][0]["id"] == "tesla-rain-guard"

        body = json.dumps({"input": "rain decision log"}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/embeddings",
                                     data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            emb = json.loads(resp.read())
        assert len(emb["data"][0]["embedding"]) == 384
    finally:
        httpd.shutdown()


def test_http_auth_required_when_key_set():
    fake = FakeTeslaClient(demo_vehicles())
    service = RainGuardService(_settings(server_api_key="secret"),
                               client_factory=lambda: fake)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=10)
            raise AssertionError("expected 401")
        except urllib.error.HTTPError as e:
            assert e.code == 401
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/models",
            headers={"Authorization": "Bearer secret"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
    finally:
        httpd.shutdown()


def test_preview_logs_without_commands():
    from tesla_rain_guard import decision_log
    import datetime as dt
    run_module.reset_states()
    settings = _settings(dry_run=False)
    fake = FakeTeslaClient([v for v in demo_vehicles()])
    fake.vehicles["VINY"].windows = "open"
    results = run_module.check_round(
        settings, fake,
        lambda lat, lon: _rain_now(), now=dt.datetime.now(), preview=True)
    assert fake.command_log == []
    close = [r for r in results if r["action"] == "close_windows"]
    assert close and close[0]["executed"] is False


def _rain_now():
    from tesla_rain_guard.weather import WeatherNow
    return WeatherNow(current_code=61, current_temp_c=22.0, hours=[])
