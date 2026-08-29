"""
Simulated provider — a deterministic scripted incident response.

This is not a toy: it is the demo's insurance policy and the tests' fixture. It
drives the exact same loop, gate and sandbox as a real model, so the approval
pause, the sandboxed diagnostic and the recovery check are all genuinely
exercised — only the token generation is replaced by a fixed plan.

Use it when there is no API key, when the network is hostile, or in CI.
"""
import json
from typing import Any

from sandbox.runner import REFERENCE_DIAGNOSTIC

from .base import AssistantTurn, ToolCall


class SimProvider:
    name = "sim"

    def __init__(self, service: str = "checkout-service"):
        self.service = service
        self.turn = 0
        self.seen: dict[str, Any] = {}
        self._rejected = False
        self._denied_by = ""

    def start(self, system: str, user: str) -> None:  # noqa: ARG002 - fixed script
        self.turn = 0

    # A failing tool returns {"ok": false, "error": ...} where the script expects
    # a list or a dict of metrics. Coerce at the edge so a dead environment
    # degrades the investigation instead of crashing the run.
    def _list(self, tool: str) -> list:
        v = self.seen.get(tool)
        return v if isinstance(v, list) else []

    def _dict(self, tool: str) -> dict:
        v = self.seen.get(tool)
        return v if isinstance(v, dict) and not v.get("error") else {}

    def record_tool_result(self, call: ToolCall, result: Any) -> None:
        self.seen[call.name] = result
        if call.name in ("rollback_service", "restart_service", "scale_service") \
                and isinstance(result, dict) and result.get("rejected"):
            self._rejected = True
            # Who actually said no. A denial from the gate failing closed (dead
            # bus, timeout, no tty) is NOT a human decision, and claiming it was
            # is the agent misreporting provenance — on a project whose whole
            # thesis is honest human-in-the-loop, that is the worst sentence we
            # could put on screen.
            self._denied_by = str(result.get("decided_by") or "")

    # -- session persistence (Layer 4) --------------------------------------
    def snapshot(self) -> dict:
        return {"turn": self.turn, "seen": self.seen, "service": self.service,
                "rejected": self._rejected, "denied_by": self._denied_by}

    def restore(self, state: dict) -> None:
        self.turn = state.get("turn", 0)
        self.seen = state.get("seen", {})
        self.service = state.get("service", self.service)
        self._rejected = state.get("rejected", False)
        self._denied_by = state.get("denied_by", "")

    # -- the script ---------------------------------------------------------
    def step(self, schemas: list[dict]) -> AssistantTurn:  # noqa: ARG002
        self.turn += 1
        available = {s["function"]["name"] for s in schemas}
        plan = [
            self._t1_alerts, self._t2_metrics, self._t3_logs, self._t4_deploys,
            self._t5_sandbox, self._t6_propose, self._t7_verify, self._t8_report,
        ]
        if self.turn > len(plan):
            return AssistantTurn(text="Investigation complete.")
        return plan[self.turn - 1](available)

    def _call(self, name: str, **args) -> AssistantTurn:
        return AssistantTurn(tool_calls=[ToolCall(id=f"sim-{self.turn}", name=name, args=args)])

    def _t1_alerts(self, _):
        return AssistantTurn(text="Pulling active alerts.",
                             tool_calls=[ToolCall(f"sim-{self.turn}", "get_active_alerts", {})])

    def _t2_metrics(self, _):
        alerts = self._list("get_active_alerts")
        if alerts:
            self.service = alerts[0].get("service", self.service)
        return self._call("get_metrics", service=self.service)

    def _t3_logs(self, _):
        return self._call("get_logs", service=self.service, level="ERROR", limit=20)

    def _t4_deploys(self, _):
        return self._call("get_recent_deploys", service=self.service, limit=5)

    def _t5_sandbox(self, available):
        if "run_diagnostic" not in available:
            return self._t6_propose(available)
        alerts = self._list("get_active_alerts")
        metrics = self._dict("get_metrics")
        payload = {
            "alert_fired_at": alerts[0].get("fired_at") if alerts else None,
            "deploys": self._list("get_recent_deploys"),
            "error_logs": self._list("get_logs"),
            "current_version": metrics.get("version"),
        }
        if not payload["alert_fired_at"] or not payload["deploys"]:
            # Nothing to correlate — skip the sandbox rather than feed it junk.
            return self._t6_propose(available)
        return AssistantTurn(
            text="Time-correlation is the crux, so I'll compute it rather than eyeball it.",
            tool_calls=[ToolCall(f"sim-{self.turn}", "run_diagnostic",
                                 {"code": REFERENCE_DIAGNOSTIC, "payload": payload})],
        )

    def _t6_propose(self, _):
        diag = self._diag()
        target = diag.get("rollback_target") or self._fallback_target()
        suspect = diag.get("suspect", "the current version")
        score = diag.get("suspicion_score")
        reason = (
            f"Deploy {suspect} shipped {diag.get('minutes_before_alert', '~15')} min before the "
            f"alert and is named in the error logs (sandboxed correlation score {score}). "
            f"This is a code regression, not capacity — a restart would not clear it. "
            f"Proposing rollback of {self.service} to {target}. "
            f"Risk if wrong: a brief redeploy blip; {target} ran clean for 30h."
        )
        return AssistantTurn(text=reason,
                             tool_calls=[ToolCall(f"sim-{self.turn}", "rollback_service",
                                                  {"service": self.service, "to_version": target})])

    def _diag(self) -> dict:
        res = self._dict("run_diagnostic").get("result")
        return res if isinstance(res, dict) else {}

    def _fallback_target(self) -> str:
        deploys = self._list("get_recent_deploys")
        current = self._dict("get_metrics").get("version")
        for d in deploys:
            if d.get("version") != current:
                return d["version"]
        return "v1.4.1"

    def _t7_verify(self, _):
        if self._rejected:
            if self._denied_by == "human":
                who = "Rollback rejected by the on-call human."
            elif self._denied_by:
                who = (f"Rollback denied by {self._denied_by} — the approval gate failed "
                       "closed, so no human ever saw this request.")
            else:
                who = "Rollback denied."
            return AssistantTurn(text=(
                f"{who} Standing down — I will not retry or substitute a different "
                "destructive action. checkout-service remains degraded on the suspect "
                "version; escalate to the service owner."))
        return self._call("get_metrics", service=self.service)

    def _t8_report(self, _):
        m = self._dict("get_metrics")
        diag = self._diag()
        return AssistantTurn(text=(
            f"INCIDENT SUMMARY\n"
            f"What happened: {self.service} error rate spiked to 34% with p99 at 4200ms.\n"
            f"Root cause: bad deploy {diag.get('suspect', 'v1.4.2')} "
            f"({diag.get('minutes_before_alert', 15)} min before the alert, "
            f"correlation {diag.get('suspicion_score', 0.76)}, version named in error logs).\n"
            f"Action taken: rollback to {diag.get('rollback_target', 'v1.4.1')}, human-approved.\n"
            f"Current state: status={m.get('status')}, "
            f"error_rate={m.get('error_rate')}, p99={m.get('p99_latency_ms')}ms.\n"
            f"Follow-up: {diag.get('suspect', 'v1.4.2')} needs a fix before redeploy."))
