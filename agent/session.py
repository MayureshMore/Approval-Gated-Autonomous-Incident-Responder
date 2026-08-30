"""
Session persistence (Layer 4) — a run survives losing its process.

An incident response that dies because someone closed a laptop is not an
incident response. The agent checkpoints after every model turn and every tool
result, so a killed run resumes exactly where it stopped — including while it is
parked at the approval gate waiting for a human.

The demo: start a run, let it pause for approval, Ctrl-C the agent, then
`--resume last`. It picks the pending rollback back up and waits for the same
approval. Same run_id, so the dashboard timeline is continuous.

State lives in `runs/<run_id>.json`. Plain JSON on purpose — you can read it
mid-demo and show there is nothing up our sleeve.
"""
import json
import os
import time
from typing import Any, Optional

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ROOT = os.path.join(_REPO, "runs")

RUNNING, FINISHED, FAILED = "running", "finished", "failed"


class SessionStore:
    def __init__(self, root: Optional[str] = None):
        self.root = root or os.environ.get("RUN_STATE_DIR", DEFAULT_ROOT)

    def path(self, run_id: str) -> str:
        return os.path.join(self.root, f"{run_id}.json")

    def save(self, state: dict) -> str:
        """Atomic write — a crash mid-checkpoint must not leave a torn file that
        makes the run unresumable, which would defeat the whole point."""
        os.makedirs(self.root, exist_ok=True)
        state = {**state, "saved_at": time.time()}
        target = self.path(state["run_id"])
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp, target)
        return target

    def load(self, run_id: str) -> dict:
        with open(self.path(run_id), encoding="utf-8") as f:
            return json.load(f)

    def exists(self, run_id: str) -> bool:
        return os.path.exists(self.path(run_id))

    def list_runs(self) -> list[dict]:
        """Newest first."""
        if not os.path.isdir(self.root):
            return []
        out = []
        for name in os.listdir(self.root):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.root, name), encoding="utf-8") as f:
                    s = json.load(f)
                out.append({"run_id": s.get("run_id", name[:-5]),
                            "status": s.get("status"), "step": s.get("step"),
                            "provider": s.get("provider"),
                            "saved_at": s.get("saved_at", 0),
                            "pending": [c["name"] for c in s.get("pending_calls", [])]})
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(out, key=lambda r: r["saved_at"], reverse=True)

    def latest(self, resumable_only: bool = True) -> Optional[str]:
        for run in self.list_runs():
            if not resumable_only or run["status"] == RUNNING:
                return run["run_id"]
        return None
