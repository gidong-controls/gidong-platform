"""Platform-side Agent protocol result DTOs.

Pydantic AI, prompting, structured inference, and derived RAG live in the
private ``controlstudio-agent`` artifact.  The public Host keeps only the
serializable results and callback shapes needed to supervise that process.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

TokenCallback = Callable[[str], Awaitable[None]]
ProgressCallback = Callable[[str, int], Awaitable[None]]
MissionCheckpointCallback = Callable[
    [
        dict[str, Any],
        str,
        int,
        Literal["continue", "respond"],
        dict[str, Any],
        str,
    ],
    Awaitable[None],
]
ToolEventCallback = Callable[
    [str, dict[str, Any]],
    Awaitable[dict[str, Any] | None],
]
ChangePlanCallback = Callable[..., Awaitable[dict[str, Any]]]

MAX_ENGINEERING_AUTONOMOUS_CYCLES = 8


@dataclass(frozen=True)
class AgentOutputValidationIssue:
    """A non-sensitive schema location and Pydantic error category."""

    loc: tuple[str | int, ...]
    error_type: str


@dataclass(frozen=True)
class AgentOutputContractDiagnostic:
    """Allowlisted structured-output diagnostics accepted from the sidecar."""

    stage: str
    exception_kind: str
    reason_code: str
    validator_code: str
    validation_errors: tuple[AgentOutputValidationIssue, ...]


class AgentOutputContractError(RuntimeError):
    """A sidecar rejected model output against its structured contract."""

    def __init__(
        self,
        message: str,
        *,
        diagnostic: AgentOutputContractDiagnostic | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic


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
    engineering_analysis: dict[str, Any] | None = None
    engineering_analysis_digest: str | None = None
    engineering_mission: dict[str, Any] | None = None
    digest: str | None = None
    cycle_disposition: Literal["continue", "respond"] | None = None


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

    @classmethod
    def failed(cls, message: str) -> ModelCapabilityResult:
        return cls(
            success=False,
            tested_at=datetime.now(timezone.utc),
            completion=False,
            streaming=False,
            tool_calling=False,
            tool_result_continuation=False,
            structured_output=False,
            message=message,
        )
