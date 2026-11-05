$ErrorActionPreference = 'Stop'

$script:UninstallRegistryRoot = 'Software\Microsoft\Windows\CurrentVersion\Uninstall'
$script:ContractNames = @('api_contract', 'event_contract', 'agent_contract', 'mcp_product_contract', 'workspace_contract', 'audit_contract')
$script:ComponentIds = @('platform', 'agent', 'plc-mcp', 'xg5000-cli')

function Get-ControlStudioUninstallHash {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    $stream = $null
    $algorithm = $null
    try {
        $stream = [IO.File]::OpenRead($LiteralPath)
        $algorithm = [Security.Cryptography.SHA256]::Create()
        return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    } finally {
        if ($null -ne $algorithm) { $algorithm.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function ConvertTo-ControlStudioFullPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $full = [IO.Path]::GetFullPath($Path)
    $volume = [IO.Path]::GetPathRoot($full)
    if ($full.Length -gt $volume.Length) { $full = $full.TrimEnd('\', '/') }
    return $full
}

function Test-ControlStudioPathOverlap {
    param([Parameter(Mandatory = $true)][string]$First, [Parameter(Mandatory = $true)][string]$Second)

    $firstFull = ConvertTo-ControlStudioFullPath -Path $First
    $secondFull = ConvertTo-ControlStudioFullPath -Path $Second
    return (
        $firstFull.Equals($secondFull, [StringComparison]::OrdinalIgnoreCase) -or
        $firstFull.StartsWith($secondFull + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $secondFull.StartsWith($firstFull + '\', [StringComparison]::OrdinalIgnoreCase)
    )
}

function Assert-ControlStudioOwnedFile {
    param([Parameter(Mandatory = $true)][string]$InstallRoot, [Parameter(Mandatory = $true)][string]$RelativePath)

    if ([IO.Path]::IsPathRooted($RelativePath) -or $RelativePath.Contains('..') -or $RelativePath -match '[/]' ) {
        throw "ControlStudio uninstall manifest path is unsafe: $RelativePath"
    }
    $root = ConvertTo-ControlStudioFullPath -Path $InstallRoot
    $path = ConvertTo-ControlStudioFullPath -Path (Join-Path $root $RelativePath)
    if (-not $path.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "ControlStudio uninstall path escaped its install root: $RelativePath"
    }
    return $path
}

function Get-ControlStudioRegistryEntries {
    $entries = @{}
    foreach ($keyName in @('ControlStudio', 'ControlStudioBeta')) {
        $subKeyPath = $script:UninstallRegistryRoot + '\' + $keyName
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($subKeyPath, $false)
        if ($null -eq $key) { continue }
        try {
            $values = @{}
            foreach ($name in $key.GetValueNames()) {
                $values[$name] = $key.GetValue($name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
            }
            $entries[$keyName] = $values
        } finally {
            $key.Dispose()
        }
    }
    return $entries
}

function Set-ControlStudioRegistryValues {
    param([Parameter(Mandatory = $true)][string]$KeyName, [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Values)

    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey(($script:UninstallRegistryRoot + '\' + $KeyName), $true)
    if ($null -eq $key) { throw 'ControlStudio uninstall registration disappeared during removal.' }
    try {
        foreach ($name in $Values.Keys) {
            $key.SetValue([string]$name, [string]$Values[$name], [Microsoft.Win32.RegistryValueKind]::String)
        }
    } finally {
        $key.Dispose()
    }
}

function Remove-ControlStudioRegistryEntry {
    param([Parameter(Mandatory = $true)][string]$KeyName)

    [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKey(($script:UninstallRegistryRoot + '\' + $KeyName), $false)
}

function Get-ControlStudioFallbackProcessPath {
    param([Parameter(Mandatory = $true)]$Process)

    # Bind this enumerated Process lifetime before opening a fresh limited handle.
    $created = $Process.StartTime.ToUniversalTime().ToFileTimeUtc()
    if (-not ('ControlStudio.UninstallProcessImage' -as [type])) {
        Add-Type -ErrorAction Stop -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
namespace ControlStudio {
    public static class UninstallProcessImage {
        [StructLayout(LayoutKind.Sequential)]
        private struct FileTime { public uint Low; public uint High; }
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetProcessTimes(IntPtr process, out FileTime creation, out FileTime exit, out FileTime kernel, out FileTime user);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder path, ref uint size);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CloseHandle(IntPtr handle);

        private static long Creation(FileTime value) {
            return (long)(((ulong)value.High << 32) | value.Low);
        }
        public static string TryGetExecutablePath(int pid, long expectedCreation) {
            if (pid <= 0 || expectedCreation <= 0) return null;
            // QUERY_LIMITED_INFORMATION | SYNCHRONIZE; never request control rights.
            IntPtr handle = OpenProcess(0x00101000, false, pid);
            if (handle == IntPtr.Zero) return null;
            try {
                FileTime creation, exit, kernel, user;
                if (!GetProcessTimes(handle, out creation, out exit, out kernel, out user)
                    || Creation(creation) != expectedCreation
                    || WaitForSingleObject(handle, 0) != 258) return null;
                StringBuilder path = new StringBuilder(32768);
                uint size = (uint)path.Capacity;
                if (!QueryFullProcessImageName(handle, 0, path, ref size)
                    || size == 0 || size >= path.Capacity || path.Length != size) return null;
                // Both checks use the same still-open process handle, including after query.
                if (!GetProcessTimes(handle, out creation, out exit, out kernel, out user)
                    || Creation(creation) != expectedCreation
                    || WaitForSingleObject(handle, 0) != 258) return null;
                return path.ToString();
            } finally {
                CloseHandle(handle);
            }
        }
    }
}
'@
    }
    return [ControlStudio.UninstallProcessImage]::TryGetExecutablePath([int]$Process.Id, [long]$created)
}

function Get-ControlStudioProcessSnapshot {
    $rows = @()
    foreach ($process in Get-Process -ErrorAction SilentlyContinue) {
        $processName = [string]$process.ProcessName
        $processPath = $null
        try { $processPath = [string]$process.Path } catch { }
        if ([string]::IsNullOrWhiteSpace($processPath)) {
            try { $processPath = Get-ControlStudioFallbackProcessPath -Process $process } catch { $processPath = $null }
        }
        try {
            if ([string]::IsNullOrWhiteSpace($processPath) -or -not [IO.Path]::IsPathRooted($processPath)) {
                throw 'Process executable path remains unavailable.'
            }
            $rows += [pscustomobject]@{ Path = [IO.Path]::GetFullPath($processPath); ProcessName = $processName; PathUnavailable = $false }
        } catch {
            $rows += [pscustomobject]@{ Path = $null; ProcessName = $processName; PathUnavailable = $true }
        }
    }
    return $rows
}

function Get-ControlStudioShortcutTarget {
    param([Parameter(Mandatory = $true)][string]$Path)

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    return [string]$shortcut.TargetPath
}

function Get-ControlStudioExpectedRegistryKey {
    param([Parameter(Mandatory = $true)][string]$Channel)

    switch ($Channel) {
        'production' { return 'ControlStudio' }
        'beta' { return 'ControlStudioBeta' }
        default { throw 'ControlStudio uninstall channel is invalid.' }
    }
}

function Get-ControlStudioAppNames {
    param([Parameter(Mandatory = $true)][string]$Channel)

    if ($Channel -eq 'production') {
        return @{ Install = 'ControlStudio'; Executable = 'ControlStudio.exe'; Display = 'ControlStudio' }
    }
    if ($Channel -eq 'beta') {
        return @{ Install = 'ControlStudio Beta'; Executable = 'ControlStudio Beta.exe'; Display = 'ControlStudio Beta' }
    }
    throw 'ControlStudio uninstall channel is invalid.'
}

function Assert-ControlStudioRoot {
    param([Parameter(Mandatory = $true)][string]$InstallRoot, [Parameter(Mandatory = $true)][System.Collections.IDictionary]$RegistryValues)

    $root = ConvertTo-ControlStudioFullPath -Path $InstallRoot
    $registeredRoot = ConvertTo-ControlStudioFullPath -Path ([string]$RegistryValues.InstallLocation)
    if (-not $root.Equals($registeredRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'ControlStudio uninstall location does not match the installed-app registration.'
    }
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { throw 'ControlStudio install root is missing.' }
    $rootItem = Get-Item -LiteralPath $root -Force
    if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'ControlStudio install root is a reparse point.' }
    $parent = Split-Path -Parent $root
    $leaf = Split-Path -Leaf $root
    if ([string]::IsNullOrWhiteSpace($parent) -or [string]::IsNullOrWhiteSpace($leaf)) { throw 'ControlStudio install root is invalid.' }
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { throw 'ControlStudio user data root is unavailable.' }
    $dataRoot = ConvertTo-ControlStudioFullPath -Path (Join-Path $env:LOCALAPPDATA (Get-ControlStudioAppNames -Channel ([string]$RegistryValues.ControlStudioChannel)).Install)
    if (Test-ControlStudioPathOverlap -First $root -Second $dataRoot) { throw 'ControlStudio install root overlaps user data.' }
    return $root
}

function Get-ControlStudioExpectedFiles {
    param([Parameter(Mandatory = $true)][string]$InstallRoot, [Parameter(Mandatory = $true)]$Bom, [Parameter(Mandatory = $true)][string]$Channel)

    if ($Bom.bom_schema -ne 'controlstudio-bom/v1' -or $Bom.release_channel -ne $Channel) { throw 'ControlStudio installed BOM schema or channel mismatch.' }
    if (@($Bom.components).Count -ne $script:ComponentIds.Count) { throw 'ControlStudio installed BOM component set is incomplete.' }
    foreach ($name in $script:ContractNames) {
        if ([string]::IsNullOrWhiteSpace([string]$Bom.contracts.$name)) { throw "ControlStudio BOM contract is missing: $name" }
    }
    $app = Get-ControlStudioAppNames -Channel $Channel
    $componentLocations = @{
        platform = $app.Executable
        agent = 'runtime\controlstudio-agent.exe'
        'plc-mcp' = 'runtime\controlstudio-plc-mcp.exe'
        'xg5000-cli' = 'runtime\controlstudio-xg5000-cli.exe'
    }
    $files = @()
    for ($index = 0; $index -lt $script:ComponentIds.Count; $index++) {
        $component = $Bom.components[$index]
        $expectedId = $script:ComponentIds[$index]
        if ($component.id -ne $expectedId) { throw 'ControlStudio installed BOM component order or identity is invalid.' }
        if ([IO.Path]::GetFileName([string]$component.artifact) -ne [string]$component.artifact) { throw 'ControlStudio installed BOM artifact name is unsafe.' }
        if ([string]$component.sha256 -notmatch '^[0-9a-fA-F]{64}$') { throw "ControlStudio BOM component hash is invalid: $expectedId" }
        foreach ($name in $script:ContractNames) {
            if ($component.contracts.$name -ne $Bom.contracts.$name) { throw "ControlStudio component contract mismatch: $expectedId/$name" }
        }
        $files += [pscustomobject]@{ path = [string]$componentLocations[$expectedId]; sha256 = ([string]$component.sha256).ToLowerInvariant(); component = $expectedId }
    }
    $contract = $Bom.contract_artifact
    if ($null -eq $contract -or [IO.Path]::GetFileName([string]$contract.artifact) -ne [string]$contract.artifact) { throw 'ControlStudio public contracts artifact path is invalid.' }
    if ([string]$contract.artifact -notmatch '^controlstudio_contracts-[A-Za-z0-9._-]+\.whl$') { throw 'ControlStudio public contracts artifact name is invalid.' }
    if ([string]$contract.sha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'ControlStudio public contracts artifact hash is invalid.' }
    $files += [pscustomobject]@{ path = ('runtime\' + [string]$contract.artifact); sha256 = ([string]$contract.sha256).ToLowerInvariant(); component = 'contracts' }
    $files += [pscustomobject]@{ path = 'runtime\controlstudio.bom.json'; sha256 = $null; component = 'bom' }
    return $files
}

function Get-ControlStudioVerifiedHistoricalLayout {
    param([Parameter(Mandatory = $true)][string]$Root, [Parameter(Mandatory = $true)][string]$Channel, [Parameter(Mandatory = $true)][System.Collections.IDictionary]$RegistryValues)

    $root = ConvertTo-ControlStudioFullPath -Path $Root
    if (-not (Test-Path -LiteralPath $root)) { return [pscustomobject]@{ root = $root; files = @(); present = $false } }
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { throw 'ControlStudio previous-version path is not a directory.' }
    if (((Get-Item -LiteralPath $root -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'ControlStudio previous-version directory is a reparse point.' }
    $dataRoot = ConvertTo-ControlStudioFullPath -Path (Join-Path $env:LOCALAPPDATA (Get-ControlStudioAppNames -Channel $Channel).Install)
    if (Test-ControlStudioPathOverlap -First $root -Second $dataRoot) { throw 'ControlStudio previous-version path overlaps user data.' }

    $bomPath = Join-Path $root 'runtime\controlstudio.bom.json'
    if (-not (Test-Path -LiteralPath $bomPath -PathType Leaf)) { throw 'ControlStudio previous-version BOM is missing; uninstall was stopped safely.' }
    $bom = Get-Content -LiteralPath $bomPath -Raw | ConvertFrom-Json
    if ($bom.bom_schema -ne 'controlstudio-bom/v1' -or $bom.release_channel -ne $Channel) { throw 'ControlStudio previous-version BOM channel or schema is invalid; uninstall was stopped safely.' }
    $ids = @('platform', 'agent', 'plc-mcp')
    if (@($bom.components).Count -eq 4) { $ids += 'xg5000-cli' }
    if (@($bom.components).Count -ne $ids.Count) { throw 'ControlStudio previous-version component set is unrecognized; uninstall was stopped safely.' }
    foreach ($name in $script:ContractNames) {
        if ([string]::IsNullOrWhiteSpace([string]$bom.contracts.$name)) { throw "ControlStudio previous-version contract is missing: $name" }
    }
    $app = Get-ControlStudioAppNames -Channel $Channel
    $componentLocations = @{
        platform = $app.Executable
        agent = 'runtime\controlstudio-agent.exe'
        'plc-mcp' = 'runtime\controlstudio-plc-mcp.exe'
        'xg5000-cli' = 'runtime\controlstudio-xg5000-cli.exe'
    }
    $files = @()
    for ($index = 0; $index -lt $ids.Count; $index++) {
        $component = $bom.components[$index]
        $expectedId = $ids[$index]
        if ($component.id -ne $expectedId -or [IO.Path]::GetFileName([string]$component.artifact) -ne [string]$component.artifact) {
            throw 'ControlStudio previous-version BOM component identity is invalid; uninstall was stopped safely.'
        }
        if ([string]$component.sha256 -notmatch '^[0-9a-fA-F]{64}$') { throw "ControlStudio previous-version component hash is invalid: $expectedId" }
        foreach ($name in $script:ContractNames) {
            if ($component.contracts.$name -ne $bom.contracts.$name) { throw "ControlStudio previous-version component contract mismatch: $expectedId/$name" }
        }
        $files += [pscustomobject]@{ path = [string]$componentLocations[$expectedId]; sha256 = ([string]$component.sha256).ToLowerInvariant(); component = $expectedId }
    }
    $contract = $bom.contract_artifact
    if ($null -eq $contract -or [IO.Path]::GetFileName([string]$contract.artifact) -ne [string]$contract.artifact -or [string]$contract.artifact -notmatch '^controlstudio_contracts-[A-Za-z0-9._-]+\.whl$' -or [string]$contract.sha256 -notmatch '^[0-9a-fA-F]{64}$') {
        throw 'ControlStudio previous-version contracts artifact is invalid; uninstall was stopped safely.'
    }
    $files += [pscustomobject]@{ path = ('runtime\' + [string]$contract.artifact); sha256 = ([string]$contract.sha256).ToLowerInvariant(); component = 'contracts' }
    $bomHash = Get-ControlStudioUninstallHash -LiteralPath $bomPath
    $files += [pscustomobject]@{ path = 'runtime\controlstudio.bom.json'; sha256 = $bomHash; component = 'bom' }

    foreach ($name in @('Uninstall-ControlStudio.ps1', 'Uninstall-ControlStudio.Core.psm1')) {
        $path = Join-Path $root $name
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $expectedHash = if ($name -eq 'Uninstall-ControlStudio.ps1') { [string]$RegistryValues.ControlStudioUninstallerSha256 } else { [string]$RegistryValues.ControlStudioUninstallerCoreSha256 }
            $actualHash = Get-ControlStudioUninstallHash -LiteralPath $path
            if ($actualHash -eq $expectedHash) { $files += [pscustomobject]@{ path = $name; sha256 = $actualHash; component = 'uninstaller' } }
        }
    }
    Assert-ControlStudioFileState -InstallRoot $root -Files $files
    if ($Channel -eq 'production') {
        foreach ($component in $bom.components) {
            if ($component.signature.kind -ne 'authenticode' -or $component.signature.status -ne 'verified') { throw "ControlStudio previous production signature metadata is invalid: $($component.id)" }
            $installedPath = Join-Path $root ([string]$componentLocations[$component.id])
            $signature = Get-AuthenticodeSignature -LiteralPath $installedPath
            if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $component.signature.thumbprint) { throw "ControlStudio previous production signature verification failed: $($component.id)" }
        }
    }
    return [pscustomobject]@{ root = $root; files = $files; present = $true; ownership = 'verified' }
}

function Get-ControlStudioRetainedLayout {
    param([Parameter(Mandatory = $true)][string]$Root, [Parameter(Mandatory = $true)][string]$Channel, [Parameter(Mandatory = $true)][System.Collections.IDictionary]$RegistryValues)

    $rootFull = ConvertTo-ControlStudioFullPath -Path $Root
    if (-not (Test-Path -LiteralPath $rootFull)) { return [pscustomobject]@{ root = $rootFull; files = @(); present = $false; ownership = 'absent' } }
    # Unsafe paths are not a historical-format exception.
    foreach ($path in @($rootFull, (Join-Path $rootFull 'runtime'))) {
        if (Test-Path -LiteralPath $path) {
            $item = Get-Item -LiteralPath $path -Force
            if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'ControlStudio historical layout directory is unsafe.' }
        }
    }
    try { return Get-ControlStudioVerifiedHistoricalLayout -Root $rootFull -Channel $Channel -RegistryValues $RegistryValues }
    catch {
        # Unverified history grants no deletion authority and remains in place.
        return [pscustomobject]@{ root = $rootFull; files = @(); present = $true; ownership = 'unverified_preserved' }
    }
}

function Get-ControlStudioPreviousLayout {
    param([Parameter(Mandatory = $true)][string]$InstallRoot, [Parameter(Mandatory = $true)][string]$Channel, [Parameter(Mandatory = $true)][System.Collections.IDictionary]$RegistryValues)

    return Get-ControlStudioRetainedLayout -Root ($InstallRoot + '.previous') -Channel $Channel -RegistryValues $RegistryValues
}

function Get-ControlStudioLayoutInventory {
    param([Parameter(Mandatory = $true)][string]$Root)

    $rootFull = ConvertTo-ControlStudioFullPath -Path $Root
    $ancestor = $rootFull
    while (-not [string]::IsNullOrWhiteSpace($ancestor)) {
        if (-not (Test-Path -LiteralPath $ancestor -PathType Container)) { throw 'ControlStudio layout ancestor is missing.' }
        if (((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'ControlStudio layout ancestor is a reparse point.' }
        $ancestor = Split-Path -Parent $ancestor
    }
    $pending = New-Object 'Collections.Generic.Queue[string]'
    $pending.Enqueue($rootFull)
    $entries = @([pscustomobject]@{ path = '.'; kind = 'directory'; sha256 = ''; bytes = 0; attributes = [int](Get-Item -LiteralPath $rootFull -Force).Attributes })
    while ($pending.Count -gt 0) {
        $directory = $pending.Dequeue()
        foreach ($item in Get-ChildItem -LiteralPath $directory -Force -ErrorAction Stop) {
            $full = ConvertTo-ControlStudioFullPath -Path $item.FullName
            if (-not $full.StartsWith($rootFull + '\', [StringComparison]::OrdinalIgnoreCase) -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'ControlStudio layout inventory path is unsafe.' }
            $relative = $full.Substring($rootFull.Length + 1)
            if ($item.PSIsContainer) {
                $entries += [pscustomobject]@{ path = $relative; kind = 'directory'; sha256 = ''; bytes = 0; attributes = [int]$item.Attributes }
                $pending.Enqueue($full)
            } else {
                $hash = Get-ControlStudioUninstallHash -LiteralPath $full
                $after = Get-Item -LiteralPath $full -Force
                if ($after.Length -ne $item.Length -or $after.LastWriteTimeUtc.Ticks -ne $item.LastWriteTimeUtc.Ticks -or $after.Attributes -ne $item.Attributes) { throw 'ControlStudio layout file changed during inventory.' }
                $entries += [pscustomobject]@{ path = $relative; kind = 'file'; sha256 = $hash; bytes = [long]$item.Length; attributes = [int]$item.Attributes }
            }
        }
    }
    return @($entries | Sort-Object -Property path)
}

function Assert-ControlStudioLayoutInventory {
    param([Parameter(Mandatory = $true)][string]$Root, [Parameter(Mandatory = $true)][AllowEmptyCollection()][array]$Expected)

    $actual = @(Get-ControlStudioLayoutInventory -Root $Root)
    if ((ConvertTo-Json -InputObject $actual -Depth 4 -Compress) -cne (ConvertTo-Json -InputObject @($Expected) -Depth 4 -Compress)) { throw 'ControlStudio transaction inventory changed; files were preserved.' }
}

function Move-ControlStudioInstallDirectory {
    param([Parameter(Mandatory = $true)][string]$SourceRoot, [Parameter(Mandatory = $true)][string]$DestinationRoot, [Parameter(Mandatory = $true)][string]$Parent)

    $parentFull = ConvertTo-ControlStudioFullPath -Path $Parent
    $sourceFull = ConvertTo-ControlStudioFullPath -Path $SourceRoot
    $destinationFull = ConvertTo-ControlStudioFullPath -Path $DestinationRoot
    if (-not (Split-Path -Parent $sourceFull).Equals($parentFull, [StringComparison]::OrdinalIgnoreCase) -or
        -not (Split-Path -Parent $destinationFull).Equals($parentFull, [StringComparison]::OrdinalIgnoreCase) -or
        $sourceFull.Equals($destinationFull, [StringComparison]::OrdinalIgnoreCase)) { throw 'ControlStudio transaction path escaped the install parent.' }
    if (Test-Path -LiteralPath $destinationFull) { throw 'ControlStudio transaction destination already exists.' }
    $ancestor = $sourceFull
    while (-not [string]::IsNullOrWhiteSpace($ancestor)) {
        $item = Get-Item -LiteralPath $ancestor -Force
        if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'ControlStudio transaction directory is unsafe.' }
        $ancestor = Split-Path -Parent $ancestor
    }
    # Same-parent rename refuses an existing destination, including a racing creator.
    [IO.Directory]::Move($sourceFull, $destinationFull)
}

function Protect-ControlStudioResidualLayout {
    param([Parameter(Mandatory = $true)][string]$Root)

    if (-not (Test-Path -LiteralPath $Root)) { return $null }
    $rootFull = ConvertTo-ControlStudioFullPath -Path $Root
    $parent = Split-Path -Parent $rootFull
    $leaf = Split-Path -Leaf $rootFull
    if ([string]::IsNullOrWhiteSpace($parent) -or [string]::IsNullOrWhiteSpace($leaf)) { throw 'ControlStudio residue root is unsafe.' }
    $inventory = @(Get-ControlStudioLayoutInventory -Root $rootFull)
    $preserved = Join-Path $parent ($leaf + '.preserved-' + [guid]::NewGuid().ToString('N'))
    $moved = $false
    try {
        Assert-ControlStudioLayoutInventory -Root $rootFull -Expected $inventory
        Move-ControlStudioInstallDirectory -SourceRoot $rootFull -DestinationRoot $preserved -Parent $parent
        $moved = $true
        Assert-ControlStudioLayoutInventory -Root $preserved -Expected $inventory
    } catch {
        $preservationError = $_
        if ($moved) {
            Assert-ControlStudioLayoutInventory -Root $preserved -Expected $inventory
            Move-ControlStudioInstallDirectory -SourceRoot $preserved -DestinationRoot $rootFull -Parent $parent
            Assert-ControlStudioLayoutInventory -Root $rootFull -Expected $inventory
        }
        throw $preservationError
    }
    return $preserved
}

function Remove-ControlStudioVerifiedLayoutPayload {
    param([Parameter(Mandatory = $true)][string]$Root, [Parameter(Mandatory = $true)][AllowEmptyCollection()][array]$Files, [scriptblock]$RemoveFile)

    Assert-ControlStudioFileState -InstallRoot $Root -Files $Files -AllowMissing
    foreach ($file in $Files) {
        $path = Assert-ControlStudioOwnedFile -InstallRoot $Root -RelativePath ([string]$file.path)
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        if (-not $file.sha256 -or (Get-ControlStudioUninstallHash -LiteralPath $path) -ne [string]$file.sha256) { throw 'ControlStudio owned payload changed before cleanup; files were preserved.' }
        if ($null -ne $RemoveFile) { & $RemoveFile $path } else { Remove-Item -LiteralPath $path -Force }
        if (Test-Path -LiteralPath $path) { throw 'ControlStudio owned payload removal did not complete.' }
    }
}

function Assert-ControlStudioFileState {
    param([Parameter(Mandatory = $true)][string]$InstallRoot, [Parameter(Mandatory = $true)][AllowEmptyCollection()][array]$Files, [switch]$AllowMissing)

    if (Test-Path -LiteralPath $InstallRoot -PathType Container) {
        if (((Get-Item -LiteralPath $InstallRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'ControlStudio payload root is a reparse point.' }
    } elseif (-not $AllowMissing) {
        throw "ControlStudio payload root is missing: $InstallRoot"
    }
    $runtime = Join-Path $InstallRoot 'runtime'
    if (Test-Path -LiteralPath $runtime -PathType Container) {
        if (((Get-Item -LiteralPath $runtime -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'ControlStudio runtime directory is a reparse point.' }
    }
    foreach ($file in $Files) {
        $path = Assert-ControlStudioOwnedFile -InstallRoot $InstallRoot -RelativePath ([string]$file.path)
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            if ($AllowMissing) { continue }
            throw "ControlStudio installed file is missing: $($file.path)"
        }
        $item = Get-Item -LiteralPath $path -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "ControlStudio installed file is a reparse point: $($file.path)" }
        if ($file.sha256 -and (Get-ControlStudioUninstallHash -LiteralPath $path) -ne [string]$file.sha256) {
            throw "ControlStudio installed file hash mismatch: $($file.path)"
        }
    }
}

function Get-ControlStudioUnrecognizedEntries {
    param([Parameter(Mandatory = $true)][string]$InstallRoot, [Parameter(Mandatory = $true)][AllowEmptyCollection()][array]$Files)

    $unknown = @()
    if (-not (Test-Path -LiteralPath $InstallRoot -PathType Container)) { return $unknown }
    $allowedRoot = @('runtime')
    $allowedRuntime = @()
    foreach ($file in $Files) {
        $relative = [string]$file.path
        if ($relative.StartsWith('runtime\', [StringComparison]::OrdinalIgnoreCase)) {
            $allowedRuntime += $relative.Substring('runtime\'.Length)
        } else {
            $allowedRoot += $relative
        }
    }
    foreach ($item in Get-ChildItem -LiteralPath $InstallRoot -Force) {
        if ($allowedRoot -notcontains $item.Name) { $unknown += $item.FullName }
    }
    $runtime = Join-Path $InstallRoot 'runtime'
    if (Test-Path -LiteralPath $runtime -PathType Container) {
        foreach ($item in Get-ChildItem -LiteralPath $runtime -Force) {
            if ($allowedRuntime -notcontains $item.Name) { $unknown += $item.FullName }
        }
    }
    return $unknown
}

function Get-ControlStudioPendingManifest {
    param([Parameter(Mandatory = $true)][string]$InstallRoot, [Parameter(Mandatory = $true)][System.Collections.IDictionary]$RegistryValues, [Parameter(Mandatory = $true)][string]$Channel)

    $json = [string]$RegistryValues.ControlStudioUninstallPending
    if ([string]::IsNullOrWhiteSpace($json)) { return $null }
    try { $manifest = $json | ConvertFrom-Json } catch { throw 'ControlStudio uninstall continuation record is invalid.' }
    $root = ConvertTo-ControlStudioFullPath -Path ([string]$manifest.install_root)
    if (-not $root.Equals((ConvertTo-ControlStudioFullPath -Path $InstallRoot), [StringComparison]::OrdinalIgnoreCase)) { throw 'ControlStudio uninstall continuation root mismatch.' }
    if ($manifest.schema -ne 'controlstudio-uninstall/v1' -or $manifest.release_channel -ne $Channel) { throw 'ControlStudio uninstall continuation schema or channel mismatch.' }
    if ([string]$manifest.bom_sha256 -notmatch '^[0-9a-f]{64}$' -or [string]$manifest.bom_sha256 -ne [string]$RegistryValues.ControlStudioBomSha256) { throw 'ControlStudio uninstall continuation BOM binding mismatch.' }
    if (@($manifest.files).Count -ne 8) { throw 'ControlStudio uninstall continuation file set is incomplete.' }
    $allowed = @{}
    foreach ($file in $manifest.files) {
        $relative = [string]$file.path
        if ([string]$file.sha256 -notmatch '^[0-9a-f]{64}$') { throw 'ControlStudio uninstall continuation file hash is invalid.' }
        if ($relative -notmatch '^(ControlStudio( Beta)?\.exe|runtime\\controlstudio-(agent|plc-mcp|xg5000-cli)\.exe|runtime\\controlstudio\.bom\.json|runtime\\controlstudio_contracts-[A-Za-z0-9._-]+\.whl|Uninstall-ControlStudio\.ps1|Uninstall-ControlStudio\.Core\.psm1)$') { throw 'ControlStudio uninstall continuation contains an unexpected path.' }
        if ($allowed.ContainsKey($relative)) { throw 'ControlStudio uninstall continuation contains a duplicate path.' }
        $allowed[$relative] = $file
    }
    foreach ($required in @('runtime\controlstudio-agent.exe', 'runtime\controlstudio-plc-mcp.exe', 'runtime\controlstudio-xg5000-cli.exe', 'runtime\controlstudio.bom.json', 'Uninstall-ControlStudio.ps1', 'Uninstall-ControlStudio.Core.psm1')) {
        if (-not $allowed.ContainsKey($required)) { throw "ControlStudio uninstall continuation is missing: $required" }
    }
    if ([string]$allowed['runtime\controlstudio.bom.json'].sha256 -ne [string]$manifest.bom_sha256) { throw 'ControlStudio uninstall continuation BOM hash is inconsistent.' }
    if ([string]$allowed['Uninstall-ControlStudio.ps1'].sha256 -ne [string]$RegistryValues.ControlStudioUninstallerSha256) { throw 'ControlStudio uninstall continuation entrypoint hash is inconsistent.' }
    if ([string]$allowed['Uninstall-ControlStudio.Core.psm1'].sha256 -ne [string]$RegistryValues.ControlStudioUninstallerCoreSha256) { throw 'ControlStudio uninstall continuation core hash is inconsistent.' }
    $expectedApp = Get-ControlStudioAppNames -Channel $Channel
    if (-not $allowed.ContainsKey($expectedApp.Executable)) { throw 'ControlStudio uninstall continuation is missing its public executable.' }
    $otherExecutable = if ($Channel -eq 'beta') { 'ControlStudio.exe' } else { 'ControlStudio Beta.exe' }
    if ($allowed.ContainsKey($otherExecutable)) { throw 'ControlStudio uninstall continuation contains a cross-channel executable.' }
    $contractFiles = @($allowed.Keys | Where-Object { $_ -match '^runtime\\controlstudio_contracts-[A-Za-z0-9._-]+\.whl$' })
    if ($contractFiles.Count -ne 1) { throw 'ControlStudio uninstall continuation contracts file is invalid.' }
    $previousRoot = ConvertTo-ControlStudioFullPath -Path ($InstallRoot + '.previous')
    if (-not (ConvertTo-ControlStudioFullPath -Path ([string]$manifest.previous_root)).Equals($previousRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'ControlStudio uninstall continuation previous-version path mismatch.' }
    $previousFiles = @($manifest.previous_files)
    if ($previousFiles.Count -notin @(0, 5, 6, 7, 8)) { throw 'ControlStudio previous-version continuation file set is invalid.' }
    $previousAllowed = @{}
    foreach ($file in $previousFiles) {
        $relative = [string]$file.path
        if ([string]$file.sha256 -notmatch '^[0-9a-f]{64}$') { throw 'ControlStudio previous-version continuation hash is invalid.' }
        if ($relative -notmatch '^(ControlStudio( Beta)?\.exe|runtime\\controlstudio-(agent|plc-mcp|xg5000-cli)\.exe|runtime\\controlstudio\.bom\.json|runtime\\controlstudio_contracts-[A-Za-z0-9._-]+\.whl|Uninstall-ControlStudio\.ps1|Uninstall-ControlStudio\.Core\.psm1)$') { throw 'ControlStudio previous-version continuation contains an unexpected path.' }
        if ($previousAllowed.ContainsKey($relative)) { throw 'ControlStudio previous-version continuation contains a duplicate path.' }
        $previousAllowed[$relative] = $file
    }
    if ($previousFiles.Count -gt 0) {
        if (-not $previousAllowed.ContainsKey('runtime\controlstudio-agent.exe') -or -not $previousAllowed.ContainsKey('runtime\controlstudio-plc-mcp.exe') -or -not $previousAllowed.ContainsKey('runtime\controlstudio.bom.json')) { throw 'ControlStudio previous-version continuation is incomplete.' }
        if (-not $previousAllowed.ContainsKey($expectedApp.Executable)) { throw 'ControlStudio previous-version continuation is missing its public executable.' }
        $previousContracts = @($previousAllowed.Keys | Where-Object { $_ -match '^runtime\\controlstudio_contracts-[A-Za-z0-9._-]+\.whl$' })
        if ($previousContracts.Count -ne 1) { throw 'ControlStudio previous-version continuation contracts file is invalid.' }
        $previousExecutable = if ($Channel -eq 'beta') { 'ControlStudio.exe' } else { 'ControlStudio Beta.exe' }
        if ($previousAllowed.ContainsKey($previousExecutable)) { throw 'ControlStudio previous-version continuation contains a cross-channel executable.' }
        $expectedCount = 5 + [int]$previousAllowed.ContainsKey('runtime\controlstudio-xg5000-cli.exe') + [int]$previousAllowed.ContainsKey('Uninstall-ControlStudio.ps1') + [int]$previousAllowed.ContainsKey('Uninstall-ControlStudio.Core.psm1')
        if ($previousFiles.Count -ne $expectedCount) { throw 'ControlStudio previous-version continuation file set is inconsistent.' }
        if ([string]$previousAllowed['Uninstall-ControlStudio.ps1'].sha256 -and [string]$previousAllowed['Uninstall-ControlStudio.ps1'].sha256 -ne [string]$RegistryValues.ControlStudioUninstallerSha256) { throw 'ControlStudio previous entrypoint binding is invalid.' }
        if ([string]$previousAllowed['Uninstall-ControlStudio.Core.psm1'].sha256 -and [string]$previousAllowed['Uninstall-ControlStudio.Core.psm1'].sha256 -ne [string]$RegistryValues.ControlStudioUninstallerCoreSha256) { throw 'ControlStudio previous core binding is invalid.' }
    }
    return $manifest
}

function Invoke-ControlStudioUninstall {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [System.Collections.IDictionary]$RegistryEntries,
        [scriptblock]$SaveRegistryValues,
        [scriptblock]$DeleteRegistryEntry,
        [scriptblock]$ProcessSnapshot,
        [scriptblock]$RemoveFile,
        [scriptblock]$ShortcutTarget
    )

    $root = ConvertTo-ControlStudioFullPath -Path $InstallRoot
    $entries = if ($null -ne $RegistryEntries) { $RegistryEntries } else { Get-ControlStudioRegistryEntries }
    $matches = @($entries.Keys | Where-Object {
        $values = $entries[$_]
        -not [string]::IsNullOrWhiteSpace([string]$values.InstallLocation) -and
        (ConvertTo-ControlStudioFullPath -Path ([string]$values.InstallLocation)).Equals($root, [StringComparison]::OrdinalIgnoreCase)
    })
    if ($matches.Count -ne 1) { throw 'ControlStudio install root does not identify exactly one installed-app registration.' }
    $keyName = [string]$matches[0]
    $registryValues = $entries[$keyName]
    $savePending = $false
    $channel = [string]$registryValues.ControlStudioChannel
    if ($keyName -ne (Get-ControlStudioExpectedRegistryKey -Channel $channel)) { throw 'ControlStudio registry key and release channel disagree.' }
    $root = Assert-ControlStudioRoot -InstallRoot $root -RegistryValues $registryValues
    $app = Get-ControlStudioAppNames -Channel $channel
    if ([string]$registryValues.DisplayName -ne $app.Display) { throw 'ControlStudio installed-app display name does not match its channel.' }
    $uninstallMatch = [regex]::Match([string]$registryValues.UninstallString, '^"(?<host>[^"]+)" -NoProfile -ExecutionPolicy Bypass -File "(?<script>[^"]+)"$')
    $expectedHost = ConvertTo-ControlStudioFullPath -Path (Join-Path $PSHOME 'powershell.exe')
    if (-not $uninstallMatch.Success -or -not (ConvertTo-ControlStudioFullPath -Path $uninstallMatch.Groups['host'].Value).Equals($expectedHost, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'ControlStudio uninstall command does not use the installed PowerShell host.'
    }
    $registeredScript = $uninstallMatch.Groups['script'].Value
    if ([IO.Path]::GetFileName($registeredScript) -ne 'Uninstall-ControlStudio.ps1' -or -not (Test-Path -LiteralPath $registeredScript -PathType Leaf)) { throw 'ControlStudio uninstall command does not name its installed entrypoint.' }
    if ((Get-ControlStudioUninstallHash -LiteralPath $registeredScript) -ne [string]$registryValues.ControlStudioUninstallerSha256) { throw 'ControlStudio registered uninstall entrypoint hash is invalid.' }

    $bomPath = Join-Path $root 'runtime\controlstudio.bom.json'
    $pending = Get-ControlStudioPendingManifest -InstallRoot $root -RegistryValues $registryValues -Channel $channel
    if ($null -eq $pending) {
        if (-not (Test-Path -LiteralPath $bomPath -PathType Leaf)) { throw 'ControlStudio installed BOM is missing.' }
        $bomHash = Get-ControlStudioUninstallHash -LiteralPath $bomPath
        if ($bomHash -ne [string]$registryValues.ControlStudioBomSha256) { throw 'ControlStudio installed BOM hash does not match registration.' }
        $bom = Get-Content -LiteralPath $bomPath -Raw | ConvertFrom-Json
        $expectedFiles = Get-ControlStudioExpectedFiles -InstallRoot $root -Bom $bom -Channel $channel
        foreach ($name in @('Uninstall-ControlStudio.ps1', 'Uninstall-ControlStudio.Core.psm1')) {
            $expectedHashName = if ($name -eq 'Uninstall-ControlStudio.ps1') { 'ControlStudioUninstallerSha256' } else { 'ControlStudioUninstallerCoreSha256' }
            $path = Join-Path $root $name
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "ControlStudio uninstaller payload is missing: $name" }
            $hash = Get-ControlStudioUninstallHash -LiteralPath $path
            if ($hash -ne [string]$registryValues[$expectedHashName]) { throw "ControlStudio uninstaller payload hash mismatch: $name" }
            $expectedFiles += [pscustomobject]@{ path = $name; sha256 = $hash; component = 'uninstaller' }
        }
        $bomEntry = @($expectedFiles | Where-Object { $_.path -eq 'runtime\controlstudio.bom.json' })[0]
        $bomEntry.sha256 = $bomHash
        Assert-ControlStudioFileState -InstallRoot $root -Files $expectedFiles
        if ($channel -eq 'production') {
            foreach ($component in $bom.components) {
                if ($component.signature.kind -ne 'authenticode' -or $component.signature.status -ne 'verified') { throw "Production component signature metadata is invalid: $($component.id)" }
                $installedPath = switch ($component.id) {
                    'platform' { Join-Path $root $app.Executable }
                    'agent' { Join-Path $root 'runtime\controlstudio-agent.exe' }
                    'plc-mcp' { Join-Path $root 'runtime\controlstudio-plc-mcp.exe' }
                    'xg5000-cli' { Join-Path $root 'runtime\controlstudio-xg5000-cli.exe' }
                }
                $signature = Get-AuthenticodeSignature -LiteralPath $installedPath
                if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $component.signature.thumbprint) { throw "Production component signature verification failed: $($component.id)" }
            }
        }
        $previous = Get-ControlStudioPreviousLayout -InstallRoot $root -Channel $channel -RegistryValues $registryValues
        $pending = [pscustomobject]@{
            schema = 'controlstudio-uninstall/v1'
            install_root = $root
            release_channel = $channel
            bom_sha256 = $bomHash
            files = @($expectedFiles | ForEach-Object { [pscustomobject]@{ path = [string]$_.path; sha256 = ([string]$_.sha256).ToLowerInvariant() } })
            previous_root = $previous.root
            previous_files = @($previous.files | ForEach-Object { [pscustomobject]@{ path = [string]$_.path; sha256 = ([string]$_.sha256).ToLowerInvariant() } })
        }
        $pendingJson = $pending | ConvertTo-Json -Depth 6 -Compress
        $savePending = $true
    } else {
        $expectedFiles = @($pending.files)
        $previous = [pscustomobject]@{ root = [string]$pending.previous_root; files = @($pending.previous_files); present = @($pending.previous_files).Count -gt 0 }
    }

    $pendingFiles = @($pending.files)
    Assert-ControlStudioFileState -InstallRoot $root -Files $pendingFiles -AllowMissing
    $previousFiles = @($pending.previous_files)
    Assert-ControlStudioFileState -InstallRoot $previous.root -Files $previousFiles -AllowMissing
    $ownedExecutablePaths = @($pendingFiles | Where-Object { $_.path -match '\.exe$' } | ForEach-Object { ConvertTo-ControlStudioFullPath -Path (Assert-ControlStudioOwnedFile -InstallRoot $root -RelativePath ([string]$_.path)) })
    $ownedExecutablePaths += @($previousFiles | Where-Object { $_.path -match '\.exe$' } | ForEach-Object { ConvertTo-ControlStudioFullPath -Path (Assert-ControlStudioOwnedFile -InstallRoot $previous.root -RelativePath ([string]$_.path)) })
    $processes = if ($null -ne $ProcessSnapshot) { @(& $ProcessSnapshot) } else { @(Get-ControlStudioProcessSnapshot) }
    foreach ($process in $processes) {
        $processPath = $null
        if (-not [string]::IsNullOrWhiteSpace([string]$process.Path)) {
            try { $processPath = ConvertTo-ControlStudioFullPath -Path ([string]$process.Path) } catch { $processPath = $null }
            if ($null -ne $processPath -and $ownedExecutablePaths -contains $processPath) { throw 'Close ControlStudio before uninstalling it.' }
        }
        if ($process.PathUnavailable -and [string]$process.ProcessName -in @('ControlStudio', 'ControlStudio Beta', 'controlstudio-agent', 'controlstudio-plc-mcp', 'controlstudio-xg5000-cli')) {
            throw 'ControlStudio process state could not be verified; close it before uninstalling.'
        }
    }
    if ($savePending) {
        if ($null -ne $SaveRegistryValues) { & $SaveRegistryValues $keyName @{ ControlStudioUninstallPending = $pendingJson } } else { Set-ControlStudioRegistryValues -KeyName $keyName -Values @{ ControlStudioUninstallPending = $pendingJson } }
    }

    $filesToRemove = @($pendingFiles | Where-Object { $_.path -notin @('Uninstall-ControlStudio.ps1', 'Uninstall-ControlStudio.Core.psm1') })
    Remove-ControlStudioVerifiedLayoutPayload -Root $root -Files $filesToRemove -RemoveFile $RemoveFile
    Remove-ControlStudioVerifiedLayoutPayload -Root $previous.root -Files $previousFiles -RemoveFile $RemoveFile

    $shortcutDirectory = $null
    if (-not [string]::IsNullOrWhiteSpace($env:APPDATA)) {
        $shortcutDirectory = Join-Path $env:APPDATA ("Microsoft\Windows\Start Menu\Programs\" + $app.Display)
    }
    $shortcutPath = if ($null -ne $shortcutDirectory) { Join-Path $shortcutDirectory ($app.Display + '.lnk') } else { $null }
    if ($null -ne $shortcutPath -and (Test-Path -LiteralPath $shortcutPath -PathType Leaf)) {
        $shortcutTargetPath = $null
        try {
            if ($null -ne $ShortcutTarget) { $shortcutTargetPath = [string](& $ShortcutTarget $shortcutPath) }
            else { $shortcutTargetPath = Get-ControlStudioShortcutTarget -Path $shortcutPath }
        } catch {
            $shortcutTargetPath = $null
        }
        if (-not [string]::IsNullOrWhiteSpace($shortcutTargetPath) -and (ConvertTo-ControlStudioFullPath -Path $shortcutTargetPath).Equals((ConvertTo-ControlStudioFullPath -Path (Join-Path $root $app.Executable)), [StringComparison]::OrdinalIgnoreCase)) {
            if ($null -ne $RemoveFile) { & $RemoveFile $shortcutPath } else { Remove-Item -LiteralPath $shortcutPath -Force }
            if (Test-Path -LiteralPath $shortcutPath) { throw 'ControlStudio Start Menu shortcut removal did not complete.' }
        }
    }

    if ($null -ne $DeleteRegistryEntry) { & $DeleteRegistryEntry $keyName } else { Remove-ControlStudioRegistryEntry -KeyName $keyName }
    $helperResidues = @()
    foreach ($name in @('Uninstall-ControlStudio.Core.psm1', 'Uninstall-ControlStudio.ps1')) {
        $path = Join-Path $root $name
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            try {
                if ($null -ne $RemoveFile) { & $RemoveFile $path } else { Remove-Item -LiteralPath $path -Force }
            } catch {
                # Registration removal is the commit point; helper cleanup cannot roll back an already completed uninstall.
            }
            if (Test-Path -LiteralPath $path -PathType Leaf) { $helperResidues += $path }
        }
    }
    $runtime = Join-Path $root 'runtime'
    $previousRuntime = Join-Path $previous.root 'runtime'
    try { if (Test-Path -LiteralPath $runtime -PathType Container) { [IO.Directory]::Delete($runtime, $false) } } catch { }
    try { [IO.Directory]::Delete($root, $false) } catch { }
    try { if (Test-Path -LiteralPath $previousRuntime -PathType Container) { [IO.Directory]::Delete($previousRuntime, $false) } } catch { }
    try { [IO.Directory]::Delete($previous.root, $false) } catch { }
    try { if ($null -ne $shortcutDirectory -and (Test-Path -LiteralPath $shortcutDirectory -PathType Container)) { [IO.Directory]::Delete($shortcutDirectory, $false) } } catch { }
    $preservedLocations = @()
    foreach ($residualRoot in @($root, $previous.root)) {
        try {
            $preservedLocation = Protect-ControlStudioResidualLayout -Root $residualRoot
            if ($preservedLocation) {
                $preservedLocations += $preservedLocation
                if ($residualRoot -eq $root) { $helperResidues = @($helperResidues | ForEach-Object { $preservedLocation + $_.Substring($root.Length) }) }
            }
        } catch { Write-Warning 'ControlStudio payload removal committed; residue preservation remains unconfirmed and no residue was deleted.' }
    }
    $unrecognized = @()
    $unrecognized += $preservedLocations
    $unrecognized += Get-ControlStudioUnrecognizedEntries -InstallRoot $root -Files $pendingFiles
    $unrecognized += Get-ControlStudioUnrecognizedEntries -InstallRoot $previous.root -Files $previousFiles
    if ($null -ne $shortcutDirectory -and (Test-Path -LiteralPath $shortcutDirectory -PathType Container)) { $unrecognized += $shortcutDirectory }
    if ($helperResidues.Count -gt 0) {
        $helperLocations = ($helperResidues | Sort-Object -Unique) -join '; '
        Write-Output "ControlStudio app payload and uninstall registration were removed; uninstall helper files remain as cleanup residue: $helperLocations"
    }
    if ($unrecognized.Count -gt 0) {
        $locations = ($unrecognized | Sort-Object -Unique) -join '; '
        Write-Output "ControlStudio payload removed; unrecognized files were preserved at: $locations"
    } elseif ($helperResidues.Count -eq 0) {
        Write-Output "ControlStudio removed; user data and external files were preserved: $root"
    }
}

Export-ModuleMember -Function Invoke-ControlStudioUninstall, Get-ControlStudioRegistryEntries, Get-ControlStudioRetainedLayout, Get-ControlStudioLayoutInventory, Assert-ControlStudioLayoutInventory, Remove-ControlStudioVerifiedLayoutPayload, Move-ControlStudioInstallDirectory, Protect-ControlStudioResidualLayout
