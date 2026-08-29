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


# --- a clean run that returns nothing must not look like success ------------
def test_lowercase_result_is_called_out_by_name():
    """`result = ...` silently vanishes; ok=true with a null result reads as
    success and sends the model on with no evidence. Say so instead."""
    out = run_diagnostic("result = {'suspicion': 0.9}")

    assert out["ok"] is True
    assert out["result"] is None
    assert "RESULT" in out["hint"]
    assert "lowercase" in out["hint"]


def test_forgetting_to_assign_at_all_is_called_out():
    out = run_diagnostic("1 + 1")

    assert out["ok"] is True
    assert "RESULT = " in out["hint"]
    assert "lowercase" not in out["hint"]


def test_a_real_result_carries_no_hint():
    out = run_diagnostic("RESULT = {'suspicion': 0.9}")

    assert out["result"] == {"suspicion": 0.9}
    assert "hint" not in out


def test_a_falsy_but_real_result_is_not_mistaken_for_nothing():
    """RESULT = 0 / [] / "" are answers, not omissions."""
    for code, expected in (("RESULT = 0", 0), ("RESULT = []", []), ("RESULT = ''", "")):
        out = run_diagnostic(code)
        assert out["result"] == expected, code
        assert "hint" not in out, f"{code} was wrongly flagged as returning nothing"


# --- an error the agent can act on -----------------------------------------
def test_error_names_the_failing_line():
    """A bare "TypeError: list indices..." cost a live run an extra sandbox
    round-trip. The message must say where."""
    out = run_diagnostic('a = [1, 2]\nRESULT = a["key"]\n')

    assert out["ok"] is False
    assert "TypeError" in out["error"]
    assert "line 2" in out["error"]
    assert 'a["key"]' in out["error"]


def test_error_shows_the_real_payload_shape():
    """The usual mistake is reaching into PAYLOAD with a key that is not there."""
    out = run_diagnostic('RESULT = PAYLOAD["deploys"]["alert_fired_at"]',
                         {"alert_fired_at": "2026-01-01T00:00:00+00:00",
                          "deploys": [{"version": "v1"}]})

    assert "PAYLOAD contains" in out["error"]
    assert "alert_fired_at: str" in out["error"]
    assert "deploys: list[1]" in out["error"]


def test_payload_shape_is_omitted_for_unrelated_errors():
    """Only lookup/shape errors get the PAYLOAD dump; noise helps nobody."""
    out = run_diagnostic("RESULT = 1 / 0", {"deploys": []})

    assert "ZeroDivisionError" in out["error"]
    assert "PAYLOAD contains" not in out["error"]


def test_a_syntax_error_still_reports_cleanly():
    out = run_diagnostic("def broken(:\n  pass")

    assert out["ok"] is False
    assert "SyntaxError" in out["error"]
