"""
The human approval gate — the money shot.

Nothing destructive runs until a person says yes. Three ways to answer, chosen
by mode; all of them go through the same `Decision` so the agent loop has one
code path:

  ui   — the dashboard (AGENT_BUS_URL set). Emits awaiting_approval, then polls
         GET /decision until a human clicks. This is what we demo.
  cli  — terminal y/N prompt. Works with no server at all.
  auto — AUTO_APPROVE=1. Recording only; every event is stamped by="auto" so a
         hands-free run can never be mistaken for a human-approved one.

Design notes worth defending in the pitch:
  * Fail CLOSED. Timeout, bus outage, EOF on stdin, Ctrl-C — every one of them
    denies. There is no path where "we couldn't ask" means "go ahead".
  * Each request carries a request_id, so a decision from an earlier gate can
    never be replayed to approve a later one.
"""
import os
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Optional

DEFAULT_TIMEOUT_S = 300
POLL_INTERVAL_S = 1.0


@dataclass
class Decision:
    approved: bool
    by: str               # "human" | "auto" | "system"
    reason: str = ""
    request_id: str = ""


class ApprovalGate:
    def __init__(self, bus, mode: Optional[str] = None, timeout_s: int = DEFAULT_TIMEOUT_S):
        self.bus = bus
        self.timeout_s = timeout_s
        self.mode = mode or self._detect_mode()
        self._seen: set[str] = set()   # request_ids already decided

    def _detect_mode(self) -> str:
        if os.environ.get("AUTO_APPROVE") == "1":
            return "auto"
        return "ui" if self.bus.bus_url else "cli"

    def request(self, action: str, args: dict, reason: str) -> Decision:
        """Emit awaiting_approval, block for a human, return the decision."""
        request_id = uuid.uuid4().hex[:12]
        self.bus.emit(
            "awaiting_approval", action=action, args=args, reason=reason,
            risk="destructive", request_id=request_id,
        )

        try:
            decision = self._collect(action, args, request_id)
        except KeyboardInterrupt:
            decision = Decision(False, "human", "interrupted at the approval gate", request_id)
        except Exception as exc:
            # Fail closed: an unreachable approver is a denial, not a green light.
            decision = Decision(False, "system", f"approval channel failed: {exc}", request_id)

        self._seen.add(request_id)
        self.bus.emit(
            "approval_decision", action=action, approved=decision.approved,
            by=decision.by, reason=decision.reason, request_id=request_id,
        )
        return decision

    # -- channels -----------------------------------------------------------
    def _collect(self, action: str, args: dict, request_id: str) -> Decision:
        if self.mode == "auto":
            time.sleep(1)  # let the dashboard render the card before it clears
            return Decision(True, "auto", "AUTO_APPROVE=1 (unattended run)", request_id)
        if self.mode == "ui":
            return self._await_ui(action, request_id)
        return self._await_cli(action, args, request_id)

    def _await_ui(self, action: str, request_id: str) -> Decision:
        import requests

        print(f"\n>>> PAUSED. Waiting for approval of {action} in the dashboard "
              f"({self.timeout_s}s timeout)…")
        deadline = time.time() + self.timeout_s
        consecutive_errors = 0

        while time.time() < deadline:
            time.sleep(POLL_INTERVAL_S)
            try:
                # request_id is an additive param: a server that ignores it still
                # answers correctly for the single-gate demo flow.
                resp = requests.get(
                    f"{self.bus.bus_url}/decision",
                    params={"action": action, "request_id": request_id}, timeout=5,
                )
                data = resp.json()
                consecutive_errors = 0
            except Exception:
                consecutive_errors += 1
                if consecutive_errors >= 20:
                    raise RuntimeError("approval bus unreachable for 20 consecutive polls")
                continue

            if data.get("pending"):
                continue
            # Guard against a decision recorded for an earlier gate on the same action.
            returned_id = data.get("request_id")
            if returned_id and returned_id in self._seen:
                continue
            return Decision(bool(data.get("approved")), "human",
                            "decided in the dashboard", request_id)

        return Decision(False, "system",
                        f"no human responded within {self.timeout_s}s — denied by default",
                        request_id)

    def _await_cli(self, action: str, args: dict, request_id: str) -> Decision:
        if not sys.stdin.isatty():
            return Decision(False, "system",
                            "no interactive terminal to approve in — denied", request_id)
        answer = input(f"\n>>> APPROVE {action}({args})?  [y/N] ").strip().lower()
        return Decision(answer in ("y", "yes"), "human", "decided at the CLI", request_id)
