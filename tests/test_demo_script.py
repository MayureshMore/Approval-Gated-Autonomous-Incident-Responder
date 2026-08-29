"""
The demo launcher.

`scripts/demo.sh` is the thing we actually run in front of judges, so the parts
that could fail silently — killing someone else's server, starting the agent
before the environment answers, leaving processes behind — get checked here.
Bash is hard to unit-test, so these assert on structure and on the one behaviour
that is cheap to run for real (--help style flags and syntax).
"""
import os
import re
import stat
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "demo.sh")


@pytest.fixture(scope="module")
def source() -> str:
    with open(SCRIPT) as f:
        return f.read()


@pytest.fixture(scope="module")
def body(source) -> str:
    """Executable lines only — the header comment mentions the same identifiers
    as the code, which would make any ordering assertion meaningless."""
    return "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))


def test_script_exists_and_is_executable():
    assert os.path.exists(SCRIPT), "the demo launcher must be committed"
    assert os.stat(SCRIPT).st_mode & stat.S_IXUSR, "must be chmod +x or `make demo` fails"


def test_it_is_valid_bash():
    proc = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_fails_fast_on_error(source):
    """Without -e a failed server start would fall through to running the agent."""
    assert re.search(r"^set -euo pipefail", source, re.M)


def test_cleans_up_on_exit_and_interrupt(source):
    """Ctrl-C during a demo must not leave uvicorn holding the ports."""
    assert re.search(r"trap cleanup EXIT INT TERM", source)


def test_only_kills_processes_it_started(source):
    """A blanket pkill would kill a server the operator is running elsewhere."""
    assert "pkill" not in source, "pkill can kill processes this script did not start"
    assert re.search(r'kill "\$pid"', source)


def test_waits_for_both_services_before_starting_the_agent(body):
    """Starting the agent against a half-up environment is the classic demo fail."""
    calls = [l for l in body.splitlines() if l.startswith("wait_for ")]
    assert len(calls) == 2, f"expected a health wait per service, got {calls}"
    assert any("/alerts" in c for c in calls) and any("/events" in c for c in calls)
    assert body.index('wait_for "http') < body.index("run_agent.py")


def test_resets_the_scenario_for_determinism(body):
    assert body.count("/reset") == 2, "both the environment and the bus must be reset"
    assert body.rindex("/reset") < body.index("run_agent.py"), "reset before the run"


def test_refuses_to_start_on_an_occupied_port(source):
    """Better a clear error than two servers fighting over :8000 mid-demo."""
    assert "already in use" in source


def test_defaults_to_the_no_api_key_provider(source):
    """The default path must work with no credentials, as demo insurance."""
    assert "--provider sim" in source


def test_forwards_arguments_to_the_agent(source):
    """--provider truefoundry / --resume last must reach run_agent.py."""
    assert '"${@:---provider sim --subagents}"' in source


def test_agent_runs_in_background_so_ctrl_c_works_at_the_approval_gate(body):
    """
    Regression: bash defers traps until the running FOREGROUND command finishes.
    With the agent in the foreground, Ctrl-C during the 300s approval pause did
    nothing and both servers leaked — and pausing at the gate is precisely when
    the operator interrupts, since that is the Layer 4 demo.
    """
    assert "agent_pid=$!" in body, "the agent must be backgrounded"
    assert 'wait "$agent_pid"' in body, "wait is interruptible; a foreground call is not"
    assert '"$agent_pid" "$bus_pid" "$mock_pid"' in body, "cleanup must also stop the agent"


def test_missing_interpreter_gives_an_actionable_message(source):
    assert "uv venv" in source, "tell the operator how to fix it, not just that it broke"


# --- Makefile --------------------------------------------------------------
def test_makefile_targets_exist():
    with open(os.path.join(REPO, "Makefile")) as f:
        mk = f.read()
    for target in ("demo:", "demo-live:", "demo-resume:", "test:", "selftest:", "reset:"):
        assert target in mk, f"Makefile is missing the {target} target"


def test_makefile_declares_phony_targets():
    """Without .PHONY, a file named `test` would silently break `make test`."""
    with open(os.path.join(REPO, "Makefile")) as f:
        assert ".PHONY:" in f.read()
