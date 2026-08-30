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
import shutil
import stat
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "demo.sh")


@pytest.fixture(scope="module")
def source() -> str:
    with open(SCRIPT, encoding="utf-8") as f:
        return f.read()


def _agent_invocation(body: str) -> int:
    """Offset of the line that actually starts the agent.

    Not the first mention of run_agent.py — the AUTO_APPROVE warning names it
    too, and anchoring on that silently inverts every ordering assertion.
    """
    for line in body.splitlines():
        if "-u run_agent.py" in line:
            return body.index(line)
    raise AssertionError("no agent invocation found in the launcher")


@pytest.fixture(scope="module")
def body(source) -> str:
    """Executable lines only — the header comment mentions the same identifiers
    as the code, which would make any ordering assertion meaningless."""
    return "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))


def test_script_exists_and_is_executable():
    assert os.path.exists(SCRIPT), "the demo launcher must be committed"

    # Assert on the mode git has recorded, not the one on disk. That is what
    # every other clone actually receives, and it also catches the bit being set
    # locally but never committed — which a bare os.stat() here would pass.
    # (It is also the only workable check on Windows: NTFS cannot represent the
    # exec bit, so os.stat() fails there even when the committed mode is right.)
    proc = subprocess.run(["git", "ls-files", "-s", "--", "scripts/demo.sh"],
                          cwd=REPO, capture_output=True, text=True)
    if proc.returncode == 0 and proc.stdout.strip():
        mode = proc.stdout.split()[0]
        assert mode == "100755", (
            f"must be committed chmod +x or `make demo` fails (git mode is {mode}; "
            "fix with: git update-index --chmod=+x scripts/demo.sh)")
    elif os.name == "nt":
        pytest.skip("not a git checkout, and NTFS cannot represent the exec bit")
    else:
        assert os.stat(SCRIPT).st_mode & stat.S_IXUSR, "must be chmod +x or `make demo` fails"


def test_it_is_valid_bash():
    if shutil.which("bash") is None:
        pytest.skip("bash not installed")
    # Run from the repo with a relative path: git-bash on Windows cannot resolve
    # a native C:\... argument, which failed this for reasons unrelated to the
    # script's syntax.
    proc = subprocess.run(["bash", "-n", "scripts/demo.sh"], cwd=REPO,
                          capture_output=True, text=True)
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
    assert body.index('wait_for "http') < _agent_invocation(body)


def test_resets_the_scenario_for_determinism(body):
    assert body.count("/reset") == 2, "both the environment and the bus must be reset"
    assert body.rindex("/reset") < _agent_invocation(body), "reset before the run"


def test_resume_does_not_reset_the_scenario(body):
    """
    Resetting on resume would clear the bus and re-break the service underneath a
    run that is mid-remediation — destroying the continuous timeline that the
    session-survival demo exists to show.
    """
    assert 'resuming="yes"' in body and '"$resuming" = "yes"' in body
    guard = body.index('if [ "$resuming" = "yes" ]')
    assert guard < body.rindex("/reset"), "the reset must sit behind the resume guard"


def test_refuses_to_start_on_an_occupied_port(source):
    """Better a clear error than two servers fighting over :8000 mid-demo."""
    assert "already in use" in source


def test_defaults_to_the_no_api_key_provider(body):
    """The default path must work with no credentials, as demo insurance."""
    assert "--provider sim" in body


def test_forwards_arguments_to_the_agent(body):
    """--provider truefoundry / --resume last must reach run_agent.py."""
    assert '-u run_agent.py "$@"' in body


def test_default_arguments_are_separate_argv_elements(body):
    """
    Regression: "${@:-a b c}" expands to ONE argument, so argparse rejected the
    documented no-argument invocation. Set the positional parameters instead.
    """
    assert '"${@:-' not in body, 'a quoted ${@:-default} collapses into one argv element'
    assert 'set -- --provider sim --subagents' in body


def test_does_not_inherit_auto_approve(body):
    """
    The launcher tells the operator to approve in the dashboard, so it must not
    inherit AUTO_APPROVE=1 from the shell or .env — that would execute every
    destructive action with no human while the UI still claims to be gating.
    """
    assert "unset AUTO_APPROVE" in body
    assert body.index("unset AUTO_APPROVE") < _agent_invocation(body)


def test_port_probe_does_not_depend_on_an_undeclared_tool(body):
    """
    `! nc -z ...` reports command-not-found as success, so a machine without nc
    would call every port free and run against somebody else's server.
    """
    assert "nc -z" not in body, "nc is not a declared prerequisite"
    assert "connect_ex" in body, "probe with the interpreter we already validated"


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
    with open(os.path.join(REPO, "Makefile"), encoding="utf-8") as f:
        mk = f.read()
    for target in ("demo:", "demo-live:", "demo-resume:", "test:", "selftest:", "reset:"):
        assert target in mk, f"Makefile is missing the {target} target"


def test_makefile_declares_phony_targets():
    """Without .PHONY, a file named `test` would silently break `make test`."""
    with open(os.path.join(REPO, "Makefile"), encoding="utf-8") as f:
        assert ".PHONY:" in f.read()
