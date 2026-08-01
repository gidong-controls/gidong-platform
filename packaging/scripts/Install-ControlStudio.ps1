[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$bomPath = Join-Path $source 'controlstudio.bom.json'
if (-not (Test-Path -LiteralPath $bomPath -PathType Leaf)) { throw 'ControlStudio installer BOM is missing.' }
$bom = Get-Content -LiteralPath $bomPath -Raw | ConvertFrom-Json
if ($bom.bom_schema -ne 'controlstudio-bom/v1') { throw 'Unsupported ControlStudio installer BOM schema.' }
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
    $verified[$component.id] = $artifact
}
$contract = $bom.contract_artifact
if ($null -eq $contract -or [IO.Path]::GetFileName($contract.artifact) -ne $contract.artifact) { throw 'Installer public contracts artifact is invalid.' }
$contractPath = Join-Path $source $contract.artifact
if (-not (Test-Path -LiteralPath $contractPath -PathType Leaf)) { throw 'Installer public contracts artifact is missing.' }
$contractHash = (Get-FileHash -LiteralPath $contractPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($contractHash -ne $contract.sha256.ToLowerInvariant()) { throw 'Installer public contracts artifact hash mismatch.' }

if ([string]::IsNullOrWhiteSpace($env:CONTROLSTUDIO_INSTALL_ROOT)) {
    $installRoot = Join-Path $env:LOCALAPPDATA 'Programs\ControlStudio'
} else {
    $installRoot = [IO.Path]::GetFullPath($env:CONTROLSTUDIO_INSTALL_ROOT)
}
if (Test-Path -LiteralPath $installRoot) { throw "ControlStudio install location already exists: $installRoot" }
$runtime = Join-Path $installRoot 'runtime'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
Copy-Item -LiteralPath $verified.platform -Destination (Join-Path $installRoot 'ControlStudio.exe')
Copy-Item -LiteralPath $verified.agent -Destination (Join-Path $runtime 'controlstudio-agent.exe')
Copy-Item -LiteralPath $verified.'plc-mcp' -Destination (Join-Path $runtime 'controlstudio-plc-mcp.exe')
Copy-Item -LiteralPath $contractPath -Destination (Join-Path $runtime $contract.artifact)
Copy-Item -LiteralPath $bomPath -Destination (Join-Path $runtime 'controlstudio.bom.json')

if ($env:CONTROLSTUDIO_SKIP_SHORTCUTS -ne '1') {
    $shortcutDirectory = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\ControlStudio'
    New-Item -ItemType Directory -Path $shortcutDirectory -Force | Out-Null
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path $shortcutDirectory 'ControlStudio.lnk'))
    $shortcut.TargetPath = Join-Path $installRoot 'ControlStudio.exe'
    $shortcut.WorkingDirectory = $installRoot
    $shortcut.Save()
}
Write-Output "ControlStudio installed: $installRoot"
