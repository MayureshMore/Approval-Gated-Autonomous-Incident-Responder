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

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI(title="Incident Responder Bus")

EVENTS = []                 # full event log
DECISIONS = {}             # action name -> {"approved": bool, "request_id": str|None}
UI_FILE = os.path.join(os.path.dirname(__file__), "ui", "dashboard.html")


@app.post("/events")
async def push_event(req: Request):
    ev = await req.json()
    EVENTS.append(ev)
    return {"ok": True, "n": len(EVENTS)}


@app.get("/events")
def get_events():
    return JSONResponse(EVENTS)


@app.post("/decision")
async def set_decision(req: Request):
    body = await req.json()
    action = body.get("action")
    request_id = body.get("request_id")
    DECISIONS[action] = {"approved": bool(body.get("approved")), "request_id": request_id}
    EVENTS.append({"kind": "approval_decision", "action": action,
                   "approved": bool(body.get("approved")), "by": "human",
                   "request_id": request_id})
    return {"ok": True}


@app.get("/decision")
def get_decision(action: str, request_id: Optional[str] = None):
    """Agent polls this. Returns {'pending': true} until the human decides.

    `request_id` is the id the agent's own pending gate carries; the response
    echoes back the request_id actually recorded at decision time (from the
    dashboard's POST /decision) so the agent can detect a decision left over
    from an earlier gate on the same action name and keep waiting instead of
    misreading it as its own answer.
    """
    d = DECISIONS.get(action)
    if d is None:
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
