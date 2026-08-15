[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$bomPath = Join-Path $source 'controlstudio.bom.json'
if (-not (Test-Path -LiteralPath $bomPath -PathType Leaf)) { throw 'ControlStudio installer BOM is missing.' }
$bom = Get-Content -LiteralPath $bomPath -Raw | ConvertFrom-Json
if ($bom.bom_schema -ne 'controlstudio-bom/v1') { throw 'Unsupported ControlStudio installer BOM schema.' }
if ($bom.release_channel -notin @('beta', 'production')) { throw 'ControlStudio installer channel must be beta or production.' }
$isProduction = $bom.release_channel -eq 'production'
$ids = @('platform', 'agent', 'plc-mcp')
if (@($bom.components).Count -ne 3) { throw 'ControlStudio installer component set is incomplete.' }
$verified = @{}
for ($index = 0; $index -lt 3; $index++) {
    $component = $bom.components[$index]
    if ($component.id -ne $ids[$index]) { throw 'ControlStudio installer component order is invalid.' }
    if ([IO.Path]::GetFileName($component.artifact) -ne $component.artifact) { throw 'Installer artifact path is invalid.' }
    $artifact = Join-Path $source $component.artifact
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) { throw "Installer artifact is missing: $($component.id)" }
    $hash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $component.sha256.ToLowerInvariant()) { throw "Installer artifact hash mismatch: $($component.id)" }
    if ($isProduction) {
        if ($component.signature.kind -ne 'authenticode' -or $component.signature.status -ne 'verified') { throw "Production artifact is not signed: $($component.id)" }
        $signature = Get-AuthenticodeSignature -LiteralPath $artifact
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $component.signature.thumbprint) { throw "Production Authenticode verification failed: $($component.id)" }
    }
    $verified[$component.id] = $artifact
}
$contract = $bom.contract_artifact
if ($null -eq $contract -or [IO.Path]::GetFileName($contract.artifact) -ne $contract.artifact) { throw 'Installer public contracts artifact is invalid.' }
$contractPath = Join-Path $source $contract.artifact
if (-not (Test-Path -LiteralPath $contractPath -PathType Leaf)) { throw 'Installer public contracts artifact is missing.' }
$contractHash = (Get-FileHash -LiteralPath $contractPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($contractHash -ne $contract.sha256.ToLowerInvariant()) { throw 'Installer public contracts artifact hash mismatch.' }

if ([string]::IsNullOrWhiteSpace($env:CONTROLSTUDIO_INSTALL_ROOT)) {
    $installName = if ($isProduction) { 'ControlStudio' } else { 'ControlStudio Beta' }
    $installRoot = Join-Path $env:LOCALAPPDATA ("Programs\" + $installName)
} else {
    $installRoot = [IO.Path]::GetFullPath($env:CONTROLSTUDIO_INSTALL_ROOT)
}
$executableName = if ($isProduction) { 'ControlStudio.exe' } else { 'ControlStudio Beta.exe' }

function Assert-InstalledLayout {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)]$ExpectedBom
    )

    $runtime = Join-Path $Root 'runtime'
    $installedBomPath = Join-Path $runtime 'controlstudio.bom.json'
    if (-not (Test-Path -LiteralPath $installedBomPath -PathType Leaf)) {
        throw "Installed ControlStudio BOM is missing: $Root"
    }
    $installedBom = Get-Content -LiteralPath $installedBomPath -Raw | ConvertFrom-Json
    if (
        $installedBom.bom_schema -ne 'controlstudio-bom/v1' -or
        $installedBom.release_channel -ne $ExpectedBom.release_channel
    ) {
        throw "Installed ControlStudio channel or BOM schema does not match: $Root"
    }
    if (@($installedBom.components).Count -ne $ids.Count) {
        throw "Installed ControlStudio component set is incomplete: $Root"
    }
    foreach ($name in @('api_contract', 'event_contract', 'agent_contract', 'mcp_product_contract', 'workspace_contract', 'audit_contract')) {
        if ([string]::IsNullOrWhiteSpace([string]$installedBom.contracts.$name)) {
            throw "Installed ControlStudio BOM contract is missing: $name"
        }
    }
    for ($index = 0; $index -lt $ids.Count; $index++) {
        $component = $installedBom.components[$index]
        if ($component.id -ne $ids[$index]) {
            throw "Installed ControlStudio component order or identity is invalid: $Root"
        }
        if ([IO.Path]::GetFileName($component.artifact) -ne $component.artifact) {
            throw "Installed ControlStudio component artifact path is invalid: $($component.id)"
        }
        foreach ($name in @('api_contract', 'event_contract', 'agent_contract', 'mcp_product_contract', 'workspace_contract', 'audit_contract')) {
            if ($component.contracts.$name -ne $installedBom.contracts.$name) {
                throw "Installed ControlStudio component contract mismatch: $($component.id)"
            }
        }
        $path = switch ($component.id) {
            'platform' { Join-Path $Root $executableName }
            'agent' { Join-Path $runtime 'controlstudio-agent.exe' }
            'plc-mcp' { Join-Path $runtime 'controlstudio-plc-mcp.exe' }
            default { throw "Installed ControlStudio component is invalid: $($component.id)" }
        }
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Installed ControlStudio component is missing: $($component.id)"
        }
        $actualHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne $component.sha256.ToLowerInvariant()) {
            throw "Installed ControlStudio component hash mismatch: $($component.id)"
        }
        if ($installedBom.release_channel -eq 'production') {
            $signature = Get-AuthenticodeSignature -LiteralPath $path
            if (
                $component.signature.kind -ne 'authenticode' -or
                $component.signature.status -ne 'verified' -or
                $signature.Status -ne 'Valid' -or
                $signature.SignerCertificate.Thumbprint -ne $component.signature.thumbprint
            ) {
                throw "Installed production signature mismatch: $($component.id)"
            }
        }
    }
    $installedContract = $installedBom.contract_artifact
    if (
        $null -eq $installedContract -or
        [IO.Path]::GetFileName($installedContract.artifact) -ne $installedContract.artifact
    ) {
        throw 'Installed public contracts artifact path is invalid.'
    }
    $installedContractPath = Join-Path $runtime $installedContract.artifact
    if (-not (Test-Path -LiteralPath $installedContractPath -PathType Leaf)) {
        throw 'Installed public contracts artifact is missing.'
    }
    $installedContractHash = (
        Get-FileHash -LiteralPath $installedContractPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($installedContractHash -ne $installedContract.sha256.ToLowerInvariant()) {
        throw 'Installed public contracts artifact hash mismatch.'
    }
    return $installedBom
}

function Assert-ControlStudioStopped {
    param([Parameter(Mandatory = $true)][string]$Root)

    $runtime = Join-Path $Root 'runtime'
    $ownedPaths = @(
        (Join-Path $Root $executableName),
        (Join-Path $runtime 'controlstudio-agent.exe'),
        (Join-Path $runtime 'controlstudio-plc-mcp.exe')
    ) | ForEach-Object { [IO.Path]::GetFullPath($_) }
    foreach ($process in Get-Process -ErrorAction SilentlyContinue) {
        try { $processPath = $process.Path } catch { continue }
        if (
            $processPath -and
            $ownedPaths -contains [IO.Path]::GetFullPath($processPath)
        ) {
            throw 'Close ControlStudio before installing this update.'
        }
    }
}

function Test-PathOverlap {
    param(
        [Parameter(Mandatory = $true)][string]$First,
        [Parameter(Mandatory = $true)][string]$Second
    )

    $firstFull = [IO.Path]::GetFullPath($First).TrimEnd('\', '/')
    $secondFull = [IO.Path]::GetFullPath($Second).TrimEnd('\', '/')
    return (
        $firstFull.Equals($secondFull, [StringComparison]::OrdinalIgnoreCase) -or
        $firstFull.StartsWith($secondFull + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $secondFull.StartsWith($firstFull + '\', [StringComparison]::OrdinalIgnoreCase)
    )
}

$installRoot = [IO.Path]::GetFullPath($installRoot)
$installParent = Split-Path -Parent $installRoot
$installLeaf = Split-Path -Leaf $installRoot
if ([string]::IsNullOrWhiteSpace($installParent) -or [string]::IsNullOrWhiteSpace($installLeaf)) {
    throw 'ControlStudio install location is invalid.'
}
$dataRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA $installName))
if (Test-PathOverlap -First $installRoot -Second $dataRoot) {
    throw 'ControlStudio install location must not overlap the user data directory.'
}
New-Item -ItemType Directory -Path $installParent -Force | Out-Null
$stagingRoot = Join-Path $installParent ($installLeaf + '.staging-' + [guid]::NewGuid().ToString('N'))
$previousRoot = Join-Path $installParent ($installLeaf + '.previous')
$stagingRuntime = Join-Path $stagingRoot 'runtime'

try {
    New-Item -ItemType Directory -Path $stagingRuntime -Force | Out-Null
    Copy-Item -LiteralPath $verified.platform -Destination (Join-Path $stagingRoot $executableName)
    Copy-Item -LiteralPath $verified.agent -Destination (Join-Path $stagingRuntime 'controlstudio-agent.exe')
    Copy-Item -LiteralPath $verified.'plc-mcp' -Destination (Join-Path $stagingRuntime 'controlstudio-plc-mcp.exe')
    Copy-Item -LiteralPath $contractPath -Destination (Join-Path $stagingRuntime $contract.artifact)
    Copy-Item -LiteralPath $bomPath -Destination (Join-Path $stagingRuntime 'controlstudio.bom.json')
    $null = Assert-InstalledLayout -Root $stagingRoot -ExpectedBom $bom

    if (Test-Path -LiteralPath $installRoot) {
        $installedBom = Assert-InstalledLayout -Root $installRoot -ExpectedBom $bom
        Assert-ControlStudioStopped -Root $installRoot
        if ($installedBom.release_channel -ne $bom.release_channel) {
            throw 'ControlStudio updates cannot change release channels in place.'
        }
        if (Test-Path -LiteralPath $previousRoot) {
            $resolvedPrevious = [IO.Path]::GetFullPath($previousRoot)
            if ((Split-Path -Parent $resolvedPrevious) -ne $installParent) {
                throw 'ControlStudio previous-version path escaped the install parent.'
            }
            $null = Assert-InstalledLayout -Root $resolvedPrevious -ExpectedBom $bom
            Assert-ControlStudioStopped -Root $resolvedPrevious
            Remove-Item -LiteralPath $resolvedPrevious -Recurse -Force
        }
        Move-Item -LiteralPath $installRoot -Destination $previousRoot
        try {
            Move-Item -LiteralPath $stagingRoot -Destination $installRoot
            $null = Assert-InstalledLayout -Root $installRoot -ExpectedBom $bom
        } catch {
            if (Test-Path -LiteralPath $installRoot) {
                Remove-Item -LiteralPath $installRoot -Recurse -Force
            }
            Move-Item -LiteralPath $previousRoot -Destination $installRoot
            throw
        }
    } else {
        try {
            Move-Item -LiteralPath $stagingRoot -Destination $installRoot
            $null = Assert-InstalledLayout -Root $installRoot -ExpectedBom $bom
        } catch {
            if (Test-Path -LiteralPath $installRoot) {
                Remove-Item -LiteralPath $installRoot -Recurse -Force
            }
            throw
        }
    }
} finally {
    if (Test-Path -LiteralPath $stagingRoot) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
}

if ($env:CONTROLSTUDIO_SKIP_SHORTCUTS -ne '1') {
    $shortcutName = if ($isProduction) { 'ControlStudio' } else { 'ControlStudio Beta' }
    $shortcutDirectory = Join-Path $env:APPDATA ("Microsoft\Windows\Start Menu\Programs\" + $shortcutName)
    New-Item -ItemType Directory -Path $shortcutDirectory -Force | Out-Null
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path $shortcutDirectory ($shortcutName + '.lnk')))
    $shortcut.TargetPath = Join-Path $installRoot $executableName
    $shortcut.WorkingDirectory = $installRoot
    $shortcut.Save()
}
Write-Output "ControlStudio installed or updated: $installRoot"
