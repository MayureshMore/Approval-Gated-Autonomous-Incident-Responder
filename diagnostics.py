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


def _parse(ts) -> datetime:
    """Accept an ISO string or a datetime.

    The agent writes this call itself, so it may hand us either. Being strict
    here buys nothing and costs a wasted sandbox run mid-incident.
    """
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        return datetime.fromisoformat(ts)
    raise TypeError(
        f"expected an ISO-8601 timestamp string or datetime, got {type(ts).__name__}: {ts!r}")


def _message_of(log) -> str:
    """A log line may arrive as {'message': ...} or as a bare string."""
    if isinstance(log, str):
        return log
    if isinstance(log, dict):
        return str(log.get("message", ""))
    return str(log)


def _check_error_logs(error_logs) -> None:
    """Reject a summary object loudly.

    Iterating a dict yields its keys, so passing a subagent's findings summary
    here silently matched nothing and produced a plausible-looking low score
    instead of an error — the worst failure mode available.
    """
    if isinstance(error_logs, dict):
        raise TypeError(
            "error_logs must be the list of log lines from get_logs(), not a summary "
            f"object. Got a dict with keys {sorted(error_logs)[:5]}.")
    if not isinstance(error_logs, list):
        raise TypeError(
            f"error_logs must be a list from get_logs(), got {type(error_logs).__name__}")


def _check_deploys(deploys) -> None:
    """Fail with a message the agent can act on, not a cryptic TypeError."""
    if not isinstance(deploys, list):
        raise TypeError(f"deploys must be a list of dicts, got {type(deploys).__name__}")
    for d in deploys:
        if not isinstance(d, dict) or "deployed_at" not in d:
            raise TypeError(
                "each deploy must be a dict with 'version' and 'deployed_at' — pass the "
                f"list from get_recent_deploys() unchanged. Got: {d!r}")


def correlate_deploy_to_incident(
    alert_fired_at: str,
    deploys: list,
    error_logs: Optional[list] = None,
    window_minutes: int = 30,
) -> dict:
    """
    Score how strongly a recent deploy explains an incident.

    Returns the prime-suspect deploy, minutes between deploy and alert, whether
    any error log references the deployed version, and a 0-1 suspicion score.
    """
    _check_deploys(deploys)
    error_logs = [] if error_logs is None else error_logs
    _check_error_logs(error_logs)
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
    version_mentioned = any(version in _message_of(l) for l in error_logs)

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
    _check_deploys(deploys)
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
