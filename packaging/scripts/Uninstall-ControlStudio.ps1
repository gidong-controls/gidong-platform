[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$core = Join-Path $PSScriptRoot 'Uninstall-ControlStudio.Core.psm1'
if (-not (Test-Path -LiteralPath $core -PathType Leaf)) { throw 'ControlStudio uninstall component is missing.' }
Import-Module -Name $core -Force
Invoke-ControlStudioUninstall -InstallRoot $PSScriptRoot
