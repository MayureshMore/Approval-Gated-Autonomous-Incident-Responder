"""Anthropic messages-API provider."""
import json
import os
from typing import Any

from .base import AssistantTurn, ToolCall


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None):
        import anthropic
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("MODEL", "claude-sonnet-4-5")
        self.system = ""
        self.messages: list[dict] = []

    def start(self, system: str, user: str) -> None:
        self.system = system
        self.messages = [{"role": "user", "content": user}]

    def step(self, schemas: list[dict]) -> AssistantTurn:
        from agent.registry import to_anthropic
        resp = self.client.messages.create(
            model=self.model, max_tokens=2048, system=self.system,
            messages=self.messages, tools=to_anthropic(schemas),
        )
        self.messages.append({"role": "assistant", "content": [
            b.model_dump() for b in resp.content]})

        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input or {}))
                 for b in resp.content if b.type == "tool_use"]
        return AssistantTurn(text=text, tool_calls=calls)

    def record_tool_result(self, call: ToolCall, result: Any) -> None:
        # Anthropic wants every tool_result for a turn in one user message; the
        # loop feeds them one at a time, so coalesce into the trailing message.
        block = {"type": "tool_result", "tool_use_id": call.id,
                 "content": json.dumps(result, default=str)[:20000]}
        if self.messages and self.messages[-1]["role"] == "user" \
                and isinstance(self.messages[-1]["content"], list):
            self.messages[-1]["content"].append(block)
        else:
            self.messages.append({"role": "user", "content": [block]})
