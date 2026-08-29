"""
End-to-end runs against the live mock env, driven by the deterministic sim
provider. These assert the behaviour we promise on stage — including the one
invariant the whole project rests on.
"""
import json

import pytest

from agent.approval import ApprovalGate, Decision
from agent.core import IncidentAgent
from agent.providers.sim import SimProvider

DESTRUCTIVE = {"rollback_service", "restart_service", "scale_service", "open_revert_pr"}


class ScriptedGate(ApprovalGate):
    """A gate that answers however the test wants, with no human and no network."""

    def __init__(self, bus, approve: bool, by: str = "human"):
        self.bus, self.mode, self.timeout_s = bus, "test", 1
        self._approve, self._by, self._seen = approve, by, set()
        self.asked: list[tuple] = []

    def request(self, action, args, reason):
        self.asked.append((action, args, reason))
        self.bus.emit("awaiting_approval", action=action, args=args,
                      reason=reason, risk="destructive", request_id="rq")
        self.bus.emit("approval_decision", action=action, approved=self._approve,
                      by=self._by, reason="scripted", request_id="rq")
        return Decision(self._approve, self._by, "scripted", "rq")


def _run(bus, approve: bool, **kw):
    gate = ScriptedGate(bus, approve)
    agent = IncidentAgent(SimProvider(), bus=bus, gate=gate, **kw)
    return agent.run(), gate


# --- THE invariant ---------------------------------------------------------
def test_no_destructive_call_without_a_prior_approval(env, bus):
    """
    Walk the event stream in order. If a destructive tool_call ever appears
    before an approval_decision(approved=True) for that action, we have shipped
    a lie — fail loudly.
    """
    _run(bus, approve=True)

    approved: set[str] = set()
    for ev in bus.events:
        if ev["kind"] == "approval_decision" and ev["approved"]:
            approved.add(ev["action"])
        if ev["kind"] == "tool_call" and ev["tool"] in DESTRUCTIVE:
            assert ev["tool"] in approved, (
                f"{ev['tool']} executed with no prior approval in: {bus.kinds()}")


def test_approved_run_recovers_the_service(env, bus):
    report, gate = _run(bus, approve=True)
    assert report.error is None
    assert report.executed_destructive == ["rollback_service"]
    assert gate.asked[0][0] == "rollback_service"
    assert gate.asked[0][1]["to_version"] == "v1.4.1"

    final = [e for e in bus.of("tool_result") if e["tool"] == "get_metrics"][-1]["result"]
    assert final["status"] == "healthy"
    assert final["version"] == "v1.4.1"
    assert final["error_rate"] < 0.01


def test_rejected_run_leaves_production_untouched(env, bus):
    import tools
    report, _ = _run(bus, approve=False)

    assert report.executed_destructive == []
    assert not [e for e in bus.of("tool_call") if e["tool"] in DESTRUCTIVE]

    live = tools.get_metrics("checkout-service")
    assert live["status"] == "degraded" and live["version"] == "v1.4.2"


def test_agent_stands_down_after_rejection(env, bus):
    report, gate = _run(bus, approve=False)
    assert len(gate.asked) == 1, "must not re-ask a human who already said no"
    assert "reject" in report.final_message.lower() or "stand" in report.final_message.lower()


# --- the harness features we claim -----------------------------------------
def test_diagnosis_comes_from_the_sandbox_not_from_eyeballing(env, bus):
    report, _ = _run(bus, approve=True)
    assert report.sandbox_runs == 1

    exec_ev = bus.of("sandbox_exec")[0]
    assert "correlate_deploy_to_incident" in exec_ev["code"]

    diag = [e for e in bus.of("tool_result") if e["tool"] == "run_diagnostic"][0]["result"]
    assert diag["sandboxed"] is True
    assert diag["result"]["suspect"] == "v1.4.2"
    assert diag["result"]["rollback_target"] == "v1.4.1"


def test_subagents_investigate_in_parallel_and_all_report(env, bus):
    report, _ = _run(bus, approve=True, use_subagents=True)
    lanes = {e["subagent"] for e in bus.of("subagent_started")}
    assert lanes == {"metrics", "logs", "deploys"}
    assert {e["subagent"] for e in bus.of("subagent_finished")} == lanes
    assert all("error" not in e["findings"] for e in bus.of("subagent_finished"))


def test_subagents_never_touch_a_gated_tool(env, bus):
    from agent.subagents import READ_ONLY
    assert not (READ_ONLY & DESTRUCTIVE)


def test_restart_is_not_proposed_for_a_bad_deploy(env, bus):
    """The scenario is built so a restart won't fix it — the agent must not pick it."""
    report, _ = _run(bus, approve=True)
    assert "restart_service" not in report.tool_calls
    assert report.gated_actions == ["rollback_service"]


# --- resilience ------------------------------------------------------------
def test_environment_outage_is_reported_not_raised(env, bus, monkeypatch):
    import tools
    monkeypatch.setattr(tools, "BASE_URL", "http://127.0.0.1:1")   # nothing listening
    report, _ = _run(bus, approve=True)
    assert report.error is None, "a dead environment must not crash the run"
    assert any("error" in json.dumps(e["result"]) for e in bus.of("tool_result"))


def test_step_budget_is_enforced(env, bus):
    report, _ = _run(bus, approve=True, max_steps=2)
    assert report.steps <= 2
