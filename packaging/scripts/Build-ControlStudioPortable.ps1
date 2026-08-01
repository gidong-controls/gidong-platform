[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InputJson,
    [Parameter(Mandatory = $true)][string]$ArtifactsDirectory,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [string]$DevelopmentSigningKey,
    [switch]$RequireProductionSignature
)

$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$bom = Join-Path $OutputDirectory 'controlstudio.bom.json'
$buildArgs = @((Join-Path $scriptRoot 'build_bom.py'), '--input', $InputJson, '--output', $bom)
if ($DevelopmentSigningKey) { $buildArgs += @('--development-signing-key', $DevelopmentSigningKey) }
& python @buildArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$assembleArgs = @((Join-Path $scriptRoot 'assemble_portable.py'), '--bom', $bom, '--artifacts', $ArtifactsDirectory, '--output', (Join-Path $OutputDirectory 'portable'))
if ($RequireProductionSignature) { $assembleArgs += '--require-production-signature' }
& python @assembleArgs
exit $LASTEXITCODE
