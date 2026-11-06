param([Parameter(Mandatory = $true)][string]$Sandbox, [string]$InstallerPath)
$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
$sandboxFull = [IO.Path]::GetFullPath($Sandbox)
$allowedRoot = [IO.Path]::GetFullPath((Join-Path $workspace 'tmp')) + '\'
if (-not $sandboxFull.StartsWith($allowedRoot, [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $sandboxFull)) { throw 'A new workspace tmp sandbox is required.' }
New-Item -ItemType Directory -Path $sandboxFull | Out-Null
$scripts = Join-Path $workspace 'gidong-platform\packaging\scripts'
$tokens = $null; $parseErrors = $null
$core = [Management.Automation.Language.Parser]::ParseFile((Join-Path $scripts 'Uninstall-ControlStudio.Core.psm1'), [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Core parse failed.' }
foreach ($name in @('Get-ControlStudioUninstallHash', 'ConvertTo-ControlStudioFullPath', 'Get-ControlStudioLayoutInventory', 'Assert-ControlStudioLayoutInventory', 'Move-ControlStudioInstallDirectory')) {
    $definition = $core.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    . ([scriptblock]::Create($definition.Extent.Text))
}
if ([string]::IsNullOrWhiteSpace($InstallerPath)) { $InstallerPath = Join-Path $scripts 'Install-ControlStudio.ps1' }
$installer = [Management.Automation.Language.Parser]::ParseFile($InstallerPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Installer parse failed.' }
$assignment = $installer.Find({ param($node) $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$freshInventory' }, $true)
if ($null -ne $assignment) {
    $freshBlock = $assignment.Parent
} else {
    # Permit a read-only baseline comparison against the original fresh branch.
    $freshBlock = $installer.Find({ param($node) $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -eq 'Move-Item' }, $true)
    while ($null -ne $freshBlock -and -not ($freshBlock -is [Management.Automation.Language.StatementBlockAst] -and $freshBlock.Parent -is [Management.Automation.Language.IfStatementAst])) { $freshBlock = $freshBlock.Parent }
    if ($null -eq $freshBlock) { throw 'Fresh branch was not found.' }
}
$freshBody = [scriptblock]::Create(($freshBlock.Statements | ForEach-Object { $_.Extent.Text }) -join "`n")
$safeMove = ${function:Move-ControlStudioInstallDirectory}
function Assert-InstalledLayout {
    param($Root, $ExpectedBom)
    if ($script:case -eq 'postmove_failure') { throw 'fixture validation failure' }
    if ($script:case -eq 'unrelated_after_move') {
        [IO.File]::WriteAllText((Join-Path $Root 'unrelated.txt'), 'preserve')
        throw 'fixture validation failure'
    }
}
function Set-ControlStudioUninstallRegistration { param($Root, $ExpectedBom, $ExecutableName) $script:registered += 1 }
$results = @()
foreach ($script:case in @('success', 'existing_destination', 'racing_destination', 'postmove_failure', 'unrelated_after_move')) {
    $installParent = Join-Path $sandboxFull $script:case
    New-Item -ItemType Directory -Path $installParent | Out-Null
    $installLeaf = 'install'; $installRoot = Join-Path $installParent $installLeaf
    $stagingRoot = Join-Path $installParent 'staging'
    New-Item -ItemType Directory -Path $stagingRoot | Out-Null
    [IO.File]::WriteAllText((Join-Path $stagingRoot 'owned.txt'), 'owned')
    $script:registered = 0; $bom = @{}; $executableName = 'fixture.exe'
    if ($script:case -eq 'existing_destination') {
        New-Item -ItemType Directory -Path $installRoot | Out-Null
        [IO.File]::WriteAllText((Join-Path $installRoot 'unrelated.txt'), 'preserve')
    }
    $script:raced = $false
    function Move-ControlStudioInstallDirectory {
        param($SourceRoot, $DestinationRoot, $Parent)
        if ($script:case -eq 'racing_destination' -and -not $script:raced) {
            $script:raced = $true
            # Race after the helper's Test-Path check, immediately before Directory.Move.
            function Test-Path {
                param($LiteralPath)
                $answer = Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath
                if ($LiteralPath -eq $DestinationRoot -and -not $answer) {
                    [IO.Directory]::CreateDirectory($DestinationRoot) | Out-Null
                    [IO.File]::WriteAllText((Join-Path $DestinationRoot 'unrelated.txt'), 'preserve')
                }
                return $answer
            }
        }
        & $safeMove -SourceRoot $SourceRoot -DestinationRoot $DestinationRoot -Parent $Parent
    }
    $failure = $null
    try { . $freshBody } catch { $failure = $_.Exception.Message }
    $failedRoots = @(Get-ChildItem -LiteralPath $installParent -Directory | Where-Object { $_.Name -like 'install.failed-*' })
    if ($script:case -eq 'success') {
        if ($failure -or $script:registered -ne 1 -or -not (Test-Path -LiteralPath (Join-Path $installRoot 'owned.txt')) -or (Test-Path -LiteralPath $stagingRoot)) { throw 'Success case failed.' }
    } elseif ($script:case -in @('existing_destination', 'racing_destination')) {
        if (-not $failure -or [IO.File]::ReadAllText((Join-Path $installRoot 'unrelated.txt')) -ne 'preserve' -or -not (Test-Path -LiteralPath (Join-Path $stagingRoot 'owned.txt')) -or (Test-Path -LiteralPath (Join-Path $installRoot 'staging'))) { throw 'Competing destination was changed.' }
    } elseif ($script:case -eq 'postmove_failure') {
        if ($failure -ne 'fixture validation failure' -or (Test-Path -LiteralPath $installRoot) -or $failedRoots.Count -ne 1 -or -not (Test-Path -LiteralPath (Join-Path $failedRoots[0].FullName 'owned.txt'))) { throw 'Own failed tree was not preserved.' }
    } else {
        if ($failure -ne 'fixture validation failure' -or [IO.File]::ReadAllText((Join-Path $installRoot 'unrelated.txt')) -ne 'preserve' -or -not (Test-Path -LiteralPath (Join-Path $installRoot 'owned.txt')) -or $failedRoots.Count -ne 0) { throw 'Changed current tree was moved or deleted.' }
    }
    $results += [pscustomobject]@{ case = $script:case; passed = $true; registration_calls = $script:registered; failure = $failure }
}
[pscustomobject]@{ powershell = $PSVersionTable.PSVersion.ToString(); sandbox = $sandboxFull; cases = $results } | ConvertTo-Json -Depth 5
