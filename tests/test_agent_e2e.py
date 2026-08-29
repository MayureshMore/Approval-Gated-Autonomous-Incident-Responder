"""
End-to-end runs against the live mock env, driven by the deterministic sim
provider. These assert the behaviour we promise on stage — including the one
invariant the whole project rests on.
"""
import json

import pytest

from agent.approval import ApprovalGate, Decision
from agent.core import IncidentAgent
from agent.providers.base import AssistantTurn, ToolCall
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


def test_subagent_findings_carry_the_raw_log_lines(env, bus):
    """
    The main agent feeds these into the sandbox diagnostic. Without the raw
    lines it passes the summary instead and scores the incident wrong.
    """
    _run(bus, approve=True, use_subagents=True)
    logs = [e for e in bus.of("subagent_finished") if e["subagent"] == "logs"][0]["findings"]
    assert isinstance(logs["lines"], list) and logs["lines"], "raw lines must travel with the summary"
    assert all(isinstance(l, dict) and "message" in l for l in logs["lines"])

    from diagnostics import correlate_deploy_to_incident
    alerts = [e for e in bus.of("tool_result") if e["tool"] == "get_active_alerts"][0]["result"]
    deploys = [e for e in bus.of("tool_result") if e["tool"] == "get_recent_deploys"][0]["result"]
    out = correlate_deploy_to_incident(alerts[0]["fired_at"], deploys, logs["lines"])
    assert out["version_referenced_in_error_logs"] is True
    assert out["suspicion_score"] >= 0.7


# --- the approval card must never be blank ---------------------------------
class SilentProvider:
    """A model that fires a destructive call with no accompanying prose.

    This is not hypothetical: gpt-4o over the TrueFoundry gateway does exactly
    this. It ran the sandbox diagnostic, then called rollback_service with an
    empty text field — which reached the approval card as "no rationale given".
    """

    name = "silent"

    def __init__(self, narrate_first: bool = False):
        self.narrate_first = narrate_first
        self._turn = 0

    def start(self, system, user):
        pass

    def step(self, schemas):
        self._turn += 1
        if self._turn == 1:
            return AssistantTurn(
                text="Correlating the deploy against the alert." if self.narrate_first else "",
                tool_calls=[ToolCall("c1", "run_diagnostic", {
                    "code": ("from diagnostics import correlate_deploy_to_incident\n"
                             "RESULT = correlate_deploy_to_incident(\n"
                             "    PAYLOAD['alert_fired_at'], PAYLOAD['deploys'],\n"
                             "    PAYLOAD['error_logs'])\n"),
                    "payload": _diagnostic_payload(),
                })])
        if self._turn == 2:
            # No text. This is the turn that used to produce a blank card.
            return AssistantTurn(text="", tool_calls=[ToolCall(
                "c2", "rollback_service",
                {"service": "checkout-service", "to_version": "v1.4.1"})])
        return AssistantTurn(text="done")

    def record_tool_result(self, call, result):
        pass


def _diagnostic_payload() -> dict:
    import tools
    alerts = tools.get_active_alerts()
    return {
        "alert_fired_at": alerts[0]["fired_at"],
        "deploys": tools.get_recent_deploys("checkout-service"),
        "error_logs": tools.get_logs("checkout-service", level="ERROR"),
    }


def _ask_reason(bus, provider) -> str:
    gate = ScriptedGate(bus, approve=False)
    IncidentAgent(provider, bus=bus, gate=gate, max_steps=4).run()
    rollbacks = [r for a, _, r in gate.asked if a == "rollback_service"]
    assert rollbacks, f"rollback was never gated; asked={[a for a, _, _ in gate.asked]}"
    return rollbacks[0]


def test_silent_destructive_call_still_gets_an_explained_card(env, bus):
    """A model that says nothing must not produce an unexplained approval card."""
    reason = _ask_reason(bus, SilentProvider())

    assert "no rationale given" not in reason
    assert reason.strip(), "approval card reason was empty"
    # It falls back to what the sandbox actually computed, and says so.
    assert "gave no rationale" in reason
    assert "v1.4.2" in reason, f"evidence missing from card: {reason!r}"


def test_fallback_prefers_what_the_agent_last_said(env, bus):
    reason = _ask_reason(bus, SilentProvider(narrate_first=True))

    assert "Correlating the deploy against the alert." in reason
    assert "last stated by the agent" in reason


def test_a_stated_rationale_is_passed_through_verbatim(env, bus):
    """The fallback must never overwrite a rationale the model did give."""
    _, gate = _run(bus, approve=True)
    rollbacks = [r for a, _, r in gate.asked if a == "rollback_service"]
    assert rollbacks
    assert "gave no rationale" not in rollbacks[0]


# --- the harness-injected `reason` must reach the human, not the tool -------
def _dispatch(bus, name: str, args: dict):
    """Run one gated call through _handle with the real impl signatures."""
    gate = ScriptedGate(bus, approve=True)
    agent = IncidentAgent(None, bus=bus, gate=gate, include_github=True)
    seen = {}

    def rollback_service(service, to_version):
        seen.update(service=service, to_version=to_version)
        return {"ok": True}

    def open_revert_pr(service, reason):
        seen.update(service=service, reason=reason)
        return {"ok": True}

    agent.impls["rollback_service"] = rollback_service
    agent.impls["open_revert_pr"] = open_revert_pr
    agent._handle(ToolCall("t1", name, dict(args)), rationale="")
    return seen, gate.asked[0]


def test_injected_reason_is_stripped_before_the_tool_runs(env, bus):
    """rollback_service(service, to_version) would die on an unexpected kwarg."""
    seen, (action, card_args, card_reason) = _dispatch(
        bus, "rollback_service",
        {"service": "checkout-service", "to_version": "v1.4.1",
         "reason": "v1.4.2 correlates at 0.76"})

    assert "reason" not in seen
    assert seen == {"service": "checkout-service", "to_version": "v1.4.1"}
    # The human still sees it, and the card shows the args that actually ran.
    assert card_reason == "v1.4.2 correlates at 0.76"
    assert card_args == {"service": "checkout-service", "to_version": "v1.4.1"}


def test_open_revert_pr_still_receives_its_own_reason(env, bus):
    """Its `reason` is a real parameter — it goes in the PR body."""
    seen, (_, _, card_reason) = _dispatch(
        bus, "open_revert_pr",
        {"service": "checkout-service", "reason": "revert the bad deploy"})

    assert seen["reason"] == "revert the bad deploy"
    assert card_reason == "revert the bad deploy"


def test_a_model_that_omits_reason_entirely_does_not_crash(env, bus):
    """Required in the schema is not a guarantee the model complies."""
    seen, (_, _, card_reason) = _dispatch(
        bus, "rollback_service", {"service": "checkout-service", "to_version": "v1.4.1"})

    assert seen == {"service": "checkout-service", "to_version": "v1.4.1"}
    assert "gave no rationale" in card_reason


# --- the agent must not misreport who denied it ----------------------------
def test_a_human_rejection_is_reported_as_a_human_rejection(env, bus):
    report, _ = _run(bus, approve=False)
    assert "on-call human" in report.final_message


def test_a_gate_that_fails_closed_is_not_reported_as_a_human(env, bus):
    """A dead bus, a timeout or a non-tty all deny — but no human saw it.

    Saying "rejected by the on-call human" there is the agent inventing a
    human decision, on a project whose entire claim is that a human made it.
    """
    gate = ScriptedGate(bus, approve=False, by="system")
    agent = IncidentAgent(SimProvider(), bus=bus, gate=gate)
    report = agent.run()

    assert "on-call human" not in report.final_message
    assert "failed" in report.final_message and "no human" in report.final_message
    # Still stands down, whoever said no.
    assert report.executed_destructive == []


# --- approval is not execution ---------------------------------------------
class BrokenEnvProvider(SilentProvider):
    """Proposes a rollback the environment will refuse."""

    def step(self, schemas):
        self._turn += 1
        if self._turn == 1:
            return AssistantTurn(text="Rolling back.", tool_calls=[ToolCall(
                "c1", "rollback_service",
                # A version that never shipped — the env refuses it.
                {"service": "checkout-service", "to_version": "v0.0.0-never-shipped",
                 "reason": "testing the failure path"})])
        return AssistantTurn(text="done")


def test_an_approved_action_that_fails_is_not_reported_as_executed(env, bus):
    """The env being unreachable, or refusing, means production did not change.
    Saying "executed" there overstates what we did."""
    gate = ScriptedGate(bus, approve=True)
    report = IncidentAgent(BrokenEnvProvider(), bus=bus, gate=gate, max_steps=3).run()

    assert report.gated_actions == ["rollback_service"]      # it was proposed
    assert report.executed_destructive == []                 # but nothing happened
    assert report.approved_but_failed == ["rollback_service"]


def test_a_successful_approved_action_is_still_reported_as_executed(env, bus):
    report, _ = _run(bus, approve=True)

    assert report.executed_destructive == ["rollback_service"]
    assert report.approved_but_failed == []
