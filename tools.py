"""
Agent-facing tool client. The harness (agent/backend dev) registers these as
the agent's tools. Read-only tools run freely; REQUIRES_APPROVAL tools must be
paused by the harness for human sign-off before they execute.

This is the TOOL_CONTRACT.md made executable — import and wrap, don't re-hit
raw HTTP from the agent loop.
"""
import os
import requests

BASE_URL = os.environ.get("MOCK_ENV_URL", "http://localhost:8000")
TIMEOUT = 10

# The harness MUST gate these behind human approval.
REQUIRES_APPROVAL = {"rollback_service", "restart_service", "scale_service"}


# ---- read-only -------------------------------------------------------------
def get_active_alerts() -> list:
    """List currently firing alerts."""
    return requests.get(f"{BASE_URL}/alerts", timeout=TIMEOUT).json()


def get_metrics(service: str) -> dict:
    """Current status/metrics for a service (status, version, error_rate, latency...)."""
    return requests.get(f"{BASE_URL}/metrics", params={"service": service}, timeout=TIMEOUT).json()


def get_logs(service: str, level: str = "ERROR", limit: int = 20) -> list:
    """Recent log lines for a service. level: ERROR|WARN|INFO|ALL."""
    return requests.get(f"{BASE_URL}/logs",
                        params={"service": service, "level": level, "limit": limit},
                        timeout=TIMEOUT).json()


def list_services() -> list:
    """List all services with status and version."""
    return requests.get(f"{BASE_URL}/services", timeout=TIMEOUT).json()


def get_recent_deploys(service: str, limit: int = 5) -> list:
    """Recent deployments for a service (version, deployed_at, status)."""
    return requests.get(f"{BASE_URL}/deploys",
                        params={"service": service, "limit": limit}, timeout=TIMEOUT).json()


# ---- destructive (approval-gated) ------------------------------------------
def rollback_service(service: str, to_version: str) -> dict:
    """DESTRUCTIVE. Roll a service back to a previous version."""
    return requests.post(f"{BASE_URL}/rollback",
                        json={"service": service, "to_version": to_version}, timeout=TIMEOUT).json()


def restart_service(service: str) -> dict:
    """DESTRUCTIVE. Restart a service."""
    return requests.post(f"{BASE_URL}/restart",
                        json={"service": service}, timeout=TIMEOUT).json()


def scale_service(service: str, replicas: int) -> dict:
    """DESTRUCTIVE. Change replica count for a service."""
    return requests.post(f"{BASE_URL}/scale",
                        json={"service": service, "replicas": replicas}, timeout=TIMEOUT).json()


# Registry the harness can iterate over to build tool definitions.
TOOLS = {
    "get_active_alerts": get_active_alerts,
    "get_metrics": get_metrics,
    "get_logs": get_logs,
    "list_services": list_services,
    "get_recent_deploys": get_recent_deploys,
    "rollback_service": rollback_service,
    "restart_service": restart_service,
    "scale_service": scale_service,
}
