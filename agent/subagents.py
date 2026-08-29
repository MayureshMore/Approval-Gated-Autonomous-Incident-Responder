"""
Parallel investigation subagents (harness feature: subagents).

Three focused investigators sweep the environment at once — metrics, logs,
deploys — and their findings are merged into one evidence pack that seeds the
main agent's reasoning. Serial investigation works; this is how a real on-call
team actually splits the first 60 seconds, and it visibly shortens time-to-
diagnosis on the dashboard.

Deliberate constraint: subagents are READ-ONLY. They investigate and summarise;
they never touch a gated tool. Concurrency and destructive actions are a bad mix,
and keeping the approval gate on a single serial path is what makes it trustworthy.

Cut-line honoured: if a subagent fails, its lane is reported as failed and the
main agent proceeds with whatever the others found.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

READ_ONLY = {"get_active_alerts", "get_metrics", "get_logs",
             "list_services", "get_recent_deploys"}


def _alerting_service(impls: dict[str, Callable]) -> tuple[str, dict]:
    alerts = impls["get_active_alerts"]()
    if isinstance(alerts, list) and alerts:
        return alerts[0].get("service", "checkout-service"), alerts[0]
    return "checkout-service", {}


def _metrics_lane(impls, service) -> dict:
    """Is it actually broken, and is anything else broken with it?"""
    metrics = impls["get_metrics"](service)
    fleet = impls["list_services"]()
    unhealthy = [s["service"] for s in fleet if s.get("status") != "healthy"]
    return {
        "service": service,
        "status": metrics.get("status"),
        "version": metrics.get("version"),
        "error_rate": metrics.get("error_rate"),
        "p99_latency_ms": metrics.get("p99_latency_ms"),
        "cpu": metrics.get("cpu"),
        "other_unhealthy_services": [s for s in unhealthy if s != service],
        "verdict": ("blast radius is limited to this service — points at the service's own "
                    "code, not a shared dependency"
                    if unhealthy == [service] else "multiple services affected"),
    }


def _logs_lane(impls, service) -> dict:
    """What is the failure signature, and does it name a version?"""
    errors = impls["get_logs"](service, "ERROR", 20)
    signature = errors[0].get("message") if errors else None
    versions = sorted({tok.strip(".,;)") for line in errors
                       for tok in line.get("message", "").split()
                       if tok.startswith("v") and any(c.isdigit() for c in tok)})
    return {
        # The raw lines travel with the summary: the main agent feeds these
        # straight into correlate_deploy_to_incident, and a summary object in
        # their place scores the incident wrong.
        "lines": errors,
        "error_count": len(errors),
        "first_error": signature,
        "versions_named_in_errors": versions,
        "verdict": (f"errors reference {versions} — a release is implicated"
                    if versions else "no version named in errors"),
    }


def _deploys_lane(impls, service, alert) -> dict:
    """Did anything ship just before this started?"""
    deploys = impls["get_recent_deploys"](service, 5)
    return {
        "recent_deploys": deploys[:3],
        "current": next((d["version"] for d in deploys if d.get("status") == "current"), None),
        "previous": next((d["version"] for d in deploys if d.get("status") != "current"), None),
        "alert_fired_at": alert.get("fired_at"),
        "verdict": "hand the timing to the sandbox diagnostic for a real correlation score",
    }


def investigate_in_parallel(bus, impls: dict[str, Callable]) -> dict[str, Any]:
    """Fan out three read-only investigators, merge what comes back."""
    service, alert = _alerting_service(impls)

    lanes: dict[str, Callable[[], dict]] = {
        "metrics": lambda: _metrics_lane(impls, service),
        "logs": lambda: _logs_lane(impls, service),
        "deploys": lambda: _deploys_lane(impls, service, alert),
    }

    findings: dict[str, Any] = {"service": service, "alert": alert}
    with ThreadPoolExecutor(max_workers=len(lanes)) as pool:
        futures = {}
        for name, fn in lanes.items():
            bus.emit("subagent_started", subagent=name,
                     task=f"read-only {name} sweep of {service}")
            futures[pool.submit(fn)] = name

        for fut in as_completed(futures):
            name = futures[fut]
            try:
                result = fut.result(timeout=20)
            except Exception as exc:
                result = {"error": f"{type(exc).__name__}: {exc}"}
            findings[name] = result
            bus.emit("subagent_finished", subagent=name, findings=result)

    return findings
