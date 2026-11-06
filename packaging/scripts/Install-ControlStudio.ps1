[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

trap {
    $message = "ControlStudio setup could not finish.`r`n`r`n" + $_.Exception.Message
    try {
        $setupShell = New-Object -ComObject WScript.Shell
        $null = $setupShell.Popup($message, 0, 'ControlStudio Setup', 16)
    } catch {
        [Console]::Error.WriteLine($message)
    }
    exit 1
}

function Get-InstallerSha256 {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    $stream = $null
    $algorithm = $null
    try {
        $stream = [IO.File]::OpenRead($LiteralPath)
        $algorithm = [Security.Cryptography.SHA256]::Create()
        $digest = $algorithm.ComputeHash($stream)
        return [BitConverter]::ToString($digest).Replace('-', '').ToLowerInvariant()
    } finally {
        if ($null -ne $algorithm) { $algorithm.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$bomPath = Join-Path $source 'controlstudio.bom.json'
if (-not (Test-Path -LiteralPath $bomPath -PathType Leaf)) { throw 'ControlStudio installer BOM is missing.' }
$bom = Get-Content -LiteralPath $bomPath -Raw | ConvertFrom-Json
if ($bom.bom_schema -ne 'controlstudio-bom/v1') { throw 'Unsupported ControlStudio installer BOM schema.' }
if ($bom.release_channel -notin @('beta', 'production')) { throw 'ControlStudio installer channel must be beta or production.' }
$isProduction = $bom.release_channel -eq 'production'
$ids = @('platform', 'agent', 'plc-mcp', 'xg5000-cli')
if (@($bom.components).Count -ne $ids.Count) { throw 'ControlStudio installer component set is incomplete.' }
$verified = @{}
for ($index = 0; $index -lt $ids.Count; $index++) {
    $component = $bom.components[$index]
    if ($component.id -ne $ids[$index]) { throw 'ControlStudio installer component order is invalid.' }
    if ([IO.Path]::GetFileName($component.artifact) -ne $component.artifact) { throw 'Installer artifact path is invalid.' }
    $artifact = Join-Path $source $component.artifact
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) { throw "Installer artifact is missing: $($component.id)" }
    $hash = Get-InstallerSha256 -LiteralPath $artifact
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
$contractHash = Get-InstallerSha256 -LiteralPath $contractPath
if ($contractHash -ne $contract.sha256.ToLowerInvariant()) { throw 'Installer public contracts artifact hash mismatch.' }

$installName = if ($isProduction) { 'ControlStudio' } else { 'ControlStudio Beta' }
$executableName = if ($isProduction) { 'ControlStudio.exe' } else { 'ControlStudio Beta.exe' }

function Assert-InstalledLayout {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)]$ExpectedBom,
        [switch]$AllowPreviousLayout
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
    $installedIds = $ids
    if ($AllowPreviousLayout -and @($installedBom.components).Count -eq 3) {
        $installedIds = @('platform', 'agent', 'plc-mcp')
    }
    if (@($installedBom.components).Count -ne $installedIds.Count) {
        throw "Installed ControlStudio component set is incomplete: $Root"
    }
    foreach ($name in @('api_contract', 'event_contract', 'agent_contract', 'mcp_product_contract', 'workspace_contract', 'audit_contract')) {
        if ([string]::IsNullOrWhiteSpace([string]$installedBom.contracts.$name)) {
            throw "Installed ControlStudio BOM contract is missing: $name"
        }
    }
    for ($index = 0; $index -lt $installedIds.Count; $index++) {
        $component = $installedBom.components[$index]
        if ($component.id -ne $installedIds[$index]) {
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
            'xg5000-cli' { Join-Path $runtime 'controlstudio-xg5000-cli.exe' }
            default { throw "Installed ControlStudio component is invalid: $($component.id)" }
        }
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Installed ControlStudio component is missing: $($component.id)"
        }
        $actualHash = Get-InstallerSha256 -LiteralPath $path
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
    $installedContractHash = Get-InstallerSha256 -LiteralPath $installedContractPath
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
        (Join-Path $runtime 'controlstudio-plc-mcp.exe'),
        (Join-Path $runtime 'controlstudio-xg5000-cli.exe')
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

function Get-ControlStudioUninstallKeyName {
    param([Parameter(Mandatory = $true)][string]$Channel)

    switch ($Channel) {
        'production' { return 'ControlStudio' }
        'beta' { return 'ControlStudioBeta' }
        default { throw 'ControlStudio installer channel is invalid.' }
    }
}

function Get-ControlStudioUninstallRegistryPath {
    param([Parameter(Mandatory = $true)][string]$KeyName)

    return ('Software\Microsoft\Windows\CurrentVersion\Uninstall\' + $KeyName)
}

function Get-ControlStudioInstallRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Channel,
        [Parameter(Mandatory = $true)][string]$DefaultRoot,
        [AllowEmptyString()][string]$ExplicitRoot,
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$RegistryEntries
    )

    if (-not [string]::IsNullOrWhiteSpace($ExplicitRoot)) {
        return [IO.Path]::GetFullPath($ExplicitRoot)
    }
    $keyName = Get-ControlStudioUninstallKeyName -Channel $Channel
    if ($RegistryEntries.Contains($keyName)) {
        $registeredRoot = [string]$RegistryEntries[$keyName].InstallLocation
        if ([string]::IsNullOrWhiteSpace($registeredRoot)) {
            throw 'Existing ControlStudio uninstall registration has no install location.'
        }
        return [IO.Path]::GetFullPath($registeredRoot)
    }
    return [IO.Path]::GetFullPath($DefaultRoot)
}

function Assert-ControlStudioUninstallRegistrationCompatible {
    param([Parameter(Mandatory = $true)][string]$Root, [Parameter(Mandatory = $true)][string]$Channel)

    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $expectedKey = Get-ControlStudioUninstallKeyName -Channel $Channel
    $registrations = Get-ControlStudioRegistryEntries
    foreach ($keyName in @('ControlStudio', 'ControlStudioBeta')) {
        if (-not $registrations.ContainsKey($keyName)) { continue }
        $registration = $registrations[$keyName]
        $registeredRoot = [string]$registration.InstallLocation
        if ([string]::IsNullOrWhiteSpace($registeredRoot)) {
            if ($keyName -eq $expectedKey) { throw 'Existing ControlStudio uninstall registration has no install location.' }
            continue
        }
        $registeredFull = [IO.Path]::GetFullPath($registeredRoot).TrimEnd('\', '/')
        if ($keyName -ne $expectedKey -and $registeredFull.Equals($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'A different ControlStudio channel is registered at this install root.'
        }
        if ($keyName -eq $expectedKey) {
            if (-not $registeredFull.Equals($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'The ControlStudio uninstall registration points to a different install root.'
            }
            if ([string]$registration.ControlStudioChannel -ne $Channel) {
                throw 'The ControlStudio uninstall registration channel is inconsistent.'
            }
        }
    }
}

function Set-ControlStudioUninstallRegistration {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)]$ExpectedBom,
        [Parameter(Mandatory = $true)][string]$ExecutableName
    )

    $channel = [string]$ExpectedBom.release_channel
    $keyName = Get-ControlStudioUninstallKeyName -Channel $channel
    $registryPath = Get-ControlStudioUninstallRegistryPath -KeyName $keyName
    $expectedValues = @{
        DisplayName = if ($channel -eq 'production') { 'ControlStudio' } else { 'ControlStudio Beta' }
        DisplayVersion = [string]$ExpectedBom.product_release
        InstallLocation = [IO.Path]::GetFullPath($Root)
        DisplayIcon = (Join-Path $Root $ExecutableName) + ',0'
        UninstallString = ('"{0}" -NoProfile -ExecutionPolicy Bypass -File "{1}"' -f (Join-Path $PSHOME 'powershell.exe'), (Join-Path $Root 'Uninstall-ControlStudio.ps1'))
        ControlStudioChannel = $channel
        ControlStudioBomSha256 = Get-InstallerSha256 -LiteralPath (Join-Path $Root 'runtime\controlstudio.bom.json')
        ControlStudioUninstallerSha256 = Get-InstallerSha256 -LiteralPath (Join-Path $Root 'Uninstall-ControlStudio.ps1')
        ControlStudioUninstallerCoreSha256 = Get-InstallerSha256 -LiteralPath (Join-Path $Root 'Uninstall-ControlStudio.Core.psm1')
    }
    $valueKinds = @{
        NoModify = [Microsoft.Win32.RegistryValueKind]::DWord
        NoRepair = [Microsoft.Win32.RegistryValueKind]::DWord
    }
    $expectedValues['NoModify'] = 1
    $expectedValues['NoRepair'] = 1
    $managedNames = @($expectedValues.Keys)
    $oldValues = @{}
    $oldKinds = @{}
    $oldKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($registryPath, $false)
    $keyExisted = $null -ne $oldKey
    if ($keyExisted) {
        try {
            foreach ($name in $managedNames) {
                if ($oldKey.GetValueNames() -contains $name) {
                    $oldValues[$name] = $oldKey.GetValue($name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
                    $oldKinds[$name] = $oldKey.GetValueKind($name)
                }
            }
        } finally {
            $oldKey.Dispose()
        }
    }
    $key = $null
    try {
        $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($registryPath)
        if ($null -eq $key) { throw 'Windows did not create the ControlStudio uninstall registration.' }
        foreach ($name in $managedNames) {
            $kind = if ($valueKinds.ContainsKey($name)) { $valueKinds[$name] } else { [Microsoft.Win32.RegistryValueKind]::String }
            $key.SetValue($name, $expectedValues[$name], $kind)
        }
        $key.Flush()
    } catch {
        $registrationError = $_
        if ($null -ne $key) { $key.Dispose(); $key = $null }
        try {
            if ($keyExisted) {
                $restore = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($registryPath, $true)
                if ($null -eq $restore) { throw 'Previous ControlStudio uninstall registration could not be reopened.' }
                try {
                    foreach ($name in $managedNames) {
                        if ($oldValues.ContainsKey($name)) {
                            $restore.SetValue($name, $oldValues[$name], $oldKinds[$name])
                        } else {
                            $restore.DeleteValue($name, $false)
                        }
                    }
                    $restore.Flush()
                } finally {
                    $restore.Dispose()
                }
            } else {
                [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKey($registryPath, $false)
            }
        } catch {
            throw "ControlStudio uninstall registration failed and could not restore its prior values: $($_.Exception.Message)"
        }
        throw $registrationError
    } finally {
        if ($null -ne $key) { $key.Dispose() }
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

function Invoke-ControlStudioInstalledUpdate {
    param([string]$Root, [string]$Staging, [string]$Parent, [string]$HistoricalRoot, $ExpectedBom, [System.Collections.IDictionary]$RegistryValues)

    $oldLayout = Get-ControlStudioRetainedLayout -Root $Root -Channel ([string]$ExpectedBom.release_channel) -RegistryValues $RegistryValues
    if ($oldLayout.ownership -ne 'verified') { throw 'ControlStudio current layout must be fully verified before updating.' }
    $historical = Get-ControlStudioRetainedLayout -Root $HistoricalRoot -Channel ([string]$ExpectedBom.release_channel) -RegistryValues $RegistryValues
    $inventory = @(Get-ControlStudioLayoutInventory -Root $Root)
    $backup = Join-Path $Parent ((Split-Path -Leaf $Root) + '.rollback-' + [guid]::NewGuid().ToString('N'))
    $backupMoved = $false
    $newMoved = $false
    try {
        Assert-ControlStudioLayoutInventory -Root $Root -Expected $inventory
        Move-ControlStudioInstallDirectory -SourceRoot $Root -DestinationRoot $backup -Parent $Parent
        $backupMoved = $true
        Assert-ControlStudioLayoutInventory -Root $backup -Expected $inventory
        Move-ControlStudioInstallDirectory -SourceRoot $Staging -DestinationRoot $Root -Parent $Parent
        $newMoved = $true
        $null = Assert-InstalledLayout -Root $Root -ExpectedBom $ExpectedBom
        Set-ControlStudioUninstallRegistration -Root $Root -ExpectedBom $ExpectedBom -ExecutableName $executableName
    } catch {
        $updateError = $_
        if ($backupMoved) {
            # Changed backup or unexpected current paths cannot be overwritten to claim rollback.
            Assert-ControlStudioLayoutInventory -Root $backup -Expected $inventory
            if ($newMoved) {
                $failedRoot = Join-Path $Parent ((Split-Path -Leaf $Root) + '.failed-' + [guid]::NewGuid().ToString('N'))
                Move-ControlStudioInstallDirectory -SourceRoot $Root -DestinationRoot $failedRoot -Parent $Parent
            }
            Move-ControlStudioInstallDirectory -SourceRoot $backup -DestinationRoot $Root -Parent $Parent
            Assert-ControlStudioLayoutInventory -Root $Root -Expected $inventory
        }
        throw $updateError
    }
    # Registration is committed; cleanup only owns exact verified hashes.
    foreach ($layout in @($oldLayout, $historical)) {
        $cleanupRoot = if ($layout -eq $oldLayout) { $backup } else { [string]$layout.root }
        try {
            if ($layout.ownership -eq 'verified') {
                Assert-ControlStudioStopped -Root $cleanupRoot
                Remove-ControlStudioVerifiedLayoutPayload -Root $cleanupRoot -Files @($layout.files)
                foreach ($directory in @((Join-Path $cleanupRoot 'runtime'), $cleanupRoot)) {
                    try { if (Test-Path -LiteralPath $directory -PathType Container) { [IO.Directory]::Delete($directory, $false) } } catch { }
                }
            }
            $null = Protect-ControlStudioResidualLayout -Root $cleanupRoot
        } catch { Write-Warning 'ControlStudio update committed; historical cleanup remains preserved and unconfirmed.' }
    }
}

Import-Module -Name (Join-Path $source 'Uninstall-ControlStudio.Core.psm1') -Force
$existingRegistrations = Get-ControlStudioRegistryEntries
$installRoot = Get-ControlStudioInstallRoot -Channel ([string]$bom.release_channel) `
    -DefaultRoot (Join-Path $env:LOCALAPPDATA ("Programs\" + $installName)) `
    -ExplicitRoot $env:CONTROLSTUDIO_INSTALL_ROOT -RegistryEntries $existingRegistrations
$installParent = Split-Path -Parent $installRoot
$installLeaf = Split-Path -Leaf $installRoot
if ([string]::IsNullOrWhiteSpace($installParent) -or [string]::IsNullOrWhiteSpace($installLeaf)) {
    throw 'ControlStudio install location is invalid.'
}
$dataRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA $installName))
if (Test-PathOverlap -First $installRoot -Second $dataRoot) {
    throw 'ControlStudio install location must not overlap the user data directory.'
}
Assert-ControlStudioUninstallRegistrationCompatible -Root $installRoot -Channel ([string]$bom.release_channel)
$existingKeyName = Get-ControlStudioUninstallKeyName -Channel ([string]$bom.release_channel)
$existingRegistration = if ($existingRegistrations.ContainsKey($existingKeyName)) { $existingRegistrations[$existingKeyName] } else { @{} }
New-Item -ItemType Directory -Path $installParent -Force | Out-Null
$stagingRoot = Join-Path $installParent ($installLeaf + '.staging-' + [guid]::NewGuid().ToString('N'))
$previousRoot = Join-Path $installParent ($installLeaf + '.previous')
$stagingRuntime = Join-Path $stagingRoot 'runtime'

try {
    New-Item -ItemType Directory -Path $stagingRuntime -Force | Out-Null
    Copy-Item -LiteralPath $verified.platform -Destination (Join-Path $stagingRoot $executableName)
    Copy-Item -LiteralPath $verified.agent -Destination (Join-Path $stagingRuntime 'controlstudio-agent.exe')
    Copy-Item -LiteralPath $verified.'plc-mcp' -Destination (Join-Path $stagingRuntime 'controlstudio-plc-mcp.exe')
    Copy-Item -LiteralPath $verified.'xg5000-cli' -Destination (Join-Path $stagingRuntime 'controlstudio-xg5000-cli.exe')
    Copy-Item -LiteralPath $contractPath -Destination (Join-Path $stagingRuntime $contract.artifact)
    Copy-Item -LiteralPath $bomPath -Destination (Join-Path $stagingRuntime 'controlstudio.bom.json')
    Copy-Item -LiteralPath (Join-Path $source 'Uninstall-ControlStudio.ps1') -Destination (Join-Path $stagingRoot 'Uninstall-ControlStudio.ps1')
    Copy-Item -LiteralPath (Join-Path $source 'Uninstall-ControlStudio.Core.psm1') -Destination (Join-Path $stagingRoot 'Uninstall-ControlStudio.Core.psm1')
    $null = Assert-InstalledLayout -Root $stagingRoot -ExpectedBom $bom

    if (Test-Path -LiteralPath $installRoot) {
        $installedBom = Assert-InstalledLayout -Root $installRoot -ExpectedBom $bom -AllowPreviousLayout
        Assert-ControlStudioStopped -Root $installRoot
        if ($installedBom.release_channel -ne $bom.release_channel) {
            throw 'ControlStudio updates cannot change release channels in place.'
        }
        Invoke-ControlStudioInstalledUpdate -Root $installRoot -Staging $stagingRoot -Parent $installParent -HistoricalRoot $previousRoot -ExpectedBom $bom -RegistryValues $existingRegistration
    } else {
        $freshInventory = @(Get-ControlStudioLayoutInventory -Root $stagingRoot)
        $freshMoved = $false
        try {
            Move-ControlStudioInstallDirectory -SourceRoot $stagingRoot -DestinationRoot $installRoot -Parent $installParent
            $freshMoved = $true
            Assert-ControlStudioLayoutInventory -Root $installRoot -Expected $freshInventory
            $null = Assert-InstalledLayout -Root $installRoot -ExpectedBom $bom
            Set-ControlStudioUninstallRegistration -Root $installRoot -ExpectedBom $bom -ExecutableName $executableName
        } catch {
            $freshError = $_
            if ($freshMoved) {
                try {
                    Assert-ControlStudioLayoutInventory -Root $installRoot -Expected $freshInventory
                    $failedRoot = Join-Path $installParent ($installLeaf + '.failed-' + [guid]::NewGuid().ToString('N'))
                    Move-ControlStudioInstallDirectory -SourceRoot $installRoot -DestinationRoot $failedRoot -Parent $installParent
                } catch {
                    Write-Warning 'ControlStudio fresh install failed; the current directory was preserved because its ownership could not be confirmed.'
                }
            }
            throw $freshError
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
