"""
Mock production environment for the Incident Responder agent.

A single breakable "prod stack". Read-only endpoints let the agent investigate;
destructive endpoints (rollback/restart/scale) are what the agent must gate
behind human approval. Deterministic scenario so the demo is repeatable.

Run:  uvicorn main:app --reload --port 8000
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel

app = FastAPI(title="Mock Prod Stack")


def now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# In-memory world state. /reset restores this to the degraded demo scenario.
# ---------------------------------------------------------------------------
def initial_state():
    t = now()
    return {
        "services": {
            "checkout-service": {
                "status": "degraded",          # bad deploy v1.4.2
                "version": "v1.4.2",
                "error_rate": 0.34,
                "p99_latency_ms": 4200,
                "cpu": 0.71,
                "memory": 0.63,
                "replicas": 4,
            },
            "auth-service": {
                "status": "healthy", "version": "v2.0.1", "error_rate": 0.002,
                "p99_latency_ms": 180, "cpu": 0.22, "memory": 0.40, "replicas": 3,
            },
            "catalog-service": {
                "status": "healthy", "version": "v3.5.0", "error_rate": 0.001,
                "p99_latency_ms": 140, "cpu": 0.30, "memory": 0.45, "replicas": 2,
            },
        },
        "alerts": [
            {
                "id": "ALRT-4471",
                "service": "checkout-service",
                "severity": "critical",
                "summary": "checkout-service error rate 34% / p99 latency 4200ms",
                "fired_at": (t - timedelta(minutes=3)).isoformat(),
            }
        ],
        "deploys": {
            "checkout-service": [
                {"version": "v1.4.2", "deployed_at": (t - timedelta(minutes=15)).isoformat(),
                 "deployed_by": "ci-bot", "status": "current"},
                {"version": "v1.4.1", "deployed_at": (t - timedelta(hours=30)).isoformat(),
                 "deployed_by": "ci-bot", "status": "superseded"},
                {"version": "v1.4.0", "deployed_at": (t - timedelta(days=6)).isoformat(),
                 "deployed_by": "ci-bot", "status": "superseded"},
            ],
            "auth-service": [
                {"version": "v2.0.1", "deployed_at": (t - timedelta(days=2)).isoformat(),
                 "deployed_by": "ci-bot", "status": "current"},
            ],
            "catalog-service": [
                {"version": "v3.5.0", "deployed_at": (t - timedelta(days=4)).isoformat(),
                 "deployed_by": "ci-bot", "status": "current"},
            ],
        },
        "logs": {
            "checkout-service": [
                {"ts": (t - timedelta(minutes=14)).isoformat(), "level": "INFO",
                 "service": "checkout-service", "message": "Deployed v1.4.2"},
                {"ts": (t - timedelta(minutes=13)).isoformat(), "level": "ERROR",
                 "service": "checkout-service",
                 "message": "NullPointer in PaymentProcessor.charge() after v1.4.2 config change"},
                {"ts": (t - timedelta(minutes=10)).isoformat(), "level": "ERROR",
                 "service": "checkout-service",
                 "message": "Upstream timeout calling payment-gateway (waited 5000ms)"},
                {"ts": (t - timedelta(minutes=5)).isoformat(), "level": "ERROR",
                 "service": "checkout-service",
                 "message": "5xx rate exceeded threshold (34%)"},
            ],
            "auth-service": [
                {"ts": (t - timedelta(minutes=8)).isoformat(), "level": "INFO",
                 "service": "auth-service", "message": "Token refresh OK"},
            ],
            "catalog-service": [
                {"ts": (t - timedelta(minutes=8)).isoformat(), "level": "INFO",
                 "service": "catalog-service", "message": "Cache warm complete"},
            ],
        },
    }


STATE = initial_state()


# ---------------------------------------------------------------------------
# Read-only endpoints
# ---------------------------------------------------------------------------
@app.get("/alerts")
def get_alerts():
    return STATE["alerts"]


@app.get("/services")
def list_services():
    return [
        {"service": s, "status": v["status"], "version": v["version"], "replicas": v["replicas"]}
        for s, v in STATE["services"].items()
    ]


@app.get("/metrics")
def get_metrics(service: str = Query(...)):
    v = STATE["services"].get(service)
    if not v:
        return {"error": f"unknown service '{service}'"}
    return {"service": service, **v}


@app.get("/logs")
def get_logs(service: str = Query(...), level: Optional[str] = "ERROR", limit: int = 20):
    logs = STATE["logs"].get(service, [])
    if level and level.upper() != "ALL":
        logs = [l for l in logs if l["level"] == level.upper()]
    # `logs[-limit:]` reads naturally but lies at the edges: limit=0 becomes
    # logs[0:] and returns EVERYTHING, and a negative limit silently drops
    # lines off the front. The agent picks this number itself.
    limit = max(0, limit)
    return logs[len(logs) - limit:] if limit else []


@app.get("/deploys")
def get_deploys(service: str = Query(...), limit: int = 5):
    # Same edge as /logs: a negative limit would slice from the end instead of
    # returning nothing.
    return STATE["deploys"].get(service, [])[:max(0, limit)]


# ---------------------------------------------------------------------------
# Destructive endpoints (agent must gate these behind approval)
# ---------------------------------------------------------------------------
class RollbackReq(BaseModel):
    service: str
    to_version: str


class RestartReq(BaseModel):
    service: str


class ScaleReq(BaseModel):
    service: str
    replicas: int


def _recover(service: str, version: str):
    """Bring a service back to healthy (used after a correct rollback/restart)."""
    v = STATE["services"][service]
    v.update(status="healthy", version=version, error_rate=0.004,
             p99_latency_ms=240, cpu=0.28)


@app.post("/rollback")
def rollback(req: RollbackReq):
    v = STATE["services"].get(req.service)
    if not v:
        return {"ok": False, "error": f"unknown service '{req.service}'"}
    from_version = v["version"]

    # A rollback only helps if it actually targets a known-good earlier release.
    # Without these two checks the environment heals on ANY version string — it
    # would report "healthy" for a rollback to a version that never existed, or
    # to the bad deploy itself — which quietly rewards a wrong diagnosis and
    # undercuts the point of the scenario (a restart already cannot fix it; the
    # *version* has to be right too).
    known = {d["version"] for d in STATE["deploys"].get(req.service, [])}
    if req.to_version not in known:
        return {"ok": False, "service": req.service, "from_version": from_version,
                "to_version": req.to_version,
                "message": (f"No deploy of {req.service} at version {req.to_version} — "
                            f"known versions are {', '.join(sorted(known))}. Nothing changed.")}
    if req.to_version == from_version:
        return {"ok": False, "service": req.service, "from_version": from_version,
                "to_version": req.to_version,
                "message": (f"{req.service} is already running {req.to_version}; rolling back to "
                            "the version that is already deployed changes nothing.")}

    _recover(req.service, req.to_version)
    # mark deploy history
    for d in STATE["deploys"].get(req.service, []):
        d["status"] = "current" if d["version"] == req.to_version else "superseded"
    return {"ok": True, "service": req.service, "from_version": from_version,
            "to_version": req.to_version,
            "message": f"Rolled back {req.service} {from_version} -> {req.to_version}; service recovering."}


@app.post("/restart")
def restart(req: RestartReq):
    v = STATE["services"].get(req.service)
    if not v:
        return {"ok": False, "error": f"unknown service '{req.service}'"}
    # restart alone does NOT fix a bad deploy — realistic: error rate only dips
    v["error_rate"] = round(v["error_rate"] * 0.8, 3)
    return {"ok": True, "service": req.service,
            "message": f"Restarted {req.service}; a restart won't fix a bad deploy — error rate still elevated."}


@app.post("/scale")
def scale(req: ScaleReq):
    v = STATE["services"].get(req.service)
    if not v:
        return {"ok": False, "error": f"unknown service '{req.service}'"}

    # Same reasoning as the rollback version guard: an environment that
    # confirms a nonsensical destructive action rewards a wrong diagnosis.
    # It used to answer {"ok": true, "replicas": -5, "Scaled ... to -5
    # replicas."} — a success response for something no orchestrator would do.
    MAX_REPLICAS = 100
    if req.replicas < 1:
        return {"ok": False, "service": req.service, "replicas": v["replicas"],
                "message": (f"Cannot scale {req.service} to {req.replicas} — replicas must be "
                            "at least 1. Nothing changed.")}
    if req.replicas > MAX_REPLICAS:
        return {"ok": False, "service": req.service, "replicas": v["replicas"],
                "message": (f"Cannot scale {req.service} to {req.replicas} — the cluster caps a "
                            f"service at {MAX_REPLICAS} replicas. Nothing changed.")}

    v["replicas"] = req.replicas
    return {"ok": True, "service": req.service, "replicas": req.replicas,
            "message": f"Scaled {req.service} to {req.replicas} replicas."}


# ---------------------------------------------------------------------------
# Demo utility
# ---------------------------------------------------------------------------
@app.post("/reset")
def reset():
    global STATE
    STATE = initial_state()
    return {"ok": True, "message": "Scenario reset to degraded checkout-service."}
