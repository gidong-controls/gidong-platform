"""Platform-side Agent protocol result DTOs.

Pydantic AI, prompting, structured inference, and derived RAG live in the
private ``controlstudio-agent`` artifact.  The public Host keeps only the
serializable results and callback shapes needed to supervise that process.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from .models import Message

TokenCallback = Callable[[str], Awaitable[None]]
ToolEventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]
ChangePlanCallback = Callable[..., Awaitable[dict[str, Any]]]


class AgentOutputContractError(RuntimeError):
    """A sidecar rejected model output against its structured contract."""


@dataclass(frozen=True)
class AgentRunOutput:
    content: str
    model: str
    input_tokens: int
    output_tokens: int
    workflow: Literal["engineering", "new_project_planning"] = "engineering"
    plan_status: Literal["needs_clarification", "ready_for_review"] | None = None
    project_name: str | None = None
    plan_artifact: dict[str, Any] | None = None
    plan_digest: str | None = None


@dataclass(frozen=True)
class ModelCapabilityResult:
    success: bool
    tested_at: datetime
    completion: bool
    streaming: bool
    tool_calling: bool
    tool_result_continuation: bool
    structured_output: bool
    message: str


class AgentRuntime(Protocol):
    async def run(
        self,
        *,
        prompt: str,
        history: Sequence[Message],
        conversation_id: str,
        run_id: str,
        trusted_context: dict[str, Any] | None = None,
        on_token: TokenCallback | None = None,
        on_tool_event: ToolEventCallback | None = None,
    ) -> AgentRunOutput: ...

    async def test_capabilities(self) -> ModelCapabilityResult: ...
