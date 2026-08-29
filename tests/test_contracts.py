"""
Contract conformance — the seams with Person B.

TOOL_CONTRACT.md and EVENT_CONTRACT.md are frozen. If someone renames a metric
field or invents an event kind, the dashboard silently stops rendering and we
find out on stage. These tests turn that into a red build instead.
"""
import inspect

import pytest

import tools
from agent.bus import CONTRACT_KINDS, EXTENDED_KINDS
from agent.registry import REQUIRES_APPROVAL, build_registry


# --- TOOL_CONTRACT.md ------------------------------------------------------
def test_metrics_shape_is_exactly_the_contract(env):
    m = tools.get_metrics("checkout-service")
    assert set(m) == {"service", "status", "version", "error_rate",
                      "p99_latency_ms", "cpu", "memory", "replicas"}


def test_alert_shape(env):
    a = tools.get_active_alerts()[0]
    assert set(a) == {"id", "service", "severity", "summary", "fired_at"}


def test_log_shape(env):
    line = tools.get_logs("checkout-service")[0]
    assert set(line) == {"ts", "level", "service", "message"}


def test_deploy_shape(env):
    d = tools.get_recent_deploys("checkout-service")[0]
    assert set(d) == {"version", "deployed_at", "deployed_by", "status"}


def test_service_list_shape(env):
    s = tools.list_services()[0]
    assert set(s) == {"service", "status", "version", "replicas"}


def test_gated_set_matches_the_contract():
    assert tools.REQUIRES_APPROVAL == {"rollback_service", "restart_service", "scale_service"}
    # Layer 2 adds one more; it must only ever grow, never shrink.
    assert tools.REQUIRES_APPROVAL <= REQUIRES_APPROVAL
    assert "open_revert_pr" in REQUIRES_APPROVAL


def test_every_destructive_tool_is_gated():
    """Anything that mutates state or reaches the outside world must be gated."""
    _, schemas = build_registry(include_github=True)
    for s in schemas:
        fn = s["function"]
        if "DESTRUCTIVE" in fn["description"].upper():
            assert fn["name"] in REQUIRES_APPROVAL, f"{fn['name']} is destructive but ungated"


def test_schemas_and_implementations_agree():
    """Every declared parameter must exist on the implementation's signature."""
    impls, schemas = build_registry(include_github=True)
    for s in schemas:
        name = s["function"]["name"]
        declared = set(s["function"]["parameters"].get("properties", {}))
        params = inspect.signature(impls[name]).parameters
        accepts_kwargs = any(p.kind == p.VAR_KEYWORD for p in params.values())
        if not accepts_kwargs:
            assert declared <= set(params), f"{name}: schema declares {declared - set(params)}"


def test_reset_restores_the_degraded_scenario(env):
    import requests
    tools.rollback_service("checkout-service", "v1.4.1")
    assert tools.get_metrics("checkout-service")["status"] == "healthy"
    requests.post(f"{env}/reset", timeout=5)
    m = tools.get_metrics("checkout-service")
    assert m["status"] == "degraded" and m["version"] == "v1.4.2"


def test_restart_does_not_fix_a_bad_deploy(env):
    """The scenario must keep forcing real reasoning — a restart is not a fix."""
    tools.restart_service("checkout-service")
    m = tools.get_metrics("checkout-service")
    assert m["status"] == "degraded" and m["error_rate"] > 0.1


# --- EVENT_CONTRACT.md -----------------------------------------------------
REQUIRED_FIELDS = {
    "run_started": {"scenario"},
    "tool_call": {"tool", "args"},
    "tool_result": {"tool", "result"},
    "awaiting_approval": {"action", "args", "reason", "risk"},
    "approval_decision": {"action", "approved", "by"},
    "agent_message": {"text"},
    "run_finished": set(),
}


def test_contract_kinds_are_exactly_the_documented_seven():
    assert CONTRACT_KINDS == set(REQUIRED_FIELDS)


def test_extended_kinds_do_not_shadow_contract_kinds():
    assert not (EXTENDED_KINDS & CONTRACT_KINDS)


def test_a_real_run_emits_only_known_kinds_with_required_fields(env, bus):
    from tests.test_agent_e2e import _run
    _run(bus, approve=True, use_subagents=True)

    for ev in bus.events:
        assert {"t", "kind"} <= set(ev), "every event needs t and kind"
        kind = ev["kind"]
        assert kind in CONTRACT_KINDS | EXTENDED_KINDS, f"undocumented event kind '{kind}'"
        missing = REQUIRED_FIELDS.get(kind, set()) - set(ev)
        assert not missing, f"{kind} is missing {missing}"


def test_dashboard_can_find_service_health_in_the_stream(env, bus):
    """
    EVENT_CONTRACT.md: the dashboard reads health from any tool_result whose
    result carries `service` + `status`. Prove such an event actually occurs.
    """
    from tests.test_agent_e2e import _run
    _run(bus, approve=True)
    health = [e for e in bus.of("tool_result")
              if isinstance(e["result"], dict)
              and {"service", "status"} <= set(e["result"])]
    assert len(health) >= 2, "dashboard needs a before AND an after reading"
    assert health[0]["result"]["status"] == "degraded"
    assert health[-1]["result"]["status"] == "healthy"


# --- the sandbox tool description ------------------------------------------
def test_sandbox_description_carries_the_real_signatures():
    """
    A model that has to guess a signature guesses wrong. In a live run GPT-4o
    burned three sandbox attempts on `deploy_times=`, a missing positional, and
    a type error, and never produced the correlation score. The description is
    generated from the code, so this also guards against drift.
    """
    import inspect

    import diagnostics
    from agent.registry import SANDBOX_TOOL_SCHEMA

    desc = SANDBOX_TOOL_SCHEMA["function"]["description"]
    for fn in (diagnostics.correlate_deploy_to_incident,
               diagnostics.recommend_rollback_target):
        assert f"{fn.__name__}{inspect.signature(fn)}" in desc, \
            f"{fn.__name__}'s real signature is not shown to the model"


def test_sandbox_description_shows_a_runnable_example():
    from agent.registry import SANDBOX_EXAMPLE, SANDBOX_TOOL_SCHEMA
    assert SANDBOX_EXAMPLE in SANDBOX_TOOL_SCHEMA["function"]["description"]
    assert "PAYLOAD[" in SANDBOX_EXAMPLE and "RESULT =" in SANDBOX_EXAMPLE


def test_the_documented_example_actually_runs(demo_payload):
    """The example we hand the model must work verbatim in the real sandbox."""
    from agent.registry import SANDBOX_EXAMPLE
    from sandbox.runner import run_diagnostic

    out = run_diagnostic(SANDBOX_EXAMPLE, demo_payload)
    assert out["ok"], out["error"]
    assert out["result"]["suspect"] == "v1.4.2"
    assert out["result"]["rollback_target"] == "v1.4.1"
