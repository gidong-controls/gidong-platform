[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$SourceRepo,
    [Parameter(Mandatory)][string]$Workspace,
    [Parameter(Mandatory)][string]$EvidenceDirectory,
    [string]$PlatformCandidate,
    [string]$AgentCandidate,
    [string]$McpCandidate,
    [string]$Bundle,
    [string]$Mirror,
    [string]$Bom,
    [string]$ArtifactRoot,
    [string]$ExecutionManifest
)

$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $PSCommandPath
$audit = Join-Path $scriptRoot 'refactor_audit.py'
$policy = Join-Path $scriptRoot 'partition-policy.json'
New-Item -ItemType Directory -Force -Path $EvidenceDirectory | Out-Null

function Invoke-Audit([string[]]$Arguments, [string]$Name) {
    & python $audit @Arguments --evidence (Join-Path $EvidenceDirectory "$Name.json")
    if ($LASTEXITCODE -ne 0) { throw "Refactor gate failed: $Name" }
}

# Stage 0 and stage 5 dry run.  This script contains no git init/clone/remote/push operation.
Invoke-Audit @('baseline', '--repo', $SourceRepo) '00-baseline'
if (($Bundle -and -not $Mirror) -or ($Mirror -and -not $Bundle)) { throw 'Bundle and Mirror must be supplied together.' }
if ($Bundle) { Invoke-Audit @('archive-verify', '--source', $SourceRepo, '--bundle', $Bundle, '--mirror', $Mirror) '00-archive-verify' }
Invoke-Audit @('sibling-plan', '--source', $SourceRepo, '--workspace', $Workspace) '05-sibling-plan'
Invoke-Audit @('snapshot-plan', '--source', $SourceRepo, '--workspace', $Workspace, '--layout', (Join-Path $scriptRoot 'snapshot-layout.json')) '05-snapshot-plan'

if ($PlatformCandidate) { Invoke-Audit @('partition', '--candidate', $PlatformCandidate, '--component', 'platform', '--policy', $policy) '05-platform-partition' }
if ($AgentCandidate) { Invoke-Audit @('partition', '--candidate', $AgentCandidate, '--component', 'agent', '--policy', $policy) '05-agent-partition' }
if ($McpCandidate) { Invoke-Audit @('partition', '--candidate', $McpCandidate, '--component', 'plc-mcp', '--policy', $policy) '05-mcp-partition' }
if ($PlatformCandidate) { Invoke-Audit @('public-history', '--repo', $PlatformCandidate) '11-public-history' }
if ($Bom) {
    $bomArguments = @('verify-bom', '--bom', $Bom)
    if ($ArtifactRoot) { $bomArguments += @('--artifact-root', $ArtifactRoot) }
    Invoke-Audit $bomArguments '06-bom'
}
if ($ExecutionManifest) { Invoke-Audit @('run-manifest', '--manifest', $ExecutionManifest) '14-build-and-packaged-e2e' }

Write-Host "Evidence written to $EvidenceDirectory. No sibling repositories were created."
