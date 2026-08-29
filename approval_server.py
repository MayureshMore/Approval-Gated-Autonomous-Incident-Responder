"""
Approval + event-bus server for the UI (iPad track).

One small process that:
  - receives events from the agent           POST /events
  - streams them to the dashboard            GET  /events
  - takes the human's approve/reject         POST /decision   (from the UI buttons)
  - hands the decision back to the agent      GET  /decision?action=...
  - serves the dashboard                      GET  /

This is what makes approval happen IN THE INTERFACE (not the terminal), which is
exactly what the UI track rewards. In the PRIMARY build you can instead point the
dashboard at the harness's own approval events; this server is the self-contained
path that always works.

Run:  uvicorn approval_server:app --port 8500
Then: open http://localhost:8500/
Agent: set AGENT_BUS_URL=http://localhost:8500 before running the agent.
"""
import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI(title="Incident Responder Bus")

EVENTS = []                 # full event log
DECISIONS = {}             # action name -> {"approved": bool, "request_id": str|None}
UI_FILE = os.path.join(os.path.dirname(__file__), "ui", "dashboard.html")


@app.post("/events")
async def push_event(req: Request):
    try:
        ev = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be JSON")
    if not isinstance(ev, dict):
        raise HTTPException(status_code=400, detail="an event must be a JSON object")
    EVENTS.append(ev)
    return {"ok": True, "n": len(EVENTS)}


@app.get("/events")
def get_events():
    return JSONResponse(EVENTS)


@app.post("/decision")
async def set_decision(req: Request):
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    action = body.get("action")
    if not action or not isinstance(action, str):
        # Without this a decision lands under the key None and shows up in the
        # timeline as "Approved null" — a recorded approval belonging to no gate.
        raise HTTPException(status_code=400, detail="'action' is required")
    request_id = body.get("request_id")
    DECISIONS[action] = {"approved": bool(body.get("approved")), "request_id": request_id}
    EVENTS.append({"kind": "approval_decision", "action": action,
                   "approved": bool(body.get("approved")), "by": "human",
                   "request_id": request_id})
    return {"ok": True}


@app.get("/decision")
def get_decision(action: str, request_id: Optional[str] = None):
    """Agent polls this. Returns {'pending': true} until the human decides.

    Decisions are keyed by action name, but an action name is not unique across
    runs — so a decision is only handed back to the gate it was actually made
    for, identified by `request_id`.

    Without that check the gate fails OPEN: approve a rollback, start a second
    run without resetting the bus, and the new run's gate reads the previous
    run's approval and executes immediately, recording `by: "human"` for a
    decision no human made. The agent carries its own stale-id guard, but that
    set lives in process memory and is empty in a freshly started process, so
    the check has to happen here to be worth anything.

    A caller that sends no `request_id` (the older `fallback_agent.py` loop) is
    answered as before, and so is a decision recorded without one — matching is
    only enforced when both sides name a gate.
    """
    d = DECISIONS.get(action)
    if d is None:
        return {"pending": True}
    recorded = d.get("request_id")
    if request_id and recorded and recorded != request_id:
        return {"pending": True}
    return {"pending": False, **d}


@app.post("/reset")
def reset():
    EVENTS.clear()
    DECISIONS.clear()
    return {"ok": True}


@app.get("/")
def dashboard():
    return FileResponse(UI_FILE)
