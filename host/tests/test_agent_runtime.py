from __future__ import annotations

from datetime import datetime, timezone

from controlstudio_host.agent_runtime import AgentRunOutput, ModelCapabilityResult


def test_platform_agent_result_dtos_do_not_import_private_runtime() -> None:
    output = AgentRunOutput(
        content="safe result",
        model="private-agent-model",
        input_tokens=1,
        output_tokens=2,
    )
    capability = ModelCapabilityResult(
        success=True,
        tested_at=datetime.now(timezone.utc),
        completion=True,
        streaming=True,
        tool_calling=True,
        tool_result_continuation=True,
        structured_output=True,
        message="ready",
    )

    assert output.workflow == "engineering"
    assert capability.success is True
