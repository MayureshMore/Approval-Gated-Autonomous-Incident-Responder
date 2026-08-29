"""
Sandbox runner — the parent half of agent-written code execution (Layer 1 / DGX #2).

The agent doesn't just call canned tools: it WRITES a diagnostic snippet and we
run it here, isolated, then it cites the computed result in its reasoning.

Isolation is real, not decorative:
  * separate OS process (a crash or hang can't take the agent down)
  * throwaway working directory, so the snippet cannot see the repo
  * scrubbed environment — no API keys reachable from inside
  * wall-clock timeout with SIGKILL, on top of the child's own RLIMIT_CPU
  * network, subprocess and dangerous imports blocked (see bootstrap.py)

`diagnostics.py` is copied into the working directory so the snippet can import
the correlation helpers without reaching outside the sandbox.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
BOOTSTRAP = os.path.join(_HERE, "bootstrap.py")
VENDORED = ("diagnostics.py",)

DEFAULT_TIMEOUT = 10
DEFAULT_LIMITS = {"cpu_seconds": 5, "memory_mb": 256}


class SandboxResult(dict):
    """dict subclass so it serialises straight into a tool_result event."""

    @property
    def ok(self) -> bool:
        return bool(self.get("ok"))


def run_python(
    code: str,
    payload: Optional[dict] = None,
    timeout: int = DEFAULT_TIMEOUT,
    limits: Optional[dict] = None,
) -> SandboxResult:
    """
    Execute `code` in the sandbox.

    The snippet reads its inputs from the injected `PAYLOAD` dict and returns by
    assigning to `RESULT`. Anything it prints comes back as `stdout`.
    """
    started = time.time()
    workdir = tempfile.mkdtemp(prefix="ir-sandbox-")
    try:
        for name in VENDORED:
            src = os.path.join(_REPO, name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(workdir, name))

        request = json.dumps({
            "code": code,
            "payload": payload or {},
            "limits": {**DEFAULT_LIMITS, **(limits or {})},
        })

        # Minimal env: no API keys, no PYTHONPATH back into the repo.
        env = {"PATH": "/usr/bin:/bin", "HOME": workdir, "PYTHONIOENCODING": "utf-8",
               "PYTHONDONTWRITEBYTECODE": "1"}

        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-S", BOOTSTRAP],
                input=request, capture_output=True, text=True,
                cwd=workdir, env=env, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                ok=False, result=None, stdout="",
                error=f"sandbox timeout after {timeout}s",
                duration_ms=int((time.time() - started) * 1000),
            )

        out = _decode(proc)
        out["duration_ms"] = int((time.time() - started) * 1000)
        return SandboxResult(**out)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _decode(proc: subprocess.CompletedProcess) -> dict:
    """Turn the child's stdout back into a result, tolerating a hard crash."""
    raw = (proc.stdout or "").strip()
    if not raw:
        killed = proc.returncode and proc.returncode < 0
        reason = ("killed by signal %d (resource limit)" % -proc.returncode) if killed \
            else (proc.stderr or "").strip()[-500:] or "no output from sandbox"
        return {"ok": False, "result": None, "stdout": "", "error": reason}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "result": None, "stdout": raw[:1000],
                "error": "sandbox produced non-JSON output"}


# --- the agent-facing tool -------------------------------------------------
def run_diagnostic(code: str, payload: Optional[dict] = None) -> dict:
    """
    Agent tool. Run a short Python diagnostic in the sandbox.

    `diagnostics` is importable; inputs arrive as PAYLOAD; assign RESULT to return.
    """
    res = run_python(code, payload=payload)
    # Keep the shape flat and small — this goes back into the model's context.
    out = {
        "ok": res.get("ok"),
        "result": res.get("result"),
        "stdout": (res.get("stdout") or "")[:1500],
        "error": res.get("error"),
        "duration_ms": res.get("duration_ms"),
        "sandboxed": True,
    }
    # Only present when the snippet ran clean but returned nothing, which
    # otherwise reads as success and sends the model on with no evidence.
    if res.get("hint"):
        out["hint"] = res["hint"]
    return out


# The snippet the agent is expected to converge on. Also used by the sim provider
# and by the tests, so "what we demo" and "what we test" are the same code path.
REFERENCE_DIAGNOSTIC = '''
from diagnostics import correlate_deploy_to_incident, recommend_rollback_target

corr = correlate_deploy_to_incident(
    PAYLOAD["alert_fired_at"], PAYLOAD["deploys"], PAYLOAD["error_logs"]
)
target = recommend_rollback_target(PAYLOAD["deploys"], PAYLOAD.get("current_version"))
print(f"suspect={corr['suspect']} score={corr['suspicion_score']} -> rollback to {target}")
RESULT = {**corr, "rollback_target": target}
'''.strip()
