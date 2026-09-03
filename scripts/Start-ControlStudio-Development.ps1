[CmdletBinding()]
param(
    [switch]$NoOpen,
    [switch]$Web,
    [switch]$WebUrl
)

$ErrorActionPreference = 'Stop'
if ((@($NoOpen, $Web, $WebUrl) | Where-Object { $_ }).Count -gt 1) {
    throw 'Specify only one of -NoOpen, -Web, or -WebUrl.'
}
$platform = Split-Path -Parent $PSScriptRoot
$workspace = Split-Path -Parent $platform
$launcher = Join-Path $platform 'launcher\.venv\Scripts\controlstudio.exe'
$agentDevPython = Join-Path $workspace 'gidong-agent\.venv-dev\Scripts\python.exe'
$agentExecutable = Join-Path $workspace 'gidong-agent\.venv\Scripts\controlstudio-agent.exe'
$mcp = Join-Path $workspace 'gidong-plc-mcp\.venv\Scripts\controlstudio-plc-mcp.exe'
$xg5000Cli = Join-Path $workspace 'controlstudio-xg5000-cli\.venv\Scripts\controlstudio-xg5000.exe'

if (Test-Path -LiteralPath $agentDevPython -PathType Leaf) {
    $agentRuntime = $agentDevPython
    $agentCommand = '"' + $agentDevPython + '" -m controlstudio_agent.sidecar'
} else {
    $agentRuntime = $agentExecutable
    $agentCommand = '"' + $agentExecutable + '"'
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
$env:CONTROLSTUDIO_DEVELOPMENT_MODEL_BASE_URL = 'http://100.65.0.1:8978/v1'
$env:CONTROLSTUDIO_DEVELOPMENT_MODEL_NAME = 'nemotron-3-ultra'
$env:CONTROLSTUDIO_DEVELOPMENT_MODEL_TIMEOUT_SECONDS = '300'
$env:CONTROLSTUDIO_DEVELOPMENT_MODEL_API_KEY = 'sk-1234567890'
$env:CONTROLSTUDIO_AGENT_COMMAND = $agentCommand
$env:CONTROLSTUDIO_MCP_COMMAND = $mcp
$env:CONTROLSTUDIO_XG5000_CLI_COMMAND = $xg5000Cli

$arguments = @('--no-config')
if ($NoOpen) { $arguments += '--no-open' }
if ($Web) { $arguments += '--web' }
if ($WebUrl) { $arguments += '--web-url' }
& $launcher @arguments
exit $LASTEXITCODE
