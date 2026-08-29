"""
The approval bus.

`tests/test_approval.py` covers the agent-side gate against a fake bus. This
covers the real server, because the gate's stale-decision guard alone is not
enough to keep the promise: that guard is a set held in the agent's memory, so a
freshly started process has an empty one and cannot recognise a decision left
over from an earlier run. The bus is the only place that check survives a
restart, which is why it lives here.
"""
import pytest
from fastapi.testclient import TestClient

import approval_server


@pytest.fixture
def client():
    """A bus with no history — the module keeps EVENTS/DECISIONS at module scope."""
    c = TestClient(approval_server.app)
    c.post("/reset")
    yield c
    c.post("/reset")


def _decide(client, request_id, approved=True, action="rollback_service"):
    body = {"action": action, "approved": approved}
    if request_id is not None:
        body["request_id"] = request_id
    return client.post("/decision", json=body)


# --- the invariant ---------------------------------------------------------
def test_a_decision_is_not_reused_by_a_later_gate(client):
    """
    THE regression test. Approve a rollback, then start a second run without
    resetting the bus — the new run's gate must NOT be able to read the previous
    run's approval.

    Observed before this was fixed: the second run executed the rollback with
    nobody at the keyboard and recorded `by: "human"` for a decision no human
    made. Action names are not unique across runs, so keying on the name alone
    made the gate fail OPEN — the exact opposite of the one claim this project
    is built on.
    """
    _decide(client, "run-A-gate", approved=True)

    later = client.get("/decision",
                       params={"action": "rollback_service", "request_id": "run-B-gate"})

    assert later.json() == {"pending": True}, (
        "a decision made for run A was handed to run B's gate — the gate fails open")


def test_a_gate_still_receives_its_own_decision(client):
    """The fix must not break the case the whole demo depends on."""
    _decide(client, "gate-1", approved=True)
    got = client.get("/decision",
                     params={"action": "rollback_service", "request_id": "gate-1"}).json()
    assert got["pending"] is False and got["approved"] is True
    assert got["request_id"] == "gate-1"


def test_a_rejection_is_carried_back_to_its_own_gate(client):
    _decide(client, "gate-1", approved=False)
    got = client.get("/decision",
                     params={"action": "rollback_service", "request_id": "gate-1"}).json()
    assert got["pending"] is False and got["approved"] is False


def test_caller_without_a_request_id_is_answered(client):
    """`fallback_agent.py` polls without one; matching is only enforced when
    both sides name a gate, so that loop must keep working."""
    _decide(client, "gate-1", approved=True)
    got = client.get("/decision", params={"action": "rollback_service"}).json()
    assert got["pending"] is False and got["approved"] is True


def test_a_decision_recorded_without_a_request_id_is_still_answered(client):
    """A client that posts no id (an older cached dashboard) must not deadlock
    an agent that does send one."""
    _decide(client, None, approved=True)
    got = client.get("/decision",
                     params={"action": "rollback_service", "request_id": "gate-1"}).json()
    assert got["pending"] is False and got["approved"] is True


def test_an_undecided_action_is_pending(client):
    assert client.get("/decision", params={"action": "rollback_service"}).json() == {"pending": True}


# --- input handling --------------------------------------------------------
def test_decision_requires_an_action(client):
    """Without this the decision lands under the key None and renders in the
    timeline as an approval belonging to no gate."""
    assert client.post("/decision", json={"approved": True}).status_code == 400
    assert client.get("/decision", params={"action": "rollback_service"}).json() == {"pending": True}


def test_malformed_bodies_are_rejected_not_crashed(client):
    assert client.post("/events", content=b"{not json").status_code == 400
    assert client.post("/events", json=[1, 2, 3]).status_code == 400
    assert client.post("/decision", content=b"{not json").status_code == 400
    # and the bus is still serving afterwards
    assert client.get("/events").status_code == 200


def test_events_round_trip(client):
    client.post("/events", json={"kind": "run_started", "scenario": "x"})
    events = client.get("/events").json()
    assert [e["kind"] for e in events] == ["run_started"]


def test_a_decision_is_also_recorded_as_an_event(client):
    """The dashboard renders the timeline from the event log, so a decision the
    operator made in the UI has to appear there too."""
    _decide(client, "gate-1", approved=True)
    kinds = [e["kind"] for e in client.get("/events").json()]
    assert "approval_decision" in kinds


def test_reset_clears_events_and_decisions(client):
    """`/reset` is what makes consecutive demo runs deterministic."""
    _decide(client, "gate-1", approved=True)
    client.post("/reset")
    assert client.get("/events").json() == []
    assert client.get("/decision", params={"action": "rollback_service"}).json() == {"pending": True}
