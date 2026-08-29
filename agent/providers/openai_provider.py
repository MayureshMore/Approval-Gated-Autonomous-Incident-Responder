"""
OpenAI chat-completions provider.

Also the base for any OpenAI-*compatible* endpoint — TrueFoundry's gateway is
one, so `truefoundry.py` subclasses this rather than duplicating the loop.
"""
import json
import os
from typing import Any, Optional

from .base import AssistantTurn, ToolCall


class OpenAIProvider:
    name = "openai"
    default_model = "gpt-4o"

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        extra_headers: Optional[dict] = None,
    ):
        from openai import OpenAI  # imported here so the module loads without the SDK

        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set")

        kwargs: dict[str, Any] = {"api_key": key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        self.base_url = base_url
        self.extra_headers = extra_headers or {}
        self.model = model or os.environ.get("MODEL") or self.default_model
        self.messages: list[dict] = []

    def start(self, system: str, user: str) -> None:
        self.messages = [{"role": "system", "content": system},
                         {"role": "user", "content": user}]

    def step(self, schemas: list[dict]) -> AssistantTurn:
        resp = self.client.chat.completions.create(
            model=self.model, messages=self.messages, tools=schemas,
            tool_choice="auto", extra_headers=self.extra_headers or None,
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


def _loads(raw: Optional[str]) -> dict:
    """Models occasionally emit malformed argument JSON; an empty dict lets the
    tool fail with a clear 'bad arguments' result instead of killing the run."""
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
