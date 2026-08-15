[CmdletBinding()]
param(
    [switch]$RequireProductionSignature
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$bomPath = Join-Path $root 'controlstudio.bom.json'
if (-not (Test-Path -LiteralPath $bomPath -PathType Leaf)) { throw 'ControlStudio BOM is missing.' }
$bom = Get-Content -LiteralPath $bomPath -Raw | ConvertFrom-Json
if ($bom.bom_schema -ne 'controlstudio-bom/v1') { throw 'Unsupported ControlStudio BOM schema.' }
if ($bom.release_channel -notin @('development', 'beta', 'production')) { throw 'Unsupported ControlStudio release channel.' }
$RequireProductionSignature = $RequireProductionSignature -or $bom.release_channel -eq 'production'
$names = @('api_contract', 'event_contract', 'agent_contract', 'mcp_product_contract', 'workspace_contract', 'audit_contract')
$ids = @('platform', 'agent', 'plc-mcp')
if (@($bom.components).Count -ne 3) { throw 'BOM must contain exactly three components.' }
$contractArtifact = $bom.contract_artifact
if ($null -eq $contractArtifact) { throw 'Public contracts artifact is missing from BOM.' }
if ([IO.Path]::GetFileName($contractArtifact.artifact) -ne $contractArtifact.artifact) { throw 'Public contracts artifact path escapes portable release.' }
$contractArtifactPath = Join-Path $root $contractArtifact.artifact
if (-not (Test-Path -LiteralPath $contractArtifactPath -PathType Leaf)) { throw 'Public contracts artifact is missing.' }
$contractArtifactHash = (Get-FileHash -LiteralPath $contractArtifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($contractArtifactHash -ne $contractArtifact.sha256.ToLowerInvariant()) { throw 'Public contracts artifact hash mismatch.' }
$executables = @{}
for ($index = 0; $index -lt 3; $index++) {
    $component = $bom.components[$index]
    if ($component.id -ne $ids[$index]) { throw 'BOM component order or identity is invalid.' }
    foreach ($name in $names) {
        if ([string]::IsNullOrWhiteSpace($bom.contracts.$name) -or $component.contracts.$name -ne $bom.contracts.$name) { throw "Contract mismatch in component $($component.id): $name" }
    }
    if ([IO.Path]::GetFileName($component.artifact) -ne $component.artifact) { throw 'Artifact path escapes portable release.' }
    $artifact = Join-Path $root $component.artifact
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) { throw "Artifact missing: $($component.id)" }
    $actualHash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $component.sha256.ToLowerInvariant()) { throw "Artifact hash mismatch: $($component.id)" }
    if ($component.signature.kind -eq 'unsigned') {
        if ($component.signature.status -ne 'not-for-production') { throw 'Invalid unsigned signature metadata.' }
        if ($RequireProductionSignature) { throw 'Unsigned portable release rejected by production signing policy.' }
    } elseif ($component.signature.kind -eq 'authenticode') {
        $signature = Get-AuthenticodeSignature -LiteralPath $artifact
        if ($signature.Status -ne 'Valid' -or $component.signature.status -ne 'verified' -or $signature.SignerCertificate.Thumbprint -ne $component.signature.thumbprint) { throw "Authenticode verification failed: $($component.id)" }
    } else {
        throw "Unsupported self-contained signature mode: $($component.signature.kind)"
    }
    $executables[$component.id] = $artifact
}

# Platform remains the sidecar supervisor; it receives immutable paths and
# creates the stdio transports itself.
$env:CONTROLSTUDIO_AGENT_COMMAND = '"' + $executables['agent'] + '"'
$env:CONTROLSTUDIO_MCP_COMMAND = $executables['plc-mcp']
& $executables['platform']
exit $LASTEXITCODE
