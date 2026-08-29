"""
Sandbox diagnostics (DGX upgrade #2).

This is the code the AGENT WRITES AND RUNS in the harness sandbox during
investigation, instead of eyeballing tool output. It quantifies whether a recent
deploy correlates with the incident, so the diagnosis is evidence-backed.

In the demo: the agent calls get_recent_deploys + get_logs, then runs
correlate_deploy_to_incident(...) in the sandbox and cites the score in its reasoning.
"""
from datetime import datetime
from typing import Optional


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def correlate_deploy_to_incident(
    alert_fired_at: str,
    deploys: list,
    error_logs: list,
    window_minutes: int = 30,
) -> dict:
    """
    Score how strongly a recent deploy explains an incident.

    Returns the prime-suspect deploy, minutes between deploy and alert, whether
    any error log references the deployed version, and a 0-1 suspicion score.
    """
    alert_t = _parse(alert_fired_at)

    # Candidate deploys: shipped BEFORE the alert, within the window.
    candidates = []
    for d in deploys:
        dt = _parse(d["deployed_at"])
        delta_min = (alert_t - dt).total_seconds() / 60.0
        if 0 <= delta_min <= window_minutes:
            candidates.append((delta_min, d))

    if not candidates:
        return {
            "suspect": None,
            "suspicion_score": 0.0,
            "reason": f"No deploy within {window_minutes} min before the alert.",
        }

    # Closest deploy in time is the prime suspect.
    candidates.sort(key=lambda x: x[0])
    delta_min, suspect = candidates[0]

    # Do any error logs name the suspect version? Strong signal.
    version = suspect["version"]
    version_mentioned = any(version in (l.get("message", "")) for l in error_logs)

    # Score: closer in time = higher; version named in logs = big boost.
    proximity = max(0.0, 1.0 - (delta_min / window_minutes))  # 1.0 == same minute
    score = min(1.0, 0.6 * proximity + (0.4 if version_mentioned else 0.0))

    return {
        "suspect": version,
        "deployed_by": suspect.get("deployed_by"),
        "minutes_before_alert": round(delta_min, 1),
        "version_referenced_in_error_logs": version_mentioned,
        "suspicion_score": round(score, 2),
        "reason": (
            f"Deploy {version} shipped {round(delta_min,1)} min before the alert"
            + (" and is named in error logs" if version_mentioned else "")
            + f". Suspicion score {round(score,2)}."
        ),
    }


def recommend_rollback_target(deploys: list, current_version: Optional[str] = None) -> Optional[str]:
    """Pick the last known-good version to roll back to (the deploy before current)."""
    if not deploys:
        return None
    ordered = sorted(deploys, key=lambda d: _parse(d["deployed_at"]), reverse=True)
    current = current_version or (ordered[0]["version"] if ordered else None)
    for d in ordered:
        if d["version"] != current:
            return d["version"]
    return None


if __name__ == "__main__":
    # Self-test with the demo scenario shape.
    from datetime import timezone, timedelta
    t = datetime.now(timezone.utc)
    deploys = [
        {"version": "v1.4.2", "deployed_at": (t - timedelta(minutes=15)).isoformat(), "deployed_by": "ci-bot"},
        {"version": "v1.4.1", "deployed_at": (t - timedelta(hours=30)).isoformat(), "deployed_by": "ci-bot"},
    ]
    logs = [{"level": "ERROR", "message": "NullPointer in PaymentProcessor after v1.4.2 config change"}]
    alert = (t - timedelta(minutes=3)).isoformat()
    print("correlation:", correlate_deploy_to_incident(alert, deploys, logs))
    print("rollback target:", recommend_rollback_target(deploys, "v1.4.2"))
