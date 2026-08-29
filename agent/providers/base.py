"""
Provider interface.

The agent loop in core.py is provider-agnostic: it speaks only in ToolCall and
AssistantTurn. Each provider translates that to and from one API dialect.

Why this exists: the primary runtime is the TrueFoundry harness, but a hackathon
demo cannot hang on one vendor being reachable at 17:55. Porting to a new runtime
means writing one class here, not touching the loop, the gate or the sandbox.
"""
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class AssistantTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class Provider(Protocol):
    name: str

    def start(self, system: str, user: str) -> None:
        """Seed the conversation."""

    def step(self, schemas: list[dict]) -> AssistantTurn:
        """Ask the model for its next turn."""

    def record_tool_result(self, call: ToolCall, result: Any) -> None:
        """Feed a tool's output back into the conversation."""


class Resumable(Protocol):
    """Providers implement this so a run can survive losing its process.

    The snapshot is the conversation, not the connection: on resume we build a
    fresh client and pour the old messages back in.
    """

    def snapshot(self) -> dict:
        """Serialisable conversation state."""

    def restore(self, state: dict) -> None:
        """Reinstate conversation state from a snapshot."""
