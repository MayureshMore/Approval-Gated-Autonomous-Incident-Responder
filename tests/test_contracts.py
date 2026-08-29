"""
Contract conformance — the seams with Person B.

TOOL_CONTRACT.md and EVENT_CONTRACT.md are frozen. If someone renames a metric
field or invents an event kind, the dashboard silently stops rendering and we
find out on stage. These tests turn that into a red build instead.
"""
import inspect
import os

import pytest

import tools
from agent.bus import CONTRACT_KINDS, EXTENDED_KINDS

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from agent.registry import APPROVAL_REASON_PARAM, REQUIRES_APPROVAL, build_registry


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
    """Every declared parameter must exist on the implementation's signature.

    `reason` is the one exception: the harness injects it onto gated tools to
    carry the model's justification to the human, and core.py strips it before
    dispatch. test_harness_reason_never_reaches_a_tool covers that seam.
    """
    impls, schemas = build_registry(include_github=True)
    for s in schemas:
        name = s["function"]["name"]
        declared = set(s["function"]["parameters"].get("properties", {}))
        params = inspect.signature(impls[name]).parameters
        accepts_kwargs = any(p.kind == p.VAR_KEYWORD for p in params.values())
        if not accepts_kwargs:
            declared -= {APPROVAL_REASON_PARAM} - set(params)
            assert declared <= set(params), f"{name}: schema declares {declared - set(params)}"


def test_every_gated_tool_demands_a_reason():
    """A human cannot approve what the agent has not explained."""
    _, schemas = build_registry(include_github=True)
    gated = [s["function"] for s in schemas if s["function"]["name"] in REQUIRES_APPROVAL]
    assert gated, "no gated tools in the registry"
    for fn in gated:
        params = fn["parameters"]
        assert APPROVAL_REASON_PARAM in params["properties"], f"{fn['name']} has no reason"
        assert APPROVAL_REASON_PARAM in params["required"], f"{fn['name']}: reason is optional"


def test_read_only_tools_are_not_burdened_with_a_reason():
    _, schemas = build_registry(include_github=True)
    for s in schemas:
        fn = s["function"]
        if fn["name"] not in REQUIRES_APPROVAL:
            assert APPROVAL_REASON_PARAM not in fn["parameters"].get("properties", {}), fn["name"]


def test_open_revert_pr_keeps_its_own_reason():
    """It declares `reason` itself and puts it in the PR body — the harness must
    not shadow it with a second definition or drop it from `required`."""
    _, schemas = build_registry(include_github=True)
    fn = next(s["function"] for s in schemas if s["function"]["name"] == "open_revert_pr")
    assert "PR body" in fn["parameters"]["properties"][APPROVAL_REASON_PARAM]["description"]
    assert fn["parameters"]["required"].count(APPROVAL_REASON_PARAM) == 1


def test_harness_reason_never_reaches_a_tool():
    """The injected `reason` must be stripped before dispatch — except on
    open_revert_pr, whose implementation genuinely takes one."""
    from agent.core import _impl_accepts

    impls, schemas = build_registry(include_github=True)
    for s in schemas:
        name = s["function"]["name"]
        if name not in REQUIRES_APPROVAL:
            continue
        accepts = _impl_accepts(impls[name], APPROVAL_REASON_PARAM)
        assert accepts == (name == "open_revert_pr"), (
            f"{name}: impl accepts reason={accepts}; core.py strips based on this, "
            "so a mismatch means the call dies on an unexpected keyword")


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


def test_rollback_to_an_unknown_version_does_not_fix_it(env):
    """
    Picking the right *tool* is not enough; the version has to be right too.
    The environment used to heal on any string at all, so a rollback to a
    release that never existed still reported healthy — rewarding a wrong
    diagnosis and hollowing out the claim that the scenario forces reasoning.
    """
    result = tools.rollback_service("checkout-service", "v9.9.9-never-shipped")
    assert result["ok"] is False
    m = tools.get_metrics("checkout-service")
    assert m["status"] == "degraded" and m["version"] == "v1.4.2"


def test_rollback_to_the_bad_version_itself_does_not_fix_it(env):
    """Rolling back onto the release that caused the incident is a no-op."""
    result = tools.rollback_service("checkout-service", "v1.4.2")
    assert result["ok"] is False
    m = tools.get_metrics("checkout-service")
    assert m["status"] == "degraded" and m["version"] == "v1.4.2"


def test_rollback_to_the_previous_good_version_does_fix_it(env):
    """The one correct answer still has to work — this is the demo path."""
    result = tools.rollback_service("checkout-service", "v1.4.1")
    assert result["ok"] is True
    m = tools.get_metrics("checkout-service")
    assert m["status"] == "healthy" and m["version"] == "v1.4.1"


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

    def squash(text: str) -> str:
        """inspect renders `x: T = None`, ast renders `x: T=None`. Same signature."""
        return "".join(text.split())

    desc = squash(SANDBOX_TOOL_SCHEMA["function"]["description"])
    for fn in (diagnostics.correlate_deploy_to_incident,
               diagnostics.recommend_rollback_target):
        assert squash(f"{fn.__name__}{inspect.signature(fn)}") in desc, \
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


def test_registry_does_not_import_the_sandbox_module():
    """
    `diagnostics` is sandbox-destined code; the parent process must never
    execute it. Signatures are read with `ast`, not by importing. Today the
    module only defines functions — the point of the sandbox is not having to
    keep re-checking that.
    """
    import subprocess
    import sys

    probe = (
        "import sys; sys.path.insert(0, '.');"
        "import agent.registry;"
        "print('diagnostics' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, cwd=REPO_ROOT)
    assert out.stdout.strip() == "False", "importing agent.registry executed diagnostics.py"


def test_signature_extraction_fails_loudly_if_a_helper_disappears(tmp_path):
    """Silently dropping a helper from the description would send the model back
    to guessing — the exact failure this whole mechanism exists to prevent."""
    from agent.registry import _diagnostics_api

    stub = tmp_path / "diagnostics.py"
    stub.write_text("def something_else():\n    pass\n")
    with pytest.raises(RuntimeError, match="no longer defines"):
        _diagnostics_api(str(stub))


# --- the environment must not confirm a nonsensical destructive action ------
# Same reasoning as the rollback version guard: if /scale answers "ok" to
# anything, a wrong diagnosis still looks like a success.
def test_scale_rejects_replica_counts_below_one(env):
    before = tools.get_metrics("checkout-service")["replicas"]
    for bad in (-5, 0):
        r = tools.scale_service("checkout-service", bad)
        assert r["ok"] is False, f"scale to {bad} was accepted"
        assert "at least 1" in r["message"]
    assert tools.get_metrics("checkout-service")["replicas"] == before


def test_scale_rejects_an_absurd_replica_count(env):
    before = tools.get_metrics("checkout-service")["replicas"]
    r = tools.scale_service("checkout-service", 10 ** 9)
    assert r["ok"] is False
    assert "caps a service" in r["message"]
    assert tools.get_metrics("checkout-service")["replicas"] == before


def test_scale_still_works_for_sane_values(env):
    r = tools.scale_service("checkout-service", 6)
    assert r["ok"] is True
    assert tools.get_metrics("checkout-service")["replicas"] == 6


def test_a_refused_scale_reports_the_replica_count_that_is_still_live(env):
    """The agent reads `replicas` back — it must be the truth, not the ask."""
    tools.scale_service("checkout-service", 5)
    r = tools.scale_service("checkout-service", 0)
    assert r["replicas"] == 5, "a refusal reported the requested count as if applied"
