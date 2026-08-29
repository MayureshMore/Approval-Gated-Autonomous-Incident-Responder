"""
Preflight.

This is the check you run to reassure yourself minutes before presenting, so it
must never cry wolf: "the build is wrong" and "you haven't started the servers"
are different situations and have to read differently.
"""
import subprocess
import sys

import pytest

import run_agent


def test_clean_build_with_env_up_has_no_problems(env):
    problems, not_started = run_agent.preflight()
    assert problems == [] and not_started == []


def test_unreachable_env_is_not_started_not_a_problem(monkeypatch):
    """Nothing listening is a setup step. Reporting it as a defect is what made
    a pre-demo sanity check look like a broken build."""
    import tools
    monkeypatch.setattr(tools, "BASE_URL", "http://127.0.0.1:1")
    problems, not_started = run_agent.preflight()
    assert problems == []
    assert len(not_started) == 1
    assert "is not running" in not_started[0]
    assert "make demo" in not_started[0], "tell the operator how to start it"


def test_a_missing_implementation_is_a_real_problem(env, monkeypatch):
    import agent.registry as reg
    monkeypatch.setattr(reg, "REQUIRES_APPROVAL", set(reg.REQUIRES_APPROVAL) | {"ghost_tool"})
    problems, not_started = run_agent.preflight()
    assert any("ghost_tool" in p for p in problems)
    assert not_started == []


def test_a_reachable_but_empty_env_is_a_real_problem(env, monkeypatch):
    """Reachable but no alerts means someone forgot /reset — actionable, and
    genuinely a problem, unlike a server that simply isn't up."""
    import tools
    monkeypatch.setattr(tools, "get_active_alerts", lambda: [])
    problems, not_started = run_agent.preflight()
    assert any("no active alerts" in p for p in problems)
    assert any("/reset" in p for p in problems), "say the command"
    assert not_started == []


def test_a_misbehaving_env_is_a_problem_not_a_missing_service(env, monkeypatch):
    import tools
    monkeypatch.setattr(tools, "get_active_alerts",
                        lambda: (_ for _ in ()).throw(ValueError("bad json")))
    problems, not_started = run_agent.preflight()
    assert any("misbehaving" in p for p in problems)
    assert not_started == []


def test_sandbox_checks_run_without_the_environment(monkeypatch):
    """The sandbox assertions must hold even with nothing else running."""
    problems, _ = run_agent.preflight(require_env=False)
    assert problems == []


# --- exit codes, because scripts and operators both read them --------------
def _selftest(base_url: str) -> subprocess.CompletedProcess:
    """Run the real CLI in a subprocess. MOCK_ENV_URL is how the URL crosses the
    process boundary — monkeypatching tools.BASE_URL cannot."""
    import os
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return subprocess.run(
        [sys.executable, "run_agent.py", "--selftest"], capture_output=True, text=True,
        cwd=repo, env={**os.environ, "MOCK_ENV_URL": base_url})


def test_selftest_exits_zero_when_only_a_service_is_down():
    out = _selftest("http://127.0.0.1:1")
    assert out.returncode == 0, "a stopped server must not read as a failed build"
    assert "SELFTEST: OK (wiring verified)" in out.stdout
    assert "SELFTEST: FAIL" not in out.stdout


def test_selftest_says_PASS_when_everything_is_up(mock_env):
    out = _selftest(mock_env)
    assert out.returncode == 0
    assert "SELFTEST: PASS" in out.stdout
