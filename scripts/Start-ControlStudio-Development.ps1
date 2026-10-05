[CmdletBinding()]
param(
    [switch]$NoOpen,
    [switch]$Web,
    [switch]$WebUrl,
    [string]$ProfileRoot
)

$ErrorActionPreference = 'Stop'
if ((@($NoOpen, $Web, $WebUrl) | Where-Object { $_ }).Count -gt 1) {
    throw 'Specify only one of -NoOpen, -Web, or -WebUrl.'
}
if ($ProfileRoot) {
    $resolvedProfileRoot = (Resolve-Path -LiteralPath $ProfileRoot -ErrorAction Stop).Path
    if (-not (Test-Path -LiteralPath $resolvedProfileRoot -PathType Container)) {
        throw "ProfileRoot must be an existing directory: $ProfileRoot"
    }
    $env:LOCALAPPDATA = $resolvedProfileRoot
}
$platform = Split-Path -Parent $PSScriptRoot
$workspace = Split-Path -Parent $platform
$launcher = Join-Path $platform 'launcher\.venv\Scripts\controlstudio.exe'
$agentDevPython = Join-Path $workspace 'gidong-agent\.venv-dev\Scripts\python.exe'
$agentPackagedPython = Join-Path $workspace 'gidong-agent\.venv\Scripts\python.exe'
$agentExecutable = Join-Path $workspace 'gidong-agent\.venv\Scripts\controlstudio-agent.exe'
$mcp = Join-Path $workspace 'gidong-plc-mcp\.venv\Scripts\controlstudio-plc-mcp.exe'
$xg5000Cli = Join-Path $workspace 'controlstudio-xg5000-cli\.venv\Scripts\controlstudio-xg5000.exe'

if (Test-Path -LiteralPath $agentDevPython -PathType Leaf) {
    $agentRuntime = $agentDevPython
    $agentCommand = '"' + $agentDevPython + '" -m controlstudio_agent.sidecar'
    $agentImportPython = $agentDevPython
} else {
    $agentRuntime = $agentExecutable
    $agentCommand = '"' + $agentExecutable + '"'
    $agentImportPython = $agentPackagedPython
}

# Validate the exact interpreter selected above before the launcher can
# request elevation.  ``-E`` keeps a caller's PYTHONPATH/PYTHON* overrides
# from making a sibling checkout appear to be an installed dependency.  This
# is an import-only check: it neither starts the sidecar nor contacts the
# model or an engineering tool.  A failed preflight never falls through to a
# different environment.
$agentImportPreflight = @'
import importlib
import sys

for module_name in (
    "controlstudio_contracts.native_preflight",
    "controlstudio_agent.sidecar",
    "controlstudio_agent.runtime",
):
    try:
        importlib.import_module(module_name)
    except BaseException as exc:
        exception_type = type(exc)
        print(
            "Agent development runtime import preflight failed: "
            f"{exception_type.__module__}.{exception_type.__name__} "
            f"module={module_name}",
            file=sys.stderr,
        )
        raise SystemExit(78)
'@
if (-not (Test-Path -LiteralPath $agentImportPython -PathType Leaf)) {
    throw "Agent runtime import preflight interpreter is missing: $agentImportPython"
}
$agentImportPreflight | & $agentImportPython -E -
if ($LASTEXITCODE -ne 0) {
    throw "Agent development interpreter failed its import preflight: $agentImportPython"
}

foreach ($entry in @(
    @{ Name = 'Platform launcher'; Path = $launcher },
    @{ Name = 'Agent sidecar'; Path = $agentRuntime },
    @{ Name = 'PLC MCP sidecar'; Path = $mcp },
    @{ Name = 'XG5000 CLI'; Path = $xg5000Cli }
)) {
    if (-not (Test-Path -LiteralPath $entry.Path -PathType Leaf)) {
        throw "$($entry.Name) development executable is missing: $($entry.Path)"
    }
}

$env:CONTROLSTUDIO_RELEASE_CHANNEL = 'development'
$env:CONTROLSTUDIO_PRODUCT_RELEASE = 'source-dev'
# Model endpoint, name, timeout, and credentials are intentionally inherited.
# Host bootstrap uses an existing stored model profile when one is available;
# an explicitly supplied environment override remains available to that Host.
$env:CONTROLSTUDIO_AGENT_COMMAND = $agentCommand
$env:CONTROLSTUDIO_MCP_COMMAND = $mcp
$env:CONTROLSTUDIO_XG5000_CLI_COMMAND = $xg5000Cli

$arguments = @('--no-config')
if ($NoOpen) { $arguments += '--no-open' }
if ($Web) { $arguments += '--web' }
if ($WebUrl) { $arguments += '--web-url' }
& $launcher @arguments
exit $LASTEXITCODE
