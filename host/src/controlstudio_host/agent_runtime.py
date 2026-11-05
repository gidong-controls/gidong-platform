"""Platform-side Agent protocol result DTOs.

Pydantic AI, prompting, structured inference, and derived RAG live in the
private ``controlstudio-agent`` artifact.  The public Host keeps only the
serializable results and callback shapes needed to supervise that process.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from .api_models import ModelContextV1

TokenCallback = Callable[[str], Awaitable[None]]
ProgressCallback = Callable[[str, int], Awaitable[None]]
TranscriptCheckpointCallback = Callable[[dict[str, Any], str], Awaitable[None]]
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


_PROVIDER_RUNTIME_ERROR_CODES = frozenset(
    {
        'api_error',
        'authentication_error',
        'bio_policy',
        'content_policy_violation',
        'context_length_exceeded',
        'data_residency_mismatch',
        'empty_image_file',
        'failed_to_download_image',
        'image_content_policy_violation',
        'image_file_not_found',
        'image_file_too_large',
        'image_parse_error',
        'image_too_large',
        'image_too_small',
        'insufficient_quota',
        'invalid_api_key',
        'invalid_base64_image',
        'invalid_image',
        'invalid_image_format',
        'invalid_image_mode',
        'invalid_image_url',
        'invalid_model',
        'invalid_parameter',
        'invalid_prompt',
        'invalid_request_error',
        'length',
        'max_tokens',
        'model_not_found',
        'permission_denied',
        'rate_limit_exceeded',
        'server_error',
        'service_unavailable',
        'unsupported_image_media_type',
        'unsupported_value',
        'vector_store_timeout',
    }
)


_PROVIDER_RUNTIME_ERROR_TYPES = frozenset({'model_not_found', 'not_found_error', 'permission_denied', 'server_error', 'invalid_request_error', 'content_policy_violation', 'rate_limit_error', 'authentication_error', 'service_unavailable', 'invalid_api_key', 'api_error', 'conflict_error'})
_PROVIDER_RUNTIME_EXCEPTION_CLASSES = frozenset({'APIError', 'ConflictError', 'ModelAPIError', 'ModelHTTPError', 'AuthenticationError', 'APIResponseValidationError', 'OAuthError', 'InternalServerError', 'PermissionDeniedError', 'NotFoundError', 'APIStatusError', 'UnprocessableEntityError', 'BadRequestError', 'RateLimitError'})


@dataclass(frozen=True)
class AgentRuntimeDiagnostic:
    """Allowlisted non-output runtime failure reported by the sidecar."""

    stage: str
    exception_kind: str
    reason_code: str
    provider_http_status: int | None = None
    provider_error_code: str | None = None
    provider_error_type: str | None = None
    provider_exception_class: str | None = None


def provider_failure_detail(diagnostic: AgentRuntimeDiagnostic) -> str:
    """Format only accepted numeric HTTP status and closed public error codes."""
    if diagnostic.exception_kind != "model_api_error":
        return ""
    fields = []
    status = diagnostic.provider_http_status
    if type(status) is int and 400 <= status <= 599:
        fields.append(f"providerHttpStatus={status}")
    code = diagnostic.provider_error_code
    if type(code) is str and code in _PROVIDER_RUNTIME_ERROR_CODES:
        fields.append(f"providerErrorCode={code}")
    error_type = diagnostic.provider_error_type
    if type(error_type) is str and error_type in _PROVIDER_RUNTIME_ERROR_TYPES:
        fields.append(f"providerErrorType={error_type}")
    exception_class = diagnostic.provider_exception_class
    if type(exception_class) is str and exception_class in _PROVIDER_RUNTIME_EXCEPTION_CLASSES:
        fields.append(f"providerExceptionClass={exception_class}")
    return "; ".join(fields)


class AgentRuntimeError(RuntimeError):
    """A sanitized model/runtime failure with an optional stable diagnostic."""

    def __init__(
        self,
        message: str,
        *,
        diagnostic: AgentRuntimeDiagnostic | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class AgentRunOutput:
    content: str
    model: str
    input_tokens: int
    output_tokens: int
    workflow: Literal["engineering", "new_project_planning", "native_ld_partial_edit"] = "engineering"
    plan_status: Literal["needs_clarification", "ready_for_review"] | None = None
    project_name: str | None = None
    plan_artifact: dict[str, Any] | None = None
    plan_digest: str | None = None
    engineering_analysis: dict[str, Any] | None = None
    engineering_analysis_digest: str | None = None
    engineering_mission: dict[str, Any] | None = None
    digest: str | None = None
    cycle_disposition: Literal["continue", "respond"] | None = None
    approval_handoff: dict[str, str] | None = None
    native_request_disposition: dict[str, Any] | None = None
    native_project_start: dict[str, Any] | None = None


def validated_native_project_start(value: Any) -> dict[str, Any] | None:
    """Typed model selection only; no native target or execution authority."""
    if value is None:
        return None
    fields = {'schema_version', 'conversation_id', 'run_id', 'prompt_sha256', 'project_name', 'simulate'}
    if (not isinstance(value, dict) or set(value) != fields or value.get('schema_version') != '1'
            or any(not isinstance(value.get(key), str) or not value[key] or len(value[key]) > 256
                   for key in ('conversation_id', 'run_id'))
            or not isinstance(value.get('prompt_sha256'), str) or re.fullmatch('[0-9a-f]{64}', value['prompt_sha256']) is None
            or not isinstance(value.get('project_name'), str) or not value['project_name'].strip()
            or value['project_name'] != value['project_name'].strip() or len(value['project_name']) > 200
            or any(ord(char) < 32 for char in value['project_name']) or type(value.get('simulate')) is not bool):
        raise ValueError('Invalid new-project start handoff')
    return dict(value)


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
    model_context: ModelContextV1 | None = None

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
            model_context=None,
        )
