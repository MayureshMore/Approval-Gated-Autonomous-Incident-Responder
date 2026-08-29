"""
The incident-response loop.

investigate (read-only) -> diagnose in the sandbox -> propose ONE remediation
-> PAUSE for a human -> execute -> confirm recovery -> report.

Everything provider-specific lives in agent/providers; everything the human sees
goes through agent/bus; nothing destructive gets past agent/approval. This file
is just the orchestration, and it is deliberately boring so it never surprises us
on stage.
"""
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from agent.approval import ApprovalGate
from agent.bus import EventBus
from agent.providers.base import ToolCall
from agent.registry import build_registry, is_gated
from agent.session import FAILED, FINISHED, RUNNING, SessionStore

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MAX_STEPS = 16
KICKOFF = "A production alert just fired. Investigate the root cause and resolve it."


def load_system_prompt() -> str:
    with open(os.path.join(_REPO, "agent_prompt.md")) as f:
        return f.read()


@dataclass
class RunReport:
    run_id: str
    provider: str
    steps: int = 0
    tool_calls: list[str] = field(default_factory=list)
    approvals: list[dict] = field(default_factory=list)
    sandbox_runs: int = 0
    final_message: str = ""
    events: list[dict] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def gated_actions(self) -> list[str]:
        return [a["action"] for a in self.approvals]

    @property
    def executed_destructive(self) -> list[str]:
        return [a["action"] for a in self.approvals if a["approved"]]


class IncidentAgent:
    def __init__(
        self,
        provider,
        bus: Optional[EventBus] = None,
        gate: Optional[ApprovalGate] = None,
        include_github: bool = False,
        max_steps: int = DEFAULT_MAX_STEPS,
        use_subagents: bool = False,
        session: Optional[SessionStore] = None,
    ):
        self.provider = provider
        self.bus = bus or EventBus()
        self.gate = gate or ApprovalGate(self.bus)
        self.max_steps = max_steps
        self.use_subagents = use_subagents
        self.include_github = include_github
        self.session = session
        self.impls, self.schemas = build_registry(include_github=include_github)
        self.report = RunReport(run_id=self.bus.run_id, provider=getattr(provider, "name", "?"))
        self._denied: set[tuple] = set()
        self._pending: list[ToolCall] = []   # calls requested but not yet completed
        self._rationale = ""
        self._scenario = ""

    # -- entry point --------------------------------------------------------
    def run(self, scenario: str = "checkout-service incident") -> RunReport:
        self._scenario = scenario
        self.bus.emit("run_started", scenario=scenario,
                      provider=self.report.provider,
                      approval_mode=self.gate.mode,
                      tools=[s["function"]["name"] for s in self.schemas])

        kickoff = KICKOFF
        if self.use_subagents:
            kickoff += "\n\n" + self._run_subagents()

        self.provider.start(load_system_prompt(), kickoff)
        self._checkpoint()
        return self._drive()

    def resume(self, state: dict) -> RunReport:
        """Continue a run that lost its process. Same run_id, same timeline."""
        self._scenario = state.get("scenario", "")
        self.report.steps = state.get("step", 0)
        # Copy, don't alias: the caller's state dict must not mutate as we run.
        self.report.tool_calls = list(state.get("tool_calls", []))
        self.report.approvals = list(state.get("approvals", []))
        self.report.sandbox_runs = state.get("sandbox_runs", 0)
        self._denied = {tuple(d) for d in state.get("denied", [])}
        self._rationale = state.get("rationale", "")
        self._pending = [ToolCall(c["id"], c["name"], c.get("args", {}))
                         for c in state.get("pending_calls", [])]
        self.provider.restore(state.get("provider_state", {}))

        self.bus.emit("run_resumed", scenario=self._scenario, step=self.report.steps,
                      pending=[c.name for c in self._pending])
        return self._drive()

    def _drive(self) -> RunReport:
        try:
            self._loop()
        except Exception as exc:
            self.report.error = f"{type(exc).__name__}: {exc}"
            self.bus.emit("error", message=self.report.error)
        finally:
            self.bus.emit("run_finished", steps=self.report.steps,
                          destructive_executed=self.report.executed_destructive,
                          error=self.report.error)
            self.report.events = list(self.bus.events)
            self._checkpoint(FAILED if self.report.error else FINISHED)
        return self.report

    # -- checkpointing (Layer 4) --------------------------------------------
    def _checkpoint(self, status: str = RUNNING) -> None:
        if not self.session:
            return
        try:
            self.session.save({
                "run_id": self.report.run_id,
                "status": status,
                "scenario": self._scenario,
                "provider": self.report.provider,
                "provider_state": self.provider.snapshot(),
                "step": self.report.steps,
                "tool_calls": self.report.tool_calls,
                "approvals": self.report.approvals,
                "sandbox_runs": self.report.sandbox_runs,
                "denied": [list(d) for d in self._denied],
                "rationale": self._rationale,
                "pending_calls": [{"id": c.id, "name": c.name, "args": c.args}
                                  for c in self._pending],
                "final_message": self.report.final_message,
                "error": self.report.error,
                "include_github": self.include_github,
                "events": self.bus.events,
            })
        except Exception as exc:
            # A failing checkpoint must never take down a live incident response.
            print(f"[session] checkpoint failed: {exc}")

    # -- the loop -----------------------------------------------------------
    def _loop(self) -> None:
        while self.report.steps < self.max_steps:
            # A resumed run finishes the calls it was midway through before it
            # asks the model for anything new.
            if not self._pending:
                self.report.steps += 1
                turn = self.provider.step(self.schemas)

                if turn.text:
                    self.bus.emit("agent_message", text=turn.text)

                if not turn.wants_tools:
                    self.report.final_message = turn.text
                    return

                self._pending = list(turn.tool_calls)
                self._rationale = turn.text
                self._checkpoint()

            for call in list(self._pending):
                result = self._handle(call, rationale=self._rationale)
                self.provider.record_tool_result(call, result)
                self._pending = [c for c in self._pending if c.id != call.id]
                self._checkpoint()

        self.bus.emit("agent_message",
                      text=f"Step budget ({self.max_steps}) exhausted; stopping.")

    def _handle(self, call, rationale: str) -> Any:
        if call.name not in self.impls:
            return {"ok": False, "error": f"unknown tool '{call.name}'"}

        if is_gated(call.name):
            verdict = self._gate(call, rationale)
            if verdict is not None:
                self.bus.emit("tool_result", tool=call.name, result=verdict)
                return verdict

        self.bus.emit("tool_call", tool=call.name, args=_trim_args(call.args))
        result = self._execute(call)
        self.bus.emit("tool_result", tool=call.name, result=_trim_result(call.name, result))
        return result

    def _gate(self, call, rationale: str) -> Optional[dict]:
        """Return a denial result to feed back, or None if cleared to execute."""
        fingerprint = (call.name, json.dumps(call.args, sort_keys=True, default=str))
        if fingerprint in self._denied:
            # Do not re-prompt a human who already said no to exactly this.
            return {"ok": False, "rejected": True,
                    "message": "Already denied in this run. Do not retry; change approach "
                               "or escalate to a human owner."}

        reason = rationale.strip() or "no rationale given"
        decision = self.gate.request(call.name, call.args, reason)
        self.report.approvals.append({
            "action": call.name, "args": call.args,
            "approved": decision.approved, "by": decision.by, "reason": decision.reason,
        })
        if decision.approved:
            return None

        self._denied.add(fingerprint)
        return {"ok": False, "rejected": True, "decided_by": decision.by,
                "message": f"Denied by {decision.by} ({decision.reason}). Do not retry this "
                           f"action; reconsider or hand off to a human."}

    def _execute(self, call) -> Any:
        if call.name == "run_diagnostic":
            self.report.sandbox_runs += 1
            self.bus.emit("sandbox_exec", code=call.args.get("code", ""),
                          payload_keys=sorted((call.args.get("payload") or {}).keys()))
        self.report.tool_calls.append(call.name)
        try:
            return self.impls[call.name](**call.args)
        except TypeError as exc:
            return {"ok": False, "error": f"bad arguments for {call.name}: {exc}"}
        except Exception as exc:
            # A dead endpoint must come back as data the model can react to,
            # not as a traceback that ends the run.
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # -- subagents (Layer: parallel investigation) --------------------------
    def _run_subagents(self) -> str:
        from agent.subagents import investigate_in_parallel
        findings = investigate_in_parallel(self.bus, self.impls)
        return ("Parallel investigation already completed by three subagents. "
                "Use these findings as your starting evidence:\n"
                + json.dumps(findings, indent=2, default=str))


# -- keep the model's context (and the dashboard) readable -------------------
def _trim_args(args: dict) -> dict:
    out = dict(args)
    if isinstance(out.get("code"), str) and len(out["code"]) > 600:
        out["code"] = out["code"][:600] + "\n# …truncated for display"
    return out


def _trim_result(name: str, result: Any) -> Any:
    if isinstance(result, list) and len(result) > 25:
        return result[:25] + [{"_truncated": len(result) - 25}]
    return result
