"""
FALLBACK local agent (demo insurance).

Primary path is the TrueForge harness. This is a self-contained agent loop using
the OpenAI API directly, so you ALWAYS have a working demo even if harness wiring
stalls. It implements the same investigate -> diagnose -> propose -> APPROVAL
PAUSE -> execute flow, and emits events to ui/events.json so the dashboard renders.

Run:
  cd mock_env && uvicorn main:app --port 8000    # in one terminal
  export OPENAI_API_KEY=...                       # in another
  python fallback_agent.py                        # then open ui/dashboard.html (served)

Env:
  OPENAI_API_KEY   required (real run)
  MODEL            default gpt-4o
  MOCK_ENV_URL     default http://localhost:8000
  AUTO_APPROVE     set to 1 to skip the prompt (for recording); default asks
"""
import json
import os
import sys
import time

import tools
from tool_schemas import TOOL_SCHEMAS

MODEL = os.environ.get("MODEL", "gpt-4o")
BUS = os.environ.get("AGENT_BUS_URL")  # e.g. http://localhost:8500 -> approve in the UI
EVENTS_PATH = os.path.join(os.path.dirname(__file__), "ui", "events.json")
_events = []


def emit(kind: str, **data):
    """Record an event: push to the bus (for the dashboard) and mirror to file."""
    ev = {"t": time.time(), "kind": kind, **data}
    _events.append(ev)
    os.makedirs(os.path.dirname(EVENTS_PATH), exist_ok=True)
    with open(EVENTS_PATH, "w") as f:
        json.dump(_events, f, indent=2)
    if BUS:
        try:
            import requests
            requests.post(f"{BUS}/events", json=ev, timeout=5)
        except Exception:
            pass
    print(f"[{kind}] " + json.dumps(data)[:200])


def run_tool(name: str, args: dict):
    fn = tools.TOOLS[name]
    return fn(**args)


def ask_approval(name: str, args: dict, reason: str) -> bool:
    """Pause for human approval. Via the UI (bus) if AGENT_BUS_URL is set, else CLI."""
    emit("awaiting_approval", action=name, args=args, reason=reason, risk="destructive")

    if os.environ.get("AUTO_APPROVE") == "1":
        time.sleep(1)
        emit("approval_decision", action=name, approved=True, by="auto")
        return True

    if BUS:
        import requests
        print(f"\n>>> Waiting for approval of {name} in the dashboard...")
        while True:
            time.sleep(1.5)
            try:
                d = requests.get(f"{BUS}/decision", params={"action": name}, timeout=5).json()
            except Exception:
                continue
            if not d.get("pending"):
                return bool(d.get("approved"))

    ans = input(f"\n>>> APPROVE {name}({args})?  [y/N] ").strip().lower()
    approved = ans == "y"
    emit("approval_decision", action=name, approved=approved, by="human")
    return approved


SYSTEM = open(os.path.join(os.path.dirname(__file__), "agent_prompt.md")).read()


def main():
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("pip install openai")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("set OPENAI_API_KEY")

    client = OpenAI()
    emit("run_started", scenario="checkout-service incident")
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "A production alert just fired. Investigate and resolve it."},
    ]

    for _ in range(15):  # step budget
        resp = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOL_SCHEMAS, tool_choice="auto",
        )
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            emit("agent_message", text=msg.content or "")
            print("\n=== AGENT ===\n" + (msg.content or ""))
            break

        for tc in msg.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments or "{}")

            if name in tools.REQUIRES_APPROVAL:
                reason = (msg.content or "proposed remediation")
                if not ask_approval(name, args, reason):
                    result = {"ok": False, "message": "Rejected by human. Do not retry; reconsider."}
                    emit("tool_result", tool=name, result=result)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
                    continue

            emit("tool_call", tool=name, args=args)
            result = run_tool(name, args)
            emit("tool_result", tool=name, result=result)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})

    emit("run_finished")


if __name__ == "__main__":
    main()
