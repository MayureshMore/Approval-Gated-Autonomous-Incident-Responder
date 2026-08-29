"""
Event bus client — the seam to Person B's dashboard (EVENT_CONTRACT.md).

Every event is mirrored three ways so a run is never invisible:
  * POSTed to AGENT_BUS_URL/events (the live dashboard)
  * appended to ui/events.json     (file mirror, works with no server)
  * printed to the console         (the CLI demo)

Bus failures are logged, never raised: a flaky dashboard must not abort an
incident response mid-flight.
"""
import json
import os
import threading
import time
import uuid
from typing import Any, Callable, Optional

# Kinds frozen in EVENT_CONTRACT.md. Anything outside this set is additive and
# must be agreed with Person B before it ships.
CONTRACT_KINDS = {
    "run_started", "tool_call", "tool_result", "awaiting_approval",
    "approval_decision", "agent_message", "run_finished",
}
# Additive kinds — the dashboard may ignore them without breaking.
EXTENDED_KINDS = {
    "subagent_started", "subagent_finished", "sandbox_exec", "error", "run_resumed",
}
KNOWN_KINDS = CONTRACT_KINDS | EXTENDED_KINDS


class EventBus:
    def __init__(
        self,
        bus_url: Optional[str] = None,
        events_path: Optional[str] = None,
        run_id: Optional[str] = None,
        echo: bool = True,
    ):
        self.bus_url = (bus_url if bus_url is not None
                        else os.environ.get("AGENT_BUS_URL", "")).rstrip("/")
        self.events_path = events_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "events.json")
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self.echo = echo
        self.events: list[dict] = []
        self._lock = threading.Lock()
        self._listeners: list[Callable[[dict], None]] = []

    def subscribe(self, fn: Callable[[dict], None]) -> None:
        """Register an in-process listener (used by tests and the CLI renderer)."""
        self._listeners.append(fn)

    def emit(self, kind: str, **data: Any) -> dict:
        if kind not in KNOWN_KINDS:
            # Loud, but not fatal — a typo shouldn't kill a live demo.
            print(f"[bus] WARNING: '{kind}' is not in the event contract")
        ev = {"t": time.time(), "kind": kind, "run_id": self.run_id, **data}
        with self._lock:
            self.events.append(ev)
            self._write_mirror()
        self._push(ev)
        if self.echo:
            self._print(ev)
        for fn in self._listeners:
            try:
                fn(ev)
            except Exception:
                pass
        return ev

    def replay(self, history: list[dict]) -> int:
        """
        Re-seed a fresh bus with a resumed run's earlier events.

        Ctrl-C on the demo launcher kills the approval server too, so a resumed
        run usually meets a bus with empty memory — and the dashboard would show
        a timeline starting at `run_resumed`, losing exactly the continuity that
        session survival is meant to demonstrate.

        Idempotent: if the bus already carries events for this run (the operator
        killed only the agent), nothing is pushed.
        """
        if not history:
            return 0

        with self._lock:
            self.events = list(history)
            self._write_mirror()

        if not self.bus_url:
            return 0
        try:
            import requests
            existing = requests.get(f"{self.bus_url}/events", timeout=5).json()
        except Exception as exc:
            print(f"[bus] could not read history ({type(exc).__name__}) — skipping replay")
            return 0

        if any(e.get("run_id") == self.run_id for e in existing):
            return 0   # the bus survived; its log is already the source of truth

        pushed = 0
        for ev in history:
            self._push(ev)
            pushed += 1
        print(f"[bus] replayed {pushed} events so the timeline stays continuous")
        return pushed

    # -- transports ---------------------------------------------------------
    def _write_mirror(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.events_path), exist_ok=True)
            tmp = self.events_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.events, f, indent=2)
            os.replace(tmp, self.events_path)  # atomic: the UI never reads a half file
        except OSError as exc:
            print(f"[bus] mirror write failed: {exc}")

    def _push(self, ev: dict) -> None:
        if not self.bus_url:
            return
        try:
            import requests
            requests.post(f"{self.bus_url}/events", json=ev, timeout=5)
        except Exception as exc:
            print(f"[bus] push failed ({type(exc).__name__}) — run continues")

    @staticmethod
    def _print(ev: dict) -> None:
        body = {k: v for k, v in ev.items() if k not in ("t", "kind", "run_id")}
        line = json.dumps(body, default=str)
        print(f"[{ev['kind']}] {line[:300]}{'…' if len(line) > 300 else ''}")
