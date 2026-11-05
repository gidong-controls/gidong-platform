"""Versioned public contracts shared by Platform, Agent, and PLC MCP.

This package contains no private Agent implementation, PLC adapter code,
approval secret, or raw engineering target identity.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .native_preflight import validate_native_preflight_diagnostic
    from .program_check_failure import ProgramCheckFailureObservationV1
    from .v1 import (
        AGENT_PROTOCOL_VERSION_V1,
        CONTRACT_VERSION_V1,
        AgentManifestV1,
        AgentToolDefinitionV1,
        AuditRecordV1,
        EventEnvelopeV1,
        ExternalToolRequestV1,
        ExternalToolResultV1,
        MCPProductManifestV1,
        MCPProductToolContractV1,
        WorkspaceSourceV1,
        WorkspaceWorkingCopyV1,
    )

__all__ = [
    "AGENT_PROTOCOL_VERSION_V1",
    "CONTRACT_VERSION_V1",
    "AgentManifestV1",
    "AgentToolDefinitionV1",
    "AuditRecordV1",
    "EventEnvelopeV1",
    "ExternalToolRequestV1",
    "ExternalToolResultV1",
    "MCPProductManifestV1",
    "MCPProductToolContractV1",
    "ProgramCheckFailureObservationV1",
    "validate_native_preflight_diagnostic",
    "WorkspaceSourceV1",
    "WorkspaceWorkingCopyV1",
]


def __getattr__(name):
    # Pure command/failure submodules must not initialize unrelated Pydantic
    # models before the MCP server can accept its initialize request.
    if name == "validate_native_preflight_diagnostic":
        from . import native_preflight as module
    elif name == "ProgramCheckFailureObservationV1":
        from . import program_check_failure as module
    elif name in __all__:
        from . import v1 as module
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
