"""A2A wire models: agent card, skills, messages and tasks.

CONCEPT:SCION-CONNECTOR-CONTROL-R014.21
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TaskState = Literal[
    "submitted",
    "working",
    "input-required",
    "completed",
    "canceled",
    "failed",
    "rejected",
    "auth-required",
    "unknown",
]
TERMINAL_STATES: frozenset[str] = frozenset(
    {"completed", "canceled", "failed", "rejected"}
)
PENDING_STATES: frozenset[str] = frozenset({"submitted", "working"})


class _Wire(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class AgentSkill(_Wire):
    """One skill an agent card advertises."""

    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=4096)
    tags: list[str] = Field(default_factory=list, max_length=64)
    input_modes: list[str] = Field(default_factory=lambda: ["text"], alias="inputModes")
    output_modes: list[str] = Field(
        default_factory=lambda: ["text"], alias="outputModes"
    )


class AgentCard(_Wire):
    """The document an agent serves at ``/.well-known/agent-card.json``."""

    name: str = Field(min_length=1, max_length=256)
    url: str = Field(min_length=1, max_length=2048)
    version: str = Field(default="0.0.0", max_length=64)
    description: str = Field(default="", max_length=4096)
    protocol_version: str = Field(default="0.3.0", alias="protocolVersion")
    skills: list[AgentSkill] = Field(default_factory=list, max_length=256)
    default_input_modes: list[str] = Field(
        default_factory=lambda: ["text"], alias="defaultInputModes"
    )
    default_output_modes: list[str] = Field(
        default_factory=lambda: ["text"], alias="defaultOutputModes"
    )
    capabilities: dict[str, Any] = Field(default_factory=dict)

    def wire(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)


class TextPart(_Wire):
    kind: Literal["text"] = "text"
    text: str


class A2AMessage(_Wire):
    kind: Literal["message"] = "message"
    role: Literal["user", "agent"]
    parts: list[TextPart] = Field(default_factory=list)
    message_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), alias="messageId"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def user_text(cls, text: str) -> A2AMessage:
        return cls(role="user", parts=[TextPart(text=text)])

    def text(self) -> str:
        return "".join(part.text for part in self.parts)

    def wire(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_defaults=False)


class A2ATaskStatus(_Wire):
    state: TaskState = "unknown"


class A2ATask(_Wire):
    kind: Literal["task"] = "task"
    id: str = Field(min_length=1)
    context_id: str | None = Field(default=None, alias="contextId")
    status: A2ATaskStatus = Field(default_factory=A2ATaskStatus)
    history: list[A2AMessage] = Field(default_factory=list)

    @property
    def pending(self) -> bool:
        return self.status.state in PENDING_STATES

    def last_agent_message(self) -> A2AMessage | None:
        for message in reversed(self.history):
            if message.role != "user":
                return message
        return None


class _TurnEvent(_Wire):
    """One event of a streamed turn, in the order the turn produced it.

    SCION-CENTRALIZED-CAPABILITIES-R017.2: the served app emits these for
    ``message/stream`` and replays them for ``tasks/resubscribe``. ``seq``
    is the durable 1-based sequence the storage assigned, and the cursor
    for a resubscribe.
    """

    task_id: str = Field(min_length=1, alias="taskId")
    context_id: str = Field(min_length=1, alias="contextId")
    seq: int = Field(ge=1)


class TextDeltaEvent(_TurnEvent):
    """Incremental agent text."""

    kind: Literal["text-delta"] = "text-delta"
    text: str


class ToolCallEvent(_TurnEvent):
    """The agent called a tool with these arguments."""

    kind: Literal["tool-call"] = "tool-call"
    tool_name: str = Field(min_length=1, alias="toolName")
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResultEvent(_TurnEvent):
    """The result a tool call returned to the agent."""

    kind: Literal["tool-result"] = "tool-result"
    tool_name: str = Field(min_length=1, alias="toolName")
    result: Any = None


class FinalStateEvent(_TurnEvent):
    """The terminal state of the task the turn ran."""

    kind: Literal["final-state"] = "final-state"
    state: TaskState


#: One streamed turn event, discriminated by its kind.
TurnEvent = Annotated[
    TextDeltaEvent | ToolCallEvent | ToolResultEvent | FinalStateEvent,
    Field(discriminator="kind"),
]
