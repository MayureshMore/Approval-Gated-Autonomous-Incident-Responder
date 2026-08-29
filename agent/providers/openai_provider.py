"""OpenAI chat-completions provider."""
import json
import os
from typing import Any

from .base import AssistantTurn, ToolCall


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str | None = None):
        from openai import OpenAI  # imported here so the module loads without the SDK
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set")
        self.client = OpenAI()
        self.model = model or os.environ.get("MODEL", "gpt-4o")
        self.messages: list[dict] = []

    def start(self, system: str, user: str) -> None:
        self.messages = [{"role": "system", "content": system},
                         {"role": "user", "content": user}]

    def step(self, schemas: list[dict]) -> AssistantTurn:
        resp = self.client.chat.completions.create(
            model=self.model, messages=self.messages, tools=schemas, tool_choice="auto",
        )
        msg = resp.choices[0].message
        self.messages.append(msg.model_dump(exclude_none=True))
        calls = [
            ToolCall(tc.id, tc.function.name, _loads(tc.function.arguments))
            for tc in (msg.tool_calls or [])
        ]
        return AssistantTurn(text=msg.content or "", tool_calls=calls)

    def record_tool_result(self, call: ToolCall, result: Any) -> None:
        self.messages.append({
            "role": "tool", "tool_call_id": call.id,
            "content": json.dumps(result, default=str)[:20000],
        })


def _loads(raw: str | None) -> dict:
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
