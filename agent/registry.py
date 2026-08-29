"""
The agent's tool registry — one source of truth for what the agent can do.

`tools.py` (frozen by TOOL_CONTRACT.md) stays the environment client. This module
layers on the two capabilities that make the run a harness demo rather than a
chat wrapper:

  run_diagnostic  — Layer 1. Agent-written Python, executed in the sandbox.
  open_revert_pr  — Layer 2. A REAL GitHub revert PR, behind the approval gate.

It also emits schemas in both OpenAI and Anthropic dialects from the same
definitions, so switching provider never means editing a tool twice.
"""
import inspect
from typing import Any, Callable

import diagnostics
import tools
from sandbox.runner import run_diagnostic
from tool_schemas import TOOL_SCHEMAS as ENV_TOOL_SCHEMAS

def _diagnostics_api() -> str:
    """Render the real signatures of the sandbox helpers.

    Generated from the code rather than written by hand: a model that has to
    guess a signature guesses wrong. In a live gateway run GPT-4o burned three
    sandbox attempts on `deploy_times=`, then a missing positional, then a type
    error — and never produced the correlation score the demo is built around.
    Generating this also means the description can never drift from the code.
    """
    lines = []
    for fn in (diagnostics.correlate_deploy_to_incident,
               diagnostics.recommend_rollback_target):
        summary = (fn.__doc__ or "").strip().splitlines()[0]
        lines.append(f"  {fn.__name__}{inspect.signature(fn)}\n      {summary}")
    return "\n".join(lines)


SANDBOX_EXAMPLE = """from diagnostics import correlate_deploy_to_incident, recommend_rollback_target
corr = correlate_deploy_to_incident(
    PAYLOAD["alert_fired_at"],   # the alert's fired_at, verbatim
    PAYLOAD["deploys"],          # get_recent_deploys() output, unchanged
    PAYLOAD["error_logs"],       # get_logs() output, unchanged
)
target = recommend_rollback_target(PAYLOAD["deploys"], PAYLOAD["current_version"])
RESULT = {**corr, "rollback_target": target}"""


# --- extra schemas (same OpenAI shape as tool_schemas.py) -------------------
SANDBOX_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_diagnostic",
        "description": (
            "Execute a short Python diagnostic in an isolated sandbox to quantify a "
            "hypothesis instead of eyeballing tool output. Use this before proposing a "
            "remediation.\n\n"
            "Your inputs arrive as the dict PAYLOAD; return by assigning to RESULT. "
            "No network and no subprocesses are available.\n\n"
            "The module `diagnostics` is importable. Call these EXACTLY as shown — "
            "pass tool output through unchanged, do not reformat it:\n"
            f"{_diagnostics_api()}\n\n"
            "`deploys` must be the list of dicts from get_recent_deploys() and "
            "`error_logs` the list of log lines from get_logs(). Pass the raw lists, "
            "never a subagent's findings summary — if you only have subagent findings, "
            "use its `recent_deploys` and `lines` fields, or call the read-only tools "
            "again to get the raw output.\n\n"
            "Worked example:\n"
            f"{SANDBOX_EXAMPLE}"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source to execute."},
                "payload": {"type": "object",
                            "description": "JSON inputs, available inside as PAYLOAD. For "
                                           "the correlation helpers include alert_fired_at, "
                                           "deploys, error_logs and current_version, copied "
                                           "verbatim from the read-only tools."},
            },
            "required": ["code"],
        },
    },
}

GITHUB_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "open_revert_pr",
        "description": (
            "DESTRUCTIVE. Open a real revert pull request on the service's repository to "
            "undo the bad deploy. Requires human approval. Use alongside rollback_service "
            "when the fix must also land in source control."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "reason": {"type": "string",
                           "description": "Why the revert is warranted; goes in the PR body."},
            },
            "required": ["service", "reason"],
        },
    },
}


def _github_impl(service: str, reason: str) -> dict:
    # Imported lazily: no `gh` CLI on the machine must not break the whole registry.
    from integrations import github_ops
    return github_ops.rollback_via_github(service=service, reason=reason)


# --- registry --------------------------------------------------------------
# Gated set = the environment's destructive tools plus anything that touches the
# outside world. Derived from tools.REQUIRES_APPROVAL so the two never drift.
REQUIRES_APPROVAL: set[str] = set(tools.REQUIRES_APPROVAL) | {"open_revert_pr"}


def build_registry(include_github: bool = False) -> tuple[dict[str, Callable], list[dict]]:
    """Return (name -> impl, openai_schemas) for the tools this run may use."""
    impls: dict[str, Callable[..., Any]] = dict(tools.TOOLS)
    schemas = list(ENV_TOOL_SCHEMAS)

    impls["run_diagnostic"] = run_diagnostic
    schemas.append(SANDBOX_TOOL_SCHEMA)

    if include_github:
        impls["open_revert_pr"] = _github_impl
        schemas.append(GITHUB_TOOL_SCHEMA)

    return impls, schemas


def to_anthropic(openai_schemas: list[dict]) -> list[dict]:
    """Translate OpenAI function schemas into Anthropic tool blocks."""
    return [
        {
            "name": s["function"]["name"],
            "description": s["function"]["description"],
            "input_schema": s["function"]["parameters"],
        }
        for s in openai_schemas
    ]


def is_gated(name: str) -> bool:
    return name in REQUIRES_APPROVAL


def audit() -> dict:
    """Self-check that every declared tool has an implementation, and vice versa.

    Called at startup: a tool the model can name but we cannot run turns into a
    confusing mid-demo failure, so we'd rather refuse to start.
    """
    impls, schemas = build_registry(include_github=True)
    declared = {s["function"]["name"] for s in schemas}
    implemented = set(impls)
    return {
        "declared": sorted(declared),
        "gated": sorted(REQUIRES_APPROVAL),
        "missing_impl": sorted(declared - implemented),
        "undeclared_impl": sorted(implemented - declared),
        "gated_without_impl": sorted(REQUIRES_APPROVAL - implemented),
    }
