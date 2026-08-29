"""
Session persistence (Layer 4).

The claim is "kill it mid-run and it continues". The dangerous version of that
claim is a resumed run that re-executes a destructive action, or skips the gate
because it thinks it already asked. Both are tested here.
"""
import json
import os

import pytest

from agent.approval import Decision
from agent.core import IncidentAgent
from agent.providers.sim import SimProvider
from agent.session import FINISHED, RUNNING, SessionStore
from tests.test_agent_e2e import DESTRUCTIVE, ScriptedGate


@pytest.fixture
def store(tmp_path):
    return SessionStore(root=str(tmp_path / "runs"))


class DyingGate(ScriptedGate):
    """Stands in for the process being killed while parked at the gate."""

    class Killed(Exception):
        pass

    def request(self, action, args, reason):
        raise self.Killed(f"process died awaiting approval of {action}")


def _agent(bus, store, gate, **kw):
    return IncidentAgent(SimProvider(), bus=bus, gate=gate, session=store, **kw)


# --- the store -------------------------------------------------------------
def test_save_and_load_roundtrip(store):
    store.save({"run_id": "abc", "status": RUNNING, "step": 3})
    assert store.load("abc")["step"] == 3
    assert store.exists("abc") and not store.exists("nope")


def test_latest_prefers_the_newest_unfinished_run(store):
    store.save({"run_id": "old", "status": FINISHED, "step": 9})
    store.save({"run_id": "new", "status": RUNNING, "step": 2})
    assert store.latest() == "new"


def test_latest_is_none_when_everything_finished(store):
    store.save({"run_id": "done", "status": FINISHED})
    assert store.latest() is None


def test_a_corrupt_state_file_is_skipped_not_fatal(store):
    store.save({"run_id": "good", "status": RUNNING})
    os.makedirs(store.root, exist_ok=True)
    with open(os.path.join(store.root, "torn.json"), "w") as f:
        f.write("{not json")
    assert [r["run_id"] for r in store.list_runs()] == ["good"]


def test_writes_are_atomic(store):
    """A torn file would make the run unresumable, defeating the point."""
    store.save({"run_id": "x", "status": RUNNING})
    assert not [f for f in os.listdir(store.root) if f.endswith(".tmp")]


# --- checkpointing ---------------------------------------------------------
def test_a_completed_run_is_marked_finished(env, bus, store):
    report = _agent(bus, store, ScriptedGate(bus, True)).run()
    assert store.load(report.run_id)["status"] == FINISHED


def test_a_killed_run_stays_resumable_with_its_pending_call(env, bus, store):
    agent = _agent(bus, store, DyingGate(bus, True))
    report = agent.run()

    state = store.load(report.run_id)
    assert state["status"] == "failed"
    assert [c["name"] for c in state["pending_calls"]] == ["rollback_service"]
    assert state["pending_calls"][0]["args"]["to_version"] == "v1.4.1"
    assert state["sandbox_runs"] == 1, "work already done must not be lost"


def test_checkpoint_failure_does_not_kill_the_run(env, bus, store, monkeypatch, capsys):
    monkeypatch.setattr(store, "save", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    report = _agent(bus, store, ScriptedGate(bus, True)).run()
    assert report.error is None and report.executed_destructive == ["rollback_service"]
    assert "checkpoint failed" in capsys.readouterr().out


# --- resuming --------------------------------------------------------------
def test_resume_finishes_the_job(env, bus, store):
    """Kill it at the gate, resume, approve — production recovers."""
    import tools
    died = _agent(bus, store, DyingGate(bus, True)).run()
    assert tools.get_metrics("checkout-service")["status"] == "degraded"

    state = store.load(died.run_id)
    resumed = _agent(bus, store, ScriptedGate(bus, True)).resume(state)

    assert resumed.executed_destructive == ["rollback_service"]
    assert tools.get_metrics("checkout-service")["status"] == "healthy"
    assert store.load(died.run_id)["status"] == FINISHED


def test_resume_does_not_redo_the_investigation(env, bus, store):
    died = _agent(bus, store, DyingGate(bus, True)).run()
    state = store.load(died.run_id)
    before = list(state["tool_calls"])

    fresh = ScriptedGate(bus, True)
    resumed = _agent(bus, store, fresh).resume(state)

    assert resumed.steps >= state["step"], "step count continues, it does not restart"
    assert resumed.sandbox_runs == state["sandbox_runs"], "the sandbox must not re-run"

    # tool_calls is restored from the checkpoint, so compare the DELTA: resume
    # must add only the remediation and the recovery check, never a re-investigation.
    added = resumed.tool_calls[len(before):]
    assert "get_active_alerts" not in added, f"investigation was repeated: {added}"
    assert "get_logs" not in added and "run_diagnostic" not in added
    assert "rollback_service" in added


def test_resume_still_asks_before_the_destructive_action(env, bus, store):
    """The invariant must survive a restart — this is the whole safety claim."""
    died = _agent(bus, store, DyingGate(bus, True)).run()
    bus.events.clear()

    gate = ScriptedGate(bus, True)
    _agent(bus, store, gate).resume(store.load(died.run_id))

    assert [a[0] for a in gate.asked] == ["rollback_service"], "resume must re-ask"
    approved: set[str] = set()
    for ev in bus.events:
        if ev["kind"] == "approval_decision" and ev["approved"]:
            approved.add(ev["action"])
        if ev["kind"] == "tool_call" and ev["tool"] in DESTRUCTIVE:
            assert ev["tool"] in approved, "resumed run executed without approval"


def test_resume_honours_a_rejection_from_before_the_crash(env, bus, store):
    """A denial recorded pre-crash must not be forgotten and re-asked into a yes."""
    import tools
    agent = _agent(bus, store, ScriptedGate(bus, False))
    report = agent.run()
    state = store.load(report.run_id)
    assert state["denied"], "the denial must be persisted"

    resumed = _agent(bus, store, ScriptedGate(bus, True))
    resumed.resume({**state, "pending_calls": [
        {"id": "retry", "name": "rollback_service",
         "args": {"service": "checkout-service", "to_version": "v1.4.1"}}]})

    assert tools.get_metrics("checkout-service")["status"] == "degraded"
    assert resumed.report.executed_destructive == []


def test_resumed_run_keeps_its_run_id_so_the_timeline_is_continuous(env, bus, store):
    died = _agent(bus, store, DyingGate(bus, True)).run()
    state = store.load(died.run_id)
    bus.run_id = state["run_id"]
    resumed = _agent(bus, store, ScriptedGate(bus, True)).resume(state)

    assert resumed.run_id == died.run_id
    assert [e["kind"] for e in bus.of("run_resumed")] == ["run_resumed"]


def test_provider_conversation_survives_the_restart(env, bus, store):
    died = _agent(bus, store, DyingGate(bus, True)).run()
    state = store.load(died.run_id)
    assert state["provider_state"]["turn"] >= 5
    assert "get_recent_deploys" in state["provider_state"]["seen"]


@pytest.mark.parametrize("provider_cls,attr", [
    ("agent.providers.openai_provider.OpenAIProvider", "messages"),
])
def test_openai_style_providers_snapshot_their_conversation(provider_cls, attr, monkeypatch):
    import importlib
    import types
    mod_name, cls_name = provider_cls.rsplit(".", 1)
    cls = getattr(importlib.import_module(mod_name), cls_name)

    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: types.SimpleNamespace(chat=None))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    p = cls()
    p.start("sys", "user")
    snap = p.snapshot()
    q = cls()
    q.restore(snap)
    assert getattr(q, attr) == getattr(p, attr)


# --- bus replay (Qodo #5) --------------------------------------------------
class FakeBusServer:
    """Stands in for approval_server over HTTP, recording what gets pushed."""

    def __init__(self, existing=None):
        self.existing = existing or []
        self.pushed: list[dict] = []


@pytest.fixture
def wired_bus(monkeypatch):
    from agent.bus import EventBus
    server = FakeBusServer()
    bus = EventBus(bus_url="http://bus.test", events_path="/dev/null", run_id="R1", echo=False)

    class Resp:
        def __init__(self, payload): self._p = payload
        def json(self): return self._p

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp(server.existing))
    monkeypatch.setattr(requests, "post", lambda url, json=None, timeout=None:
                        server.pushed.append(json) or Resp({"ok": True}))
    monkeypatch.setattr(bus, "_write_mirror", lambda: None)
    return bus, server


def test_replay_reseeds_a_bus_that_lost_its_memory(wired_bus):
    """Ctrl-C kills the approval server too, so a resumed run meets an empty bus."""
    bus, server = wired_bus
    history = [{"kind": "run_started", "run_id": "R1"}, {"kind": "tool_call", "run_id": "R1"}]
    assert bus.replay(history) == 2
    assert [e["kind"] for e in server.pushed] == ["run_started", "tool_call"]
    assert bus.events == history


def test_replay_is_idempotent_when_the_bus_survived(wired_bus):
    """Killing only the agent leaves the bus intact — re-pushing would duplicate."""
    bus, server = wired_bus
    server.existing = [{"kind": "run_started", "run_id": "R1"}]
    assert bus.replay([{"kind": "run_started", "run_id": "R1"}]) == 0
    assert server.pushed == []


def test_replay_ignores_another_runs_events_on_the_bus(wired_bus):
    bus, server = wired_bus
    server.existing = [{"kind": "run_started", "run_id": "SOMEONE-ELSE"}]
    assert bus.replay([{"kind": "run_started", "run_id": "R1"}]) == 1


def test_replay_of_nothing_is_a_no_op(wired_bus):
    bus, server = wired_bus
    assert bus.replay([]) == 0 and server.pushed == []


def test_resume_replays_history_before_announcing_itself(env, bus, store):
    """The dashboard must never see run_resumed as the first event of a run."""
    died = _agent(bus, store, DyingGate(bus, True)).run()
    state = store.load(died.run_id)

    fresh = type(bus)()
    fresh.run_id = state["run_id"]
    _agent(fresh, store, ScriptedGate(fresh, True)).resume(state)

    kinds = fresh.kinds()
    assert kinds[0] == "run_started", f"timeline starts at {kinds[0]!r}, not the original run"
    assert kinds.index("run_started") < kinds.index("run_resumed")
