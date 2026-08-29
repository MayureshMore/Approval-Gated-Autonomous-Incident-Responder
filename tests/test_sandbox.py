"""
The sandbox has to be a real boundary, not a claim we make on stage.

Each test here is one sentence of the pitch, made checkable.
"""
from sandbox.runner import REFERENCE_DIAGNOSTIC, run_diagnostic, run_python


def test_runs_agent_written_code():
    res = run_python("RESULT = sum(PAYLOAD['xs'])", {"xs": [1, 2, 3]})
    assert res["ok"] and res["result"] == 6


def test_captures_stdout_and_timing():
    res = run_python("print('hello from the sandbox')\nRESULT = 1")
    assert "hello from the sandbox" in res["stdout"]
    assert res["duration_ms"] >= 0


def test_diagnostics_module_is_importable(demo_payload):
    out = run_diagnostic(REFERENCE_DIAGNOSTIC, demo_payload)
    assert out["ok"] and out["sandboxed"] is True
    assert out["result"]["suspect"] == "v1.4.2"
    assert out["result"]["rollback_target"] == "v1.4.1"
    assert out["result"]["suspicion_score"] >= 0.7


# --- the boundary ----------------------------------------------------------
def test_network_is_blocked():
    assert "socket" in (run_python("import socket\nRESULT=1")["error"] or "")


def test_urllib_is_blocked():
    assert run_python("import urllib.request\nRESULT=1")["error"]


def test_subprocess_is_blocked():
    assert "subprocess" in (run_python("import subprocess\nRESULT=1")["error"] or "")


def test_os_system_is_blocked():
    res = run_python("import os\nRESULT = os.system('echo pwned')")
    assert not res["ok"]


def test_cannot_see_the_repository():
    res = run_python("import os\nRESULT = sorted(os.listdir('.'))")
    assert res["result"] == ["diagnostics.py"]      # nothing else is reachable
    assert "tools.py" not in res["result"]


def test_secrets_are_not_in_the_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-never-leak")
    res = run_python("import os\nRESULT = os.environ.get('OPENAI_API_KEY')")
    assert res["result"] is None


def test_infinite_loop_is_killed():
    res = run_python("while True: pass", timeout=3)
    assert not res["ok"] and "timeout" in res["error"]


def test_memory_bomb_does_not_take_down_the_parent():
    res = run_python("RESULT = bytearray(4 * 1024 * 1024 * 1024)", timeout=8)
    assert not res["ok"]                 # died in the child; we are still here


# --- failure surfaces as data, never as a crash ----------------------------
def test_syntax_error_is_reported():
    res = run_python("def broken(:\n  pass")
    assert not res["ok"] and "SyntaxError" in res["error"]


def test_exception_is_reported():
    res = run_python("RESULT = 1 / 0")
    assert not res["ok"] and "ZeroDivisionError" in res["error"]


def test_unserialisable_result_is_rejected_not_raised():
    res = run_python("RESULT = object()")
    assert not res["ok"] and res["error"]


def test_missing_result_is_none_not_an_error():
    res = run_python("x = 5")
    assert res["ok"] and res["result"] is None
