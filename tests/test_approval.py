"""
The approval gate. This is the product claim, so it gets the harshest tests:
every way the gate can fail must fail CLOSED.
"""
import pytest

from agent.approval import ApprovalGate, Decision


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _ui_gate(bus, responses, monkeypatch, timeout_s=5):
    """A UI-mode gate whose /decision polls return `responses` in order."""
    import requests
    bus.bus_url = "http://bus.test"
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        item = responses[i]
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("agent.approval.POLL_INTERVAL_S", 0.01)
    return ApprovalGate(bus, mode="ui", timeout_s=timeout_s)


def test_human_approve(bus, monkeypatch):
    gate = _ui_gate(bus, [{"pending": True}, {"pending": False, "approved": True}], monkeypatch)
    d = gate.request("rollback_service", {"service": "checkout-service"}, "bad deploy")
    assert d.approved and d.by == "human"


def test_human_reject(bus, monkeypatch):
    gate = _ui_gate(bus, [{"pending": False, "approved": False}], monkeypatch)
    assert gate.request("rollback_service", {}, "r").approved is False


def test_emits_awaiting_then_decision_in_order(bus, monkeypatch):
    gate = _ui_gate(bus, [{"pending": False, "approved": True}], monkeypatch)
    gate.request("rollback_service", {"service": "s"}, "because")
    assert bus.kinds() == ["awaiting_approval", "approval_decision"]

    ask = bus.of("awaiting_approval")[0]
    assert ask["risk"] == "destructive"          # EVENT_CONTRACT.md
    assert ask["action"] == "rollback_service"
    assert ask["reason"] == "because"
    assert ask["request_id"] == bus.of("approval_decision")[0]["request_id"]


# --- fail closed -----------------------------------------------------------
def test_timeout_denies(bus, monkeypatch):
    gate = _ui_gate(bus, [{"pending": True}], monkeypatch, timeout_s=1)
    d = gate.request("rollback_service", {}, "r")
    assert d.approved is False and d.by == "system" and "within" in d.reason


def test_unreachable_bus_denies(bus, monkeypatch):
    gate = _ui_gate(bus, [ConnectionError("bus down")], monkeypatch, timeout_s=30)
    d = gate.request("rollback_service", {}, "r")
    assert d.approved is False and d.by == "system"


def test_interrupt_denies(bus, monkeypatch):
    gate = _ui_gate(bus, [KeyboardInterrupt()], monkeypatch)
    assert gate.request("rollback_service", {}, "r").approved is False


def test_non_tty_cli_denies(bus, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    gate = ApprovalGate(bus, mode="cli")
    d = gate.request("rollback_service", {}, "r")
    assert d.approved is False and d.by == "system"


# --- mode selection --------------------------------------------------------
def test_auto_approve_is_stamped_as_auto(bus, monkeypatch):
    monkeypatch.setenv("AUTO_APPROVE", "1")
    monkeypatch.setattr("time.sleep", lambda *_: None)
    gate = ApprovalGate(bus)
    d = gate.request("rollback_service", {}, "r")
    assert d.approved and d.by == "auto"
    # A recording must never be mistakable for a human-approved run.
    assert bus.of("approval_decision")[0]["by"] == "auto"


def test_mode_is_ui_when_bus_url_set(bus, monkeypatch):
    monkeypatch.delenv("AUTO_APPROVE", raising=False)
    bus.bus_url = "http://bus.test"
    assert ApprovalGate(bus).mode == "ui"


def test_mode_is_cli_without_bus(bus, monkeypatch):
    monkeypatch.delenv("AUTO_APPROVE", raising=False)
    assert ApprovalGate(bus).mode == "cli"
