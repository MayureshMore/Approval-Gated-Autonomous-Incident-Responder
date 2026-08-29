"""
The correlation maths the agent cites on stage. If the score is wrong, the
diagnosis is a guess wearing a number, so pin down the edges.
"""
from datetime import datetime, timedelta, timezone

import pytest

from diagnostics import correlate_deploy_to_incident, recommend_rollback_target

T = datetime.now(timezone.utc)


def _iso(**kw):
    return (T - timedelta(**kw)).isoformat()


def _deploys(*pairs):
    return [{"version": v, "deployed_at": _iso(minutes=m), "deployed_by": "ci-bot"}
            for v, m in pairs]


def test_demo_scenario_scores_high():
    out = correlate_deploy_to_incident(
        _iso(minutes=3), _deploys(("v1.4.2", 15), ("v1.4.1", 1800)),
        [{"message": "NullPointer after v1.4.2 config change"}])
    assert out["suspect"] == "v1.4.2"
    assert out["version_referenced_in_error_logs"] is True
    assert out["suspicion_score"] == 0.76      # the number in DEMO_SCRIPT.md


def test_no_deploy_in_window_scores_zero():
    out = correlate_deploy_to_incident(_iso(minutes=3), _deploys(("v1.0.0", 5000)), [])
    assert out["suspect"] is None and out["suspicion_score"] == 0.0


def test_empty_deploys_is_handled():
    out = correlate_deploy_to_incident(_iso(minutes=3), [], [])
    assert out["suspect"] is None


def test_deploys_after_the_alert_are_not_suspects():
    """A deploy that shipped after the alert cannot have caused it."""
    deploys = [{"version": "v2.0.0", "deployed_at": (T + timedelta(minutes=5)).isoformat(),
                "deployed_by": "ci-bot"}]
    out = correlate_deploy_to_incident(_iso(minutes=3), deploys, [])
    assert out["suspect"] is None


def test_closest_deploy_wins():
    out = correlate_deploy_to_incident(
        _iso(minutes=1), _deploys(("v1.4.2", 5), ("v1.4.1", 25)), [])
    assert out["suspect"] == "v1.4.2"


def test_unmentioned_version_scores_lower_than_a_mentioned_one():
    args = (_iso(minutes=3), _deploys(("v1.4.2", 15)))
    quiet = correlate_deploy_to_incident(*args, [])
    named = correlate_deploy_to_incident(*args, [{"message": "boom in v1.4.2"}])
    assert named["suspicion_score"] > quiet["suspicion_score"]


def test_score_stays_in_range():
    out = correlate_deploy_to_incident(
        _iso(minutes=0), _deploys(("v9", 0)), [{"message": "v9 exploded"}])
    assert 0.0 <= out["suspicion_score"] <= 1.0


def test_logs_without_a_message_key_do_not_crash():
    out = correlate_deploy_to_incident(_iso(minutes=3), _deploys(("v1.4.2", 15)), [{}, {"level": "ERROR"}])
    assert out["suspect"] == "v1.4.2"


# --- rollback target -------------------------------------------------------
def test_rollback_target_is_the_previous_version():
    assert recommend_rollback_target(_deploys(("v1.4.2", 15), ("v1.4.1", 1800)), "v1.4.2") == "v1.4.1"


def test_rollback_target_ignores_ordering_of_the_input():
    assert recommend_rollback_target(_deploys(("v1.4.1", 1800), ("v1.4.2", 15)), "v1.4.2") == "v1.4.1"


def test_rollback_target_none_when_nothing_to_roll_back_to():
    assert recommend_rollback_target(_deploys(("v1.4.2", 15)), "v1.4.2") is None
    assert recommend_rollback_target([], "v1") is None
