[CmdletBinding()]
param(
    [switch]$NoOpen,
    [switch]$Web,
    [switch]$WebUrl,
    [switch]$WebDev,
    [string]$ProfileRoot,
    [string]$WebHandoffFile
)

$ErrorActionPreference = 'Stop'
if ($WebHandoffFile) { $WebUrl = $true }
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

if ($WebDev) {
    $webRoot = Join-Path $platform 'web'
    $viteEntry = Join-Path $webRoot 'node_modules\vite\bin\vite.js'
    $node = (Get-Command node.exe -ErrorAction Stop).Source
    if (-not (Test-Path -LiteralPath $viteEntry -PathType Leaf)) {
        throw 'Install web dependencies with npm ci before starting -WebDev.'
    }
    $listeners = @(Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0) {
        foreach ($listener in $listeners) {
            $viteProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
            $commandLine = [string]$viteProcess.CommandLine
            if (-not $viteProcess -or
                $viteProcess.ExecutablePath -ne $node -or
                $commandLine.Replace('/', '\').IndexOf($webRoot, [StringComparison]::OrdinalIgnoreCase) -lt 0 -or
                $commandLine.Replace('/', '\') -notmatch 'vite\\bin\\vite\.js') {
                throw 'Port 5173 belongs to another process. Stop that process or use the built web UI.'
            }
        }
    } else {
        $logRoot = Join-Path $webRoot '.controlstudio-dev'
        New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
        $viteProcess = Start-Process -FilePath $node -ArgumentList @(('"' + $viteEntry + '"')) `
            -WorkingDirectory $webRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $logRoot 'vite.stdout.log') `
            -RedirectStandardError (Join-Path $logRoot 'vite.stderr.log')
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        $response = $null
        do {
            $viteProcess.Refresh()
            if ($viteProcess.HasExited) { throw 'The development web server did not start. Check web/.controlstudio-dev logs.' }
            try {
                $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:5173/@vite/client' -TimeoutSec 2
                if ($response.StatusCode -eq 200) { break }
            } catch {}
            Start-Sleep -Milliseconds 200
        } while ([DateTime]::UtcNow -lt $deadline)
        if (-not $response -or $response.StatusCode -ne 200) { throw 'The development web server did not become ready on port 5173.' }
    }
    if (-not ($NoOpen -or $Web -or $WebUrl)) { $Web = $true }
}

$arguments = @('--no-config')
if ($NoOpen) { $arguments += '--no-open' }
if ($Web) { $arguments += '--web' }
if ($WebUrl) { $arguments += '--web-url' }
if ($WebHandoffFile) { $arguments += @('--web-url-file', ([IO.Path]::GetFullPath($WebHandoffFile))) }
if ($WebDev -and -not $NoOpen) { $arguments += '--development-web' }
& $launcher @arguments
exit $LASTEXITCODE
