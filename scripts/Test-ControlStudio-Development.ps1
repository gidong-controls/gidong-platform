[CmdletBinding()]
param(
    [switch]$RequireAgent,
    [switch]$KeepRunning
)

$ErrorActionPreference = 'Stop'
$start = Join-Path $PSScriptRoot 'Start-ControlStudio-Development.ps1'
$systemPowerShell = Join-Path `
    $env:SystemRoot `
    'System32\WindowsPowerShell\v1.0\powershell.exe'
$baseUrl = 'http://127.0.0.1:8767'
$sessionToken = $null
$csrfToken = $null
$hostPid = $null
$launcherProcess = $null

function Get-DevelopmentListenerPid {
    $netstat = Join-Path $env:SystemRoot 'System32\netstat.exe'
    $lines = & $netstat -ano -p TCP
    foreach ($line in $lines) {
        if (
            $line -match `
                '^\s*TCP\s+127\.0\.0\.1:8767\s+\S+\s+LISTENING\s+(\d+)\s*$'
        ) {
            return [int]$Matches[1]
        }
    }
    return $null
}

if (Get-DevelopmentListenerPid) {
    throw 'Development smoke requires port 8767 to be free before it starts.'
}

try {
    $launcherProcess = Start-Process `
        -FilePath $systemPowerShell `
        -ArgumentList @(
            '-NoLogo',
            '-NoProfile',
            '-ExecutionPolicy',
            'Bypass',
            '-File',
            $start,
            '-NoOpen'
        ) `
        -WindowStyle Hidden `
        -PassThru

    $startupDeadline = [DateTimeOffset]::UtcNow.AddSeconds(120)
    $health = $null
    while ([DateTimeOffset]::UtcNow -lt $startupDeadline) {
        try {
            $health = Invoke-RestMethod -Uri "$baseUrl/api/health" -TimeoutSec 2
            break
        }
        catch {
            if ($launcherProcess.HasExited -and $launcherProcess.ExitCode -ne 0) {
                throw "Development launcher failed with exit code $($launcherProcess.ExitCode)."
            }
            Start-Sleep -Milliseconds 250
        }
    }
    if ($null -eq $health) {
        throw 'Development Host did not become healthy within 120 seconds.'
    }

    $hostPid = Get-DevelopmentListenerPid
    if ($null -eq $hostPid) {
        throw 'Development Host health was ready without the expected loopback listener.'
    }
    if (
        $health.status -ne 'ready' -or
        $health.database -ne 'ready' -or
        $health.static_ui -ne 'ready'
    ) {
        throw 'Development Host health, database, or static UI is not ready.'
    }

    $tokenPath = Join-Path $env:LOCALAPPDATA 'ControlStudio Development\session.token'
    $launchToken = (Get-Content -LiteralPath $tokenPath -Raw).Trim()
    $handoff = Invoke-RestMethod `
        -Method Post `
        -Uri "$baseUrl/api/launcher/handoff" `
        -Headers @{ Authorization = "Bearer $launchToken" } `
        -TimeoutSec 10
    $browser = Invoke-RestMethod `
        -Method Post `
        -Uri "$baseUrl/api/session" `
        -Headers @{ Authorization = "Bearer $($handoff.handoffTicket)" } `
        -TimeoutSec 10
    $sessionToken = $browser.sessionToken
    $headers = @{ Authorization = "Bearer $sessionToken" }
    $readinessDeadline = [DateTimeOffset]::UtcNow.AddSeconds(120)
    $status = $null
    while ([DateTimeOffset]::UtcNow -lt $readinessDeadline) {
        $status = Invoke-RestMethod `
            -Uri "$baseUrl/api/v1/status" `
            -Headers $headers `
            -TimeoutSec 10
        if (
            $status.host.state -eq 'ready' -and
            $status.mcp.state -eq 'ready' -and
            (-not $RequireAgent -or $status.agent.state -eq 'ready')
        ) {
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (
        $status.host.state -ne 'ready' -or
        $status.mcp.state -ne 'ready' -or
        ($RequireAgent -and $status.agent.state -ne 'ready')
    ) {
        $readinessDetail = if ($RequireAgent -and $status.agent.state -ne 'ready') {
            $status.agent.detail
        }
        else {
            $status.mcp.detail
        }
        throw (
            'Development readiness timed out: Host={0}; MCP={1}; Agent={2}; detail={3}' -f `
                $status.host.state,
                $status.mcp.state,
                $status.agent.state,
                $readinessDetail
        )
    }
    $bootstrap = Invoke-RestMethod `
        -Uri "$baseUrl/api/v1/bootstrap" `
        -Headers $headers `
        -TimeoutSec 30
    $csrfToken = $bootstrap.csrfToken

    if ($status.releaseChannel -ne 'development' -or $status.productRelease -ne 'source-dev') {
        throw 'Development Host reported the wrong release identity.'
    }
    [pscustomobject]@{
        schema = 'controlstudio-development-smoke/v1'
        releaseChannel = $status.releaseChannel
        productRelease = $status.productRelease
        host = $status.host.state
        database = $health.database
        staticUi = $health.static_ui
        agent = $status.agent.state
        agentRequired = [bool]$RequireAgent
        mcp = $status.mcp.state
        mcpToolCount = $status.mcp.toolCount
        mcpAgentToolCount = $status.mcp.agentToolCount
        result = 'passed'
    } | ConvertTo-Json -Depth 3
}
finally {
    if (-not $KeepRunning) {
        if ($sessionToken -and $csrfToken) {
            try {
                Invoke-RestMethod `
                    -Method Post `
                    -Uri "$baseUrl/api/v1/host/shutdown" `
                    -Headers @{
                        Authorization = "Bearer $sessionToken"
                        'X-ControlStudio-CSRF' = $csrfToken
                    } `
                    -TimeoutSec 10 | Out-Null
            }
            catch {
                # The exact process captured from the fresh 8767 listener is the
                # cleanup authority when graceful shutdown did not answer.
            }
        }
        Start-Sleep -Milliseconds 500
        $remainingPid = Get-DevelopmentListenerPid
        if ($remainingPid -and $hostPid -and $remainingPid -eq $hostPid) {
            Stop-Process -Id $hostPid -Force
        }
    }
}
