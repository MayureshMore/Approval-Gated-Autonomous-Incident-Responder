"""
Shared fixtures.

The mock env runs for real (uvicorn on a free port) rather than being stubbed:
the point of these tests is that the agent, the contract and the environment
agree, and a stub would let them drift apart quietly.
"""
import os
import socket
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def mock_env():
    """Start the mock prod stack; yield its base URL."""
    import uvicorn
    from mock_env.main import app

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        pytest.fail("mock_env did not start")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def env(mock_env, monkeypatch):
    """Point tools.py at the live mock env and reset the scenario each test."""
    import requests

    import tools
    monkeypatch.setattr(tools, "BASE_URL", mock_env)
    requests.post(f"{mock_env}/reset", timeout=5)
    return mock_env


class RecordingBus:
    """An EventBus stand-in that records instead of writing files or HTTP."""

    def __init__(self, bus_url: str = ""):
        self.bus_url = bus_url
        self.run_id = "test"
        self.events: list[dict] = []

    def emit(self, kind: str, **data):
        ev = {"t": time.time(), "kind": kind, "run_id": self.run_id, **data}
        self.events.append(ev)
        return ev

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.events]

    def of(self, kind: str) -> list[dict]:
        return [e for e in self.events if e["kind"] == kind]


@pytest.fixture
def bus():
    return RecordingBus()


@pytest.fixture
def demo_payload():
    """The exact shape the agent hands the sandbox in the demo scenario."""
    from datetime import datetime, timedelta, timezone
    t = datetime.now(timezone.utc)
    return {
        "alert_fired_at": (t - timedelta(minutes=3)).isoformat(),
        "current_version": "v1.4.2",
        "deploys": [
            {"version": "v1.4.2", "deployed_at": (t - timedelta(minutes=15)).isoformat(),
             "deployed_by": "ci-bot", "status": "current"},
            {"version": "v1.4.1", "deployed_at": (t - timedelta(hours=30)).isoformat(),
             "deployed_by": "ci-bot", "status": "superseded"},
        ],
        "error_logs": [
            {"level": "ERROR",
             "message": "NullPointer in PaymentProcessor.charge() after v1.4.2 config change"},
        ],
    }
