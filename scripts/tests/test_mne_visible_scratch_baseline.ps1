[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$certifier = Join-Path (Split-Path -Parent $PSScriptRoot) 'Start-ControlStudio-Mne-Certification.ps1'
$source = [IO.File]::ReadAllText($certifier, [Text.Encoding]::UTF8)
$match = [regex]::Match(
    $source,
    '(?s)(function Assert-ExactPreservedXgBaseline \{.*?\n\})\n\nfunction Assert-ResumeAfterHiddenScratchLaunch'
)
if (-not $match.Success) { throw 'Could not extract the preserved-baseline guard for focused testing' }
Invoke-Expression $match.Groups[1].Value

function New-FakeXgProcess {
    param([int]$ProcessId, [string]$StartedUtc)

    $process = [pscustomobject]@{
        Id = $ProcessId
        ProcessName = 'XG5000'
        StartTime = [DateTime]::Parse($StartedUtc, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind)
    }
    $process | Add-Member -MemberType ScriptMethod -Name Refresh -Value { }
    return $process
}

$script:mockXgProcesses = @()
function Get-Process {
    [CmdletBinding()]
    param([string]$Name)
    if ($Name -cne 'XG5000') { throw 'Focused test only permits the XG5000 process query' }
    return @($script:mockXgProcesses)
}

$authorizationJson = @'
{
  "schema": "controlstudio-mne-resume-after-visible-scratch/v4",
  "preservedBaseline": [
    { "Pid": 14284, "ProcessName": "XG5000", "StartUtc": "2026-08-31T08:57:58.7651004Z" },
    { "Pid": 16228, "ProcessName": "XG5000", "StartUtc": "2026-08-31T10:54:12.7899604Z" }
  ]
}
'@
$authorization = $authorizationJson | ConvertFrom-Json -DateKind String
if ($authorization.preservedBaseline[0].StartUtc -isnot [string] -or $authorization.preservedBaseline[0].StartUtc -cne '2026-08-31T08:57:58.7651004Z') {
    throw 'Authorization parser did not preserve canonical StartUtc JSON text'
}
$baseline = @($authorization.preservedBaseline)
$reviewedPid = 21860
$script:mockXgProcesses = @(
    (New-FakeXgProcess 14284 '2026-08-31T08:57:58.7651004Z'),
    (New-FakeXgProcess 16228 '2026-08-31T10:54:12.7899604Z'),
    (New-FakeXgProcess $reviewedPid '2026-09-02T09:58:33.6800060Z')
)
$preClose = @(Assert-ExactPreservedXgBaseline -Baseline $baseline -ReviewedPid $reviewedPid -RequireReviewedProcess)
if ($preClose.Count -ne 2) { throw 'Pre-close baseline guard did not retain both user sessions' }

$script:mockXgProcesses = @(
    (New-FakeXgProcess 14284 '2026-08-31T08:57:58.7651004Z'),
    (New-FakeXgProcess 16228 '2026-08-31T10:54:12.7899604Z')
)
$postClose = @(Assert-ExactPreservedXgBaseline -Baseline $baseline -ReviewedPid $reviewedPid)
if ($postClose.Count -ne 2) { throw 'Post-close baseline guard did not retain both user sessions' }

$script:mockXgProcesses += New-FakeXgProcess 77777 '2026-09-02T10:10:10.0000000Z'
try {
    [void](Assert-ExactPreservedXgBaseline -Baseline $baseline -ReviewedPid $reviewedPid)
    throw 'Unknown extra XG5000 PID was accepted'
} catch {
    if ($_.Exception.Message -eq 'Unknown extra XG5000 PID was accepted') { throw }
}

Write-Output 'Visible scratch preserved-baseline focused test: OK'
