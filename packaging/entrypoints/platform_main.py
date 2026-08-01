"""PyInstaller entry point for the public Platform launcher.

The release composer supplies private sidecar locations through environment
variables; no private source tree is imported by this entry point.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

_HOST_CHILD_ARGUMENT = "--controlstudio-internal-host"
_ARTIFACT_SELF_TEST_ARGUMENT = "--controlstudio-artifact-self-test"


async def _run_artifact_self_test() -> int:
    """Exercise the configured Agent and MCP strictly as child artifacts."""

    from controlstudio_host.agent_client import AgentSupervisor
    from controlstudio_host.mcp_client import MCPGateway

    agent = AgentSupervisor(startup_timeout_seconds=30.0)
    mcp = MCPGateway(init_timeout_seconds=30.0)
    try:
        await agent.ensure_started()
        synchronized = await agent.sync_knowledge(
            [
                {
                    "reference": "knowledge://artifact-self-test/page/1",
                    "source_id": "artifact-self-test",
                    "content": "ControlStudio packaged artifact contract probe.",
                }
            ]
        )
        mcp_status = await mcp.start()
        if synchronized != 1 or mcp_status.status != "ready":
            raise RuntimeError("packaged child artifact contract probe failed")
        print(
            json.dumps(
                {
                    "status": "ready",
                    "agent_id": agent.manifest_agent_id,
                    "knowledge_documents": synchronized,
                    "mcp_server": mcp_status.server_name,
                    "mcp_tools": mcp_status.tool_count,
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        await agent.stop()
        await mcp.stop()

if getattr(sys, "frozen", False):
    bundled_web = Path(sys._MEIPASS) / "web-dist"  # type: ignore[attr-defined]
    # A one-file executable is extracted to a fresh temporary directory.  The
    # path is only used for the immutable web asset payload embedded in this
    # executable, never for a checkout-relative web source directory.
    os.environ["CONTROLSTUDIO_WEB_DIST"] = str(bundled_web)

if _HOST_CHILD_ARGUMENT in sys.argv[1:]:
    # The public Platform is a single executable. The launcher starts the same
    # immutable artifact in this private internal mode instead of looking for
    # a Python/module checkout or a second Host executable.
    sys.argv = [sys.argv[0], *(item for item in sys.argv[1:] if item != _HOST_CHILD_ARGUMENT)]
    from controlstudio_host.__main__ import main as host_main

    host_main()
    raise SystemExit(0)

if _ARTIFACT_SELF_TEST_ARGUMENT in sys.argv[1:]:
    raise SystemExit(asyncio.run(_run_artifact_self_test()))

from controlstudio_launcher.cli import main as launcher_main

launcher_arguments = list(sys.argv[1:])
if "--host-executable" not in launcher_arguments:
    if getattr(sys, "frozen", False):
        launcher_arguments[:0] = [
            "--host-executable",
            sys.executable,
            f"--host-arg={_HOST_CHILD_ARGUMENT}",
        ]
    else:
        launcher_arguments[:0] = [
            "--host-executable",
            sys.executable,
            "--host-arg=-m",
            "--host-arg=controlstudio_host",
        ]

raise SystemExit(launcher_main(launcher_arguments))
