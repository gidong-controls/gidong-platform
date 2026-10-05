"""Versioned public contracts shared by Platform, Agent, and PLC MCP.

This package contains no private Agent implementation, PLC adapter code,
approval secret, or raw engineering target identity.
"""

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
