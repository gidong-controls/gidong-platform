[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$InstalledRoot,
  [Parameter(Mandatory=$true)][string]$ProfileRoot,
  [Parameter(Mandatory=$true)][string]$ProjectPath,
  [Parameter(Mandatory=$true)][string]$ExpectedUserSid,
  [string]$StatusPath
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskStream = $null; $taskAttempted = $false; $taskExitCode = 1
$taskRecord = [ordered]@{ stage='preflight'; outcome='not_started'; xgPid=$null; launcherPid=$null }
function Write-TaskState([string]$Stage, [string]$Outcome) {
  $script:taskRecord.stage=$Stage; $script:taskRecord.outcome=$Outcome
  if ($script:taskStream) {
    $taskBytes=[Text.Encoding]::UTF8.GetBytes(($script:taskRecord | ConvertTo-Json -Compress))
    $script:taskStream.Position=0; $script:taskStream.SetLength(0)
    $script:taskStream.Write($taskBytes,0,$taskBytes.Length); $script:taskStream.Flush($true)
  }
}
try {
  Write-TaskState 'same_user_admin_guard' 'not_started'
  $taskIdentity=[Security.Principal.WindowsIdentity]::GetCurrent()
  $taskPrincipal=New-Object Security.Principal.WindowsPrincipal($taskIdentity)
  if ($taskIdentity.User.Value -cne $ExpectedUserSid -or -not $taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'guard' }
  if ($StatusPath) { $taskStream=[IO.File]::Open([IO.Path]::GetFullPath($StatusPath),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read) }
  Write-TaskState 'installed_profile_guard' 'not_started'
  $taskApp=[IO.Path]::GetFullPath($InstalledRoot); $taskProfile=[IO.Path]::GetFullPath($ProfileRoot)
  $taskProject=[IO.Path]::GetFullPath($ProjectPath)
  $taskExe=Join-Path $taskApp 'ControlStudio Beta.exe'; $taskXgExe='C:\XG5000\XG5000.exe'
  $taskBomPath=Join-Path $taskApp 'runtime\controlstudio.bom.json'
  foreach ($taskFile in @($taskExe,$taskXgExe,$taskBomPath,$taskProject)) { if (-not (Test-Path -LiteralPath $taskFile -PathType Leaf)) { throw 'guard' } }
  $taskBom=Get-Content -LiteralPath $taskBomPath -Raw | ConvertFrom-Json
  if ($taskBom.release_channel -cne 'beta' -or -not $taskBom.product_release) { throw 'guard' }
  $taskEnvironment=@{ CONTROLSTUDIO_DATABASE=(Join-Path $taskProfile 'controlstudio.sqlite3'); CONTROLSTUDIO_KNOWLEDGE_DATABASE=(Join-Path $taskProfile 'controlstudio-knowledge.sqlite3'); CONTROLSTUDIO_WORKSPACE_ROOT=(Join-Path $taskProfile 'workspaces'); CONTROLSTUDIO_CHANGE_JOURNAL_ROOT=(Join-Path $taskProfile 'change-transactions') }
  foreach ($taskKey in @('CONTROLSTUDIO_DATABASE','CONTROLSTUDIO_KNOWLEDGE_DATABASE')) { if (-not (Test-Path -LiteralPath $taskEnvironment[$taskKey] -PathType Leaf)) { throw 'guard' } }
  foreach ($taskKey in @('CONTROLSTUDIO_WORKSPACE_ROOT','CONTROLSTUDIO_CHANGE_JOURNAL_ROOT')) { if (-not (Test-Path -LiteralPath $taskEnvironment[$taskKey] -PathType Container)) { throw 'guard' } }
  Write-TaskState 'processes_and_ports_guard' 'not_started'
  $taskNames=@('ControlStudio','ControlStudio Beta','ControlStudio Development','controlstudio-platform','controlstudio-host','controlstudio-launcher','controlstudio-agent','controlstudio-plc-mcp','controlstudio-xg5000-cli','XG5000','SimCore')
  if (@([Diagnostics.Process]::GetProcesses() | Where-Object { $taskNames -contains $_.ProcessName }).Count -ne 0) { throw 'guard' }
  if (@([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() | Where-Object { $_.Port -in @(8766,8767) }).Count -ne 0) { throw 'guard' }
  Write-TaskState 'xg_public_launch' 'unknown'; $taskAttempted=$true
  $taskXg=Start-Process -FilePath $taskXgExe -ArgumentList ('"'+$taskProject+'"') -WindowStyle Normal -PassThru
  $taskRecord.xgPid=$taskXg.Id
  Write-TaskState 'beta_public_launch' 'unknown'
  $taskStart=New-Object Diagnostics.ProcessStartInfo
  $taskStart.FileName=$taskExe; $taskStart.Arguments='--no-config --web'
  $taskStart.WorkingDirectory=$taskApp; $taskStart.UseShellExecute=$false; $taskStart.WindowStyle=[Diagnostics.ProcessWindowStyle]::Hidden
  foreach ($taskKey in $taskEnvironment.Keys) { $taskStart.EnvironmentVariables[$taskKey]=$taskEnvironment[$taskKey] }
  $taskLauncher=[Diagnostics.Process]::Start($taskStart); $taskRecord.launcherPid=$taskLauncher.Id
  Write-TaskState 'public_launcher_wait' 'unknown'
  if (-not $taskLauncher.WaitForExit(240000)) { throw 'wait_expired' }
  if ($taskLauncher.ExitCode -ne 0) { throw 'launcher_failed' }
  Write-TaskState 'public_launcher_bootstrap_confirmed' 'PASS'; $taskExitCode=0
} catch {
  $taskRecord.outcome=if ($taskAttempted) { 'unknown' } else { 'preflight_not_started' }
  try { Write-TaskState $taskRecord.stage $taskRecord.outcome } catch { }
} finally {
  try { if ($taskStream) { $taskStream.Dispose() } } catch { $taskExitCode=1; $taskRecord.outcome='unknown' }
}
$taskRecord | ConvertTo-Json -Compress | Write-Output
exit $taskExitCode
