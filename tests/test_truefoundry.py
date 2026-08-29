"""
The TrueFoundry gateway provider (A2 — the primary runtime).

No network and no key needed: we stub the OpenAI client and assert on how the
provider is *configured* and how it translates the wire format. That is where a
wrong base_url, a dropped tool schema or a mangled tool_call would hide.
"""
import json
import types

import pytest

from agent.providers import truefoundry as tfy
from agent.providers.base import ToolCall


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("TRUEFOUNDRY_API_KEY", "tfy-test-key")
    monkeypatch.delenv("TRUEFOUNDRY_BASE_URL", raising=False)
    monkeypatch.delenv("TRUEFOUNDRY_MODEL", raising=False)
    monkeypatch.delenv("MODEL", raising=False)


class FakeCompletions:
    """Captures the kwargs the provider sends, and replays a canned response."""

    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _response(text=None, tool_calls=()):
    def msg_dump(exclude_none=True):
        return {"role": "assistant", "content": text}

    message = types.SimpleNamespace(
        content=text,
        tool_calls=[types.SimpleNamespace(
            id=tc[0], function=types.SimpleNamespace(name=tc[1], arguments=tc[2]))
            for tc in tool_calls],
        model_dump=msg_dump,
    )
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


@pytest.fixture
def provider(configured, monkeypatch):
    """A real TrueFoundryProvider with only the network boundary faked."""
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.chat = types.SimpleNamespace(completions=FakeCompletions(_response("ok")))

    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    p = tfy.TrueFoundryProvider(run_id="run-123")
    p.captured_init = captured
    return p


# --- configuration ---------------------------------------------------------
def test_points_at_the_gateway_not_openai(provider):
    assert provider.captured_init["base_url"] == "https://pacific.truefoundry.cloud/api/llm"
    assert provider.captured_init["api_key"] == "tfy-test-key"


def test_uses_a_gateway_qualified_model_id(provider):
    assert provider.model == "openai-main/gpt-4o"
    assert "/" in provider.model, "gateway model ids are provider-account/model"


def test_base_url_and_model_are_overridable(configured, monkeypatch):
    monkeypatch.setenv("TRUEFOUNDRY_BASE_URL", "https://other.truefoundry.cloud/api/llm/")
    monkeypatch.setenv("TRUEFOUNDRY_MODEL", "anthropic-main/claude-sonnet-4-5")
    assert tfy.base_url() == "https://other.truefoundry.cloud/api/llm"   # trailing / stripped

    import openai
    captured = {}
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: captured.update(kw) or
                        types.SimpleNamespace(chat=None))
    p = tfy.TrueFoundryProvider()
    assert p.model == "anthropic-main/claude-sonnet-4-5"
    assert captured["base_url"].endswith("/api/llm")


def test_missing_key_fails_with_an_actionable_message(monkeypatch):
    monkeypatch.delenv("TRUEFOUNDRY_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="Personal Access Token"):
        tfy.api_key()


def test_run_is_tagged_for_gateway_tracing(provider):
    meta = json.loads(provider.extra_headers["X-TFY-METADATA"])
    assert meta["run_id"] == "run-123"
    assert meta["application"] == "ripcord-incident-responder"


def test_does_not_read_the_openai_key(configured, monkeypatch):
    """A stray OPENAI_API_KEY must never be what authenticates to the gateway."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-wrong-key")
    import openai
    captured = {}
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: captured.update(kw) or
                        types.SimpleNamespace(chat=None))
    tfy.TrueFoundryProvider()
    assert captured["api_key"] == "tfy-test-key"


# --- wire format -----------------------------------------------------------
def test_sends_tools_and_the_system_prompt(provider):
    provider.start("SYSTEM RULES", "a production alert fired")
    schemas = [{"type": "function",
                "function": {"name": "get_metrics", "description": "d", "parameters": {}}}]
    provider.step(schemas)

    sent = provider.client.chat.completions.calls[0]
    assert sent["model"] == "openai-main/gpt-4o"
    assert sent["tools"] == schemas, "tool schemas must reach the gateway unchanged"
    assert sent["tool_choice"] == "auto"
    assert sent["messages"][0] == {"role": "system", "content": "SYSTEM RULES"}
    assert sent["extra_headers"]["X-TFY-METADATA"]


def test_parses_a_gated_tool_call_off_the_wire(configured, monkeypatch):
    import openai
    resp = _response("rolling back", [("call_1", "rollback_service",
                                       '{"service":"checkout-service","to_version":"v1.4.1"}')])
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=FakeCompletions(resp))))

    turn = tfy.TrueFoundryProvider().step([])
    assert turn.wants_tools
    call = turn.tool_calls[0]
    assert (call.id, call.name) == ("call_1", "rollback_service")
    assert call.args == {"service": "checkout-service", "to_version": "v1.4.1"}

    from agent.registry import is_gated
    assert is_gated(call.name), "this must still hit the approval gate"


def test_malformed_tool_arguments_do_not_crash_the_run(configured, monkeypatch):
    import openai
    resp = _response(None, [("c1", "get_metrics", "{not json")])
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=FakeCompletions(resp))))
    assert tfy.TrueFoundryProvider().step([]).tool_calls[0].args == {}


def test_tool_results_are_fed_back_in_openai_shape(provider):
    provider.start("s", "u")
    provider.record_tool_result(ToolCall("call_9", "get_metrics"), {"status": "degraded"})
    last = provider.messages[-1]
    assert last["role"] == "tool" and last["tool_call_id"] == "call_9"
    assert json.loads(last["content"])["status"] == "degraded"


# --- preflight -------------------------------------------------------------
def test_preflight_reports_a_missing_key(monkeypatch):
    monkeypatch.delenv("TRUEFOUNDRY_API_KEY", raising=False)
    out = tfy.preflight()
    assert out["ready"] is False and "TRUEFOUNDRY_API_KEY" in out["error"]


def test_preflight_rejects_a_model_the_gateway_does_not_expose(configured, monkeypatch):
    monkeypatch.setenv("TRUEFOUNDRY_MODEL", "openai-main/gpt-9-imaginary")
    monkeypatch.setattr(tfy, "list_models", lambda: ["openai-main/gpt-4o"])
    out = tfy.preflight()
    assert out["ready"] is False and "not exposed" in out["error"]


def test_preflight_passes_when_the_model_is_there(configured, monkeypatch):
    monkeypatch.setenv("TRUEFOUNDRY_MODEL", "openai-main/gpt-4o")
    monkeypatch.setattr(tfy, "list_models", lambda: ["openai-main/gpt-4o", "x/y"])
    assert tfy.preflight()["ready"] is True


def test_unreachable_gateway_is_reported_not_raised(configured, monkeypatch):
    monkeypatch.setattr(tfy, "list_models",
                        lambda: (_ for _ in ()).throw(ConnectionError("dns fail")))
    out = tfy.preflight()
    assert out["ready"] is False and "unreachable" in out["error"]
