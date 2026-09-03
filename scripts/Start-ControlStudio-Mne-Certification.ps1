[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$Spec,

    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$Bundle,

    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$LocalAppData,

    [Parameter(Mandatory)]
    [ValidateRange(1, [int]::MaxValue)]
    [int]$ExpectedHostListenerPid,

    [ValidateRange(0, [int]::MaxValue)]
    [int]$ExpectedXgPid = 0,

    [ValidateNotNullOrEmpty()]
    [string]$XgExecutable = 'C:\XG5000\XG5000.exe',

    [switch]$DebugCertification,
    [switch]$SelfTest,
    [switch]$ResumeAfterReviewedXgClose,
    [string]$ResumeAuthorization,
    [switch]$ResumePreflightOnly,
    [switch]$ResumeExecutionPathSelfTest,
    [switch]$ResumeAfterHiddenScratchLaunch,
    [string]$HiddenScratchResumeAuthorization,
    [switch]$HiddenScratchResumePreflightOnly,
    [switch]$HiddenScratchResumeExecutionPathSelfTest,
    [switch]$ResumeAfterReviewedVisibleScratch,
    [string]$VisibleScratchResumeAuthorization,
    [string]$ReopenIntoWorkspaceId,
    [switch]$ResumeAfterReviewedNoXgWorkspaceLaunch,
    [string]$NoXgWorkspaceLaunchAuthorization,
    [switch]$CleanupReviewedHeadlessXgThenNoXgWorkspaceLaunch,
    [string]$HeadlessXgCleanupAuthorization,
    [switch]$HeadlessXgCleanupPreflightOnly,
    [switch]$VisibleTargetPreflightOnly,
    [ValidateRange(0, [int]::MaxValue)]
    [int]$RecoverOwnedHeadlessPid = 0,
    [string]$RecoverOwnedHeadlessStartedUtc,
    [switch]$RecoverOwnedHeadlessPreflightOnly,
    [switch]$ResumeAfterFailedElevatedLaunch,
    [string]$FailedElevatedLaunchAuthorization,
    [switch]$FailedElevatedLaunchPreflightOnly
)

# Developer-only, one-shot certification/restart harness. It accepts the
# artifact data root as an argument rather than making the product launcher
# depend on test artifacts. The same in-memory secret is supplied to the
# certificate command and to the subsequently launched Host/MCP pair.
$ErrorActionPreference = 'Stop'
$platform = Split-Path -Parent $PSScriptRoot
$workspace = Split-Path -Parent $platform
$certifier = Join-Path $workspace 'gidong-plc-mcp\.venv\Scripts\controlstudio-certify-mne-capability.exe'
$mcpPython = Join-Path $workspace 'gidong-plc-mcp\.venv\Scripts\python.exe'
$hostPython = Join-Path $platform 'host\.venv\Scripts\python.exe'
$agentDevPython = Join-Path $workspace 'gidong-agent\.venv-dev\Scripts\python.exe'
$agentExecutable = Join-Path $workspace 'gidong-agent\.venv\Scripts\controlstudio-agent.exe'
$mcp = Join-Path $workspace 'gidong-plc-mcp\.venv\Scripts\controlstudio-plc-mcp.exe'
$baseUrl = 'http://127.0.0.1:8767'

function Initialize-ReviewedModalNativeApi {
    if ($null -ne ('ControlStudioMneModalNative' -as [type])) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public sealed class ControlStudioMneModalSnapshot {
    public long Handle;
    public long Parent;
    public string Title;
    public string ClassName;
    public string[] ChildTexts;
    public int[] ChildControlIds;
}

public static class ControlStudioMneModalNative {
    private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);
    private const uint BM_CLICK = 0x00F5;
    private const uint SMTO_ABORTIFHUNG = 0x0002;
    private const uint SMTO_ERRORONEXIT = 0x0020;

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool EnumChildWindows(IntPtr parent, EnumWindowsProc callback, IntPtr lParam);
    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern int GetWindowTextW(IntPtr hwnd, StringBuilder text, int maximum);
    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern int GetClassNameW(IntPtr hwnd, StringBuilder text, int maximum);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr GetParent(IntPtr hwnd);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool IsWindowEnabled(IntPtr hwnd);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr GetDlgItem(IntPtr dialog, int id);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern int GetDlgCtrlID(IntPtr hwnd);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr SendMessageTimeoutW(
        IntPtr hwnd, uint message, UIntPtr wParam, IntPtr lParam,
        uint flags, uint timeoutMilliseconds, out IntPtr result);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool ShowWindowAsync(IntPtr hwnd, int command);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool ShowWindow(IntPtr hwnd, int command);
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CommandLineToArgvW(string commandLine, out int argumentCount);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr LocalFree(IntPtr memory);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(uint desiredAccess, bool inheritHandle, uint processId);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool QueryFullProcessImageNameW(IntPtr process, uint flags, StringBuilder path, ref uint size);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    public static string GetProcessImagePath(uint processId) {
        const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;
        IntPtr process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, processId);
        if (process == IntPtr.Zero) {
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "OpenProcess failed");
        }
        try {
            var path = new StringBuilder(32768);
            uint length = (uint)path.Capacity;
            if (!QueryFullProcessImageNameW(process, 0, path, ref length)) {
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "QueryFullProcessImageNameW failed");
            }
            return path.ToString();
        } finally {
            CloseHandle(process);
        }
    }

    private static string TextOf(IntPtr hwnd) {
        var text = new StringBuilder(4096);
        GetWindowTextW(hwnd, text, text.Capacity);
        return text.ToString();
    }

    private static string ClassOf(IntPtr hwnd) {
        var text = new StringBuilder(512);
        GetClassNameW(hwnd, text, text.Capacity);
        return text.ToString();
    }

    public static ControlStudioMneModalSnapshot[] FindVisibleEnabledOwnedWindows(uint expectedPid, long mainHwnd) {
        var result = new List<ControlStudioMneModalSnapshot>();
        EnumWindows(delegate(IntPtr hwnd, IntPtr ignored) {
            uint pid;
            GetWindowThreadProcessId(hwnd, out pid);
            if (pid != expectedPid || GetParent(hwnd).ToInt64() != mainHwnd || !IsWindowVisible(hwnd) || !IsWindowEnabled(hwnd)) {
                return true;
            }
            var childTexts = new List<string>();
            var childControlIds = new List<int>();
            EnumChildWindows(hwnd, delegate(IntPtr child, IntPtr ignoredChild) {
                childTexts.Add(TextOf(child));
                childControlIds.Add(GetDlgCtrlID(child));
                return true;
            }, IntPtr.Zero);
            result.Add(new ControlStudioMneModalSnapshot {
                Handle = hwnd.ToInt64(), Parent = GetParent(hwnd).ToInt64(),
                Title = TextOf(hwnd), ClassName = ClassOf(hwnd), ChildTexts = childTexts.ToArray(),
                ChildControlIds = childControlIds.ToArray()
            });
            return true;
        }, IntPtr.Zero);
        return result.ToArray();
    }

    // This deliberately enumerates only top-level windows of one reviewed PID.
    // It is used solely to recover the window hidden by a reviewed launcher.
    public static ControlStudioMneModalSnapshot[] FindTopLevelWindows(uint expectedPid) {
        var result = new List<ControlStudioMneModalSnapshot>();
        EnumWindows(delegate(IntPtr hwnd, IntPtr ignored) {
            uint pid;
            GetWindowThreadProcessId(hwnd, out pid);
            if (pid != expectedPid) return true;
            result.Add(new ControlStudioMneModalSnapshot {
                Handle = hwnd.ToInt64(), Parent = GetParent(hwnd).ToInt64(),
                Title = TextOf(hwnd), ClassName = ClassOf(hwnd),
                ChildTexts = new string[0], ChildControlIds = new int[0]
            });
            return true;
        }, IntPtr.Zero);
        return result.ToArray();
    }

    public static bool IsVisibleAndEnabled(long hwnd) {
        IntPtr handle = new IntPtr(hwnd);
        return IsWindowVisible(handle) && IsWindowEnabled(handle);
    }

    public static bool IsVisible(long hwnd) {
        return IsWindowVisible(new IntPtr(hwnd));
    }

    public static void RestoreWindow(long hwnd) {
        IntPtr handle = new IntPtr(hwnd);
        // SW_RESTORE is intentionally bounded to this one reviewed top-level HWND.
        ShowWindowAsync(handle, 9);
        ShowWindow(handle, 9);
    }

    public static string[] ParseCommandLine(string commandLine) {
        if (String.IsNullOrWhiteSpace(commandLine)) {
            throw new ArgumentException("A process command line is required", "commandLine");
        }
        int count;
        IntPtr argv = CommandLineToArgvW(commandLine, out count);
        if (argv == IntPtr.Zero || count <= 0) {
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "CommandLineToArgvW failed");
        }
        try {
            var result = new string[count];
            for (int index = 0; index < count; index++) {
                result[index] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(argv, index * IntPtr.Size));
            }
            return result;
        } finally {
            LocalFree(argv);
        }
    }

    public static void ClickEnabledNoButton(long dialogHwnd) {
        IntPtr dialog = new IntPtr(dialogHwnd);
        IntPtr button = GetDlgItem(dialog, 7);
        if (button == IntPtr.Zero || GetDlgCtrlID(button) != 7 || !IsWindowVisible(button) || !IsWindowEnabled(button)) {
            throw new InvalidOperationException("The exact enabled IDNO button is unavailable");
        }
        IntPtr ignored;
        if (SendMessageTimeoutW(button, BM_CLICK, UIntPtr.Zero, IntPtr.Zero, SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT, 5000, out ignored) == IntPtr.Zero) {
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "IDNO BM_CLICK delivery failed");
        }
    }
}
'@
}

function Test-ExactNoSaveModalShape {
    param(
        [Parameter(Mandatory)]$Candidate,
        [Parameter(Mandatory)][int64]$ExpectedMainHwnd
    )

    if ($Candidate.Parent -ne $ExpectedMainHwnd -or $Candidate.Title -cne 'XG5000' -or $Candidate.ClassName -cne '#32770') {
        return $false
    }
    $prompt = "프로젝트창이 닫힙니다.`n저장하시겠습니까?"
    return @($Candidate.ChildTexts | Where-Object {
        ([string]$_).Replace("`r`n", "`n") -ceq $prompt
    }).Count -eq 1
}

function Test-KnownDecorativeOwnedWindow {
    param([Parameter(Mandatory)]$Candidate)

    return (
        [string]$Candidate.ClassName -ceq 'BCGPShadowSideWnd' -and
        [string]$Candidate.Title -ceq '' -and
        @($Candidate.ChildControlIds).Count -eq 0
    )
}

function Get-ActionableOwnedWindows {
    param([Parameter(Mandatory)]$Candidates)
    return @($Candidates | Where-Object { -not (Test-KnownDecorativeOwnedWindow -Candidate $_) })
}

function Resolve-ExactNoSaveModal {
    param(
        [Parameter(Mandatory)][int]$XgPid,
        [Parameter(Mandatory)][int64]$MainHwnd
    )

    Initialize-ReviewedModalNativeApi
    $modals = @([ControlStudioMneModalNative]::FindVisibleEnabledOwnedWindows([uint32]$XgPid, $MainHwnd))
    $actionable = @(Get-ActionableOwnedWindows -Candidates $modals)
    if ($actionable.Count -eq 0) { return $null }
    if ($actionable.Count -ne 1 -or -not (Test-ExactNoSaveModalShape -Candidate $actionable[0] -ExpectedMainHwnd $MainHwnd)) {
        $diagnostic = @($modals | ForEach-Object {
            [pscustomobject]@{
                class = $_.ClassName
                title = $_.Title
                parent = $_.Parent
                childControlIds = @($_.ChildControlIds)
            }
        }) | ConvertTo-Json -Compress -Depth 4
        throw "Unexpected or ambiguous XG5000 modal; refusing to choose a button. candidateCount=$($modals.Count); actionableCandidateCount=$($actionable.Count); candidates=$diagnostic"
    }
    return $actionable[0]
}

function Close-ReviewedXgForCertification {
    param([Parameter(Mandatory)]$XgProcess)

    $mainHwnd = [int64]$XgProcess.MainWindowHandle
    if ($mainHwnd -eq 0) { throw 'The reviewed XG5000 main window is unavailable' }
    $modal = Resolve-ExactNoSaveModal -XgPid $XgProcess.Id -MainHwnd $mainHwnd
    if ($null -ne $modal) {
        [ControlStudioMneModalNative]::ClickEnabledNoButton($modal.Handle)
    } elseif (-not $XgProcess.CloseMainWindow()) {
        throw 'The reviewed XG5000 main window did not accept a clean close; no process was terminated.'
    }

    # XG5000 can finish its no-save shutdown after its modal button has
    # returned.  Keep the exact modal/IDNO path and never force-kill it; allow
    # the observed natural shutdown window before declaring the boundary dirty.
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
    $clickedHandle = if ($null -ne $modal) { [int64]$modal.Handle } else { $null }
    $graceUntil = if ($null -ne $clickedHandle) { [DateTimeOffset]::UtcNow.AddSeconds(3) } else { [DateTimeOffset]::MinValue }
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $XgProcess.Refresh()
        if ($XgProcess.HasExited) { return }
        if ([DateTimeOffset]::UtcNow -lt $graceUntil) {
            Start-Sleep -Milliseconds 250
            continue
        }
        $nextModal = Resolve-ExactNoSaveModal -XgPid $XgProcess.Id -MainHwnd $mainHwnd
        if ($null -ne $nextModal) {
            if ($null -ne $clickedHandle -and [int64]$nextModal.Handle -eq $clickedHandle) {
                Start-Sleep -Milliseconds 250
                continue
            }
            if ($null -ne $clickedHandle) { throw 'A different modal appeared after the exact no-save click; refusing a second click.' }
            [ControlStudioMneModalNative]::ClickEnabledNoButton($nextModal.Handle)
            $clickedHandle = [int64]$nextModal.Handle
            $graceUntil = [DateTimeOffset]::UtcNow.AddSeconds(3)
        }
        Start-Sleep -Milliseconds 250
    }
    throw 'XG5000 did not exit after the exact no-save decision; no process was terminated.'
}

function Assert-ExactVisibleCertificationTarget {
    param(
        [Parameter(Mandatory)]$Template,
        [Parameter(Mandatory)][int]$ExpectedPid,
        [Parameter(Mandatory)][string]$ExpectedExecutable
    )

    # The product launcher owns XG5000 independently of the Host process.  Do
    # not infer ownership from a process-parent relationship: bind the reviewed
    # visible window to the disposable spec instead, then retain the complete
    # pre-close XG5000 baseline so this harness can only close that exact PID.
    $target = $Template.target
    if ($null -eq $target -or [int]$target.pid -le 0 -or [int64]$target.hwnd -le 0) {
        throw 'Certification template target PID/HWND is invalid'
    }
    if ([int]$target.pid -ne $ExpectedPid) {
        throw 'Expected XG5000 PID does not match the reviewed certification target'
    }
    if ([string]::IsNullOrWhiteSpace([string]$target.project_path) -or [string]::IsNullOrWhiteSpace([string]$target.project_hash)) {
        throw 'Certification template target project binding is invalid'
    }
    $projectPath = [System.IO.Path]::GetFullPath([string]$target.project_path)
    if (-not (Test-Path -LiteralPath $projectPath -PathType Leaf)) {
        throw 'Certification template target project is unavailable'
    }
    if ([string]$target.project_hash -notmatch '^[0-9a-fA-F]{64}$') {
        throw 'Certification template target project hash is invalid'
    }
    $markerPath = [System.IO.Path]::GetFullPath([string]$Template.scratch.marker_path)
    $backupPath = [System.IO.Path]::GetFullPath([string]$Template.scratch.backup_path)
    if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf) -or -not (Test-Path -LiteralPath $backupPath -PathType Leaf)) {
        throw 'Certification disposable marker or backup is unavailable'
    }
    try { $marker = [System.IO.File]::ReadAllText($markerPath, [Text.Encoding]::UTF8) | ConvertFrom-Json }
    catch { throw 'Certification disposable marker is not valid UTF-8 JSON' }
    if (
        $marker.schema -cne 'controlstudio-xg5000-disposable-project/1.0' -or
        $marker.confirmed_disposable -ne $true -or
        [System.IO.Path]::GetFullPath([string]$marker.project_path) -cne $projectPath -or
        [string]$marker.project_sha256 -cne [string]$target.project_hash
    ) {
        throw 'Certification disposable marker does not bind the reviewed target project'
    }
    if ((Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne ([string]$target.project_hash).ToLowerInvariant()) {
        throw 'Certification backup hash does not bind the reviewed target project'
    }

    Initialize-ReviewedModalNativeApi
    try { $process = Get-Process -Id $ExpectedPid -ErrorAction Stop }
    catch { throw 'Reviewed XG5000 target PID is unavailable' }
    $process.Refresh()
    if ($process.HasExited -or $process.ProcessName -ine 'XG5000' -or [int64]$process.MainWindowHandle -ne [int64]$target.hwnd) {
        throw 'Reviewed XG5000 target PID/HWND no longer matches the certification target'
    }
    $actualExecutable = [ControlStudioMneModalNative]::GetProcessImagePath([uint32]$ExpectedPid)
    if ([System.IO.Path]::GetFullPath($actualExecutable) -cne [System.IO.Path]::GetFullPath($ExpectedExecutable)) {
        throw 'Reviewed XG5000 target executable does not match the certification executable'
    }
    $baselinePids = @(
        Get-Process -Name XG5000 -ErrorAction SilentlyContinue |
            ForEach-Object { [int]$_.Id } |
            Sort-Object -Unique
    )
    if ($baselinePids -notcontains $ExpectedPid) {
        throw 'Reviewed XG5000 target is absent from the immutable XG5000 baseline'
    }
    return [pscustomobject]@{
        Process = $process
        BaselinePids = $baselinePids
        ProcessStartedUtc = $process.StartTime.ToUniversalTime()
        ProjectPath = $projectPath
        ProjectHash = [string]$target.project_hash
    }
}

function Assert-ExactOwnedHeadlessCertificationFailure {
    param(
        [Parameter(Mandatory)]$Template,
        [Parameter(Mandatory)][int]$ExpectedPid,
        [Parameter(Mandatory)][string]$ExpectedStartedUtc,
        [Parameter(Mandatory)][string]$ExpectedExecutable,
        [Parameter(Mandatory)][string]$PythonExecutable
    )

    if ($ExpectedPid -le 0 -or [string]::IsNullOrWhiteSpace($ExpectedStartedUtc)) {
        throw 'Headless recovery requires an exact PID and start time'
    }
    if ($null -ne (Get-DevelopmentListenerPid)) {
        throw 'Headless recovery refuses to stop a process while a Host listener is active'
    }
    $target = $Template.target
    $projectPath = [IO.Path]::GetFullPath([string]$target.project_path)
    if (-not (Test-Path -LiteralPath $projectPath -PathType Leaf) -or [string]$target.project_hash -notmatch '^[0-9a-fA-F]{64}$') {
        throw 'Headless recovery target project binding is invalid'
    }
    try { $process = Get-Process -Id $ExpectedPid -ErrorAction Stop }
    catch { throw 'Headless recovery PID is unavailable' }
    $process.Refresh()
    if (
        $process.HasExited -or
        $process.ProcessName -ine 'XG5000' -or
        $process.StartTime.ToUniversalTime().ToString('o') -cne $ExpectedStartedUtc -or
        [int64]$process.MainWindowHandle -ne 0 -or
        [string]$process.MainWindowTitle -cne ''
    ) {
        throw 'Headless recovery PID/start/window identity differs'
    }
    Initialize-ReviewedModalNativeApi
    $actualExecutable = [ControlStudioMneModalNative]::GetProcessImagePath([uint32]$ExpectedPid)
    if ([IO.Path]::GetFullPath($actualExecutable) -cne [IO.Path]::GetFullPath($ExpectedExecutable)) {
        throw 'Headless recovery executable differs'
    }
    $usageCode = @'
import sys
from xg5000_control.live.windows_file_usage import process_uses_file
print('true' if process_uses_file(int(sys.argv[1]), sys.argv[2], timeout_seconds=0.75) else 'false')
'@
    $saved = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $usage = & $PythonExecutable -c $usageCode $ExpectedPid $projectPath 2>&1
        $exit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $saved }
    if ($exit -ne 0 -or (@($usage | Where-Object { $_ -match '^(?i:true)$' }).Count -ne 1)) {
        throw 'Headless recovery did not prove that the exact PID owns the reviewed working copy'
    }
    return [pscustomobject]@{
        Process = $process
        ProjectPath = $projectPath
        ProjectHash = [string]$target.project_hash
    }
}

function Stop-ExactOwnedHeadlessCertificationFailure {
    param([Parameter(Mandatory)]$Reviewed)

    $process = $Reviewed.Process
    $process.Refresh()
    if ($process.HasExited -or [int64]$process.MainWindowHandle -ne 0 -or [string]$process.MainWindowTitle -cne '') {
        throw 'Headless recovery process changed before exact termination'
    }
    Stop-Process -Id $process.Id -Force -ErrorAction Stop
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        if ($null -eq (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) {
            # Process exit precedes release of the XG5000 file handle on some
            # builds.  Keep polling within the same bounded recovery window.
            try {
                $actualHash = (Get-FileHash -LiteralPath $Reviewed.ProjectPath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
                if ($actualHash -cne $Reviewed.ProjectHash.ToLowerInvariant()) {
                    throw 'Headless recovery changed the reviewed working-copy hash'
                }
                return
            } catch [System.IO.IOException] {
                Start-Sleep -Milliseconds 250
                continue
            }
        }
        Start-Sleep -Milliseconds 250
    }
    throw 'Exact headless certification process did not exit or release its working-copy file'
}

function Test-FailedElevatedLaunchAuthorizationShape {
    param([Parameter(Mandatory)]$Authorization)

    $expected = @('schema', 'prior', 'inputs', 'state', 'expectedFailure')
    if ((@($Authorization.PSObject.Properties.Name | Sort-Object) -join ',') -cne ((@($expected | Sort-Object) -join ','))) { return $false }
    return (
        $Authorization.schema -ceq 'controlstudio-mne-resume-after-failed-elevated-launch/v1' -and
        $Authorization.expectedFailure -ceq 'scratch_xg5000_main_window_timeout/v1' -and
        $null -ne $Authorization.prior -and $null -ne $Authorization.inputs -and $null -ne $Authorization.state
    )
}

function Assert-ResumeAfterFailedElevatedLaunch {
    param(
        [Parameter(Mandatory)][string]$AuthorizationPath,
        [Parameter(Mandatory)][string]$SpecPath,
        [Parameter(Mandatory)]$Template,
        [Parameter(Mandatory)][string]$BundlePath,
        [Parameter(Mandatory)][string]$PythonExecutable
    )

    if ($null -ne (Get-DevelopmentListenerPid) -or (Test-Path -LiteralPath $BundlePath)) {
        throw 'Failed-elevated-launch resume requires no Host listener and no capability bundle'
    }
    $exactAuthorizationPath = [IO.Path]::GetFullPath($AuthorizationPath)
    if (-not (Test-Path -LiteralPath $exactAuthorizationPath -PathType Leaf)) { throw 'Failed-elevated-launch authorization is not a file' }
    $authorization = [IO.File]::ReadAllText($exactAuthorizationPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if (-not (Test-FailedElevatedLaunchAuthorizationShape -Authorization $authorization)) { throw 'Failed-elevated-launch authorization fields or purpose are invalid' }
    $inputs = $authorization.inputs
    foreach ($item in @(
        @{ Path = $SpecPath; Hash = $inputs.specSha256 },
        @{ Path = [string]$Template.scratch.backup_path; Hash = $inputs.backupSha256 },
        @{ Path = [string]$Template.scratch.marker_path; Hash = $inputs.markerSha256 }
    )) {
        if (-not (Test-Path -LiteralPath $item.Path -PathType Leaf) -or (Get-FileHash -LiteralPath $item.Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne ([string]$item.Hash).ToLowerInvariant()) {
            throw 'Failed-elevated-launch authorization input digest mismatched'
        }
    }
    if (
        $inputs.specPath -cne $SpecPath -or
        $inputs.projectPath -cne [string]$Template.target.project_path -or
        $inputs.projectHash -cne [string]$Template.target.project_hash -or
        $inputs.workspaceId -cne [string]$Template.target.workspace_id
    ) { throw 'Failed-elevated-launch authorization inputs differ' }
    $actualProjectHash = (Get-FileHash -LiteralPath ([string]$Template.target.project_path) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualProjectHash -cne ([string]$Template.target.project_hash).ToLowerInvariant()) { throw 'Failed-elevated-launch working-copy hash differs' }
    $prior = $authorization.prior
    foreach ($artifact in @(
        @{ Path = $prior.receiptPath; Hash = $prior.receiptSha256; Label = 'receipt' },
        @{ Path = $prior.logPath; Hash = $prior.logSha256; Label = 'log' }
    )) {
        $path = [IO.Path]::GetFullPath([string]$artifact.Path)
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne ([string]$artifact.Hash).ToLowerInvariant()) {
            throw "Failed-elevated-launch prior $($artifact.Label) digest mismatched"
        }
    }
    $priorReceipt = [IO.File]::ReadAllText([IO.Path]::GetFullPath([string]$prior.receiptPath), [Text.Encoding]::UTF8) | ConvertFrom-Json
    $priorLog = [IO.File]::ReadAllText([IO.Path]::GetFullPath([string]$prior.logPath), [Text.Encoding]::UTF8)
    if (
        $priorReceipt.schema -cne 'controlstudio-elevated-certification-wrapper/v1' -or
        $priorReceipt.status -cne 'failed' -or
        ([string]$priorReceipt.error).IndexOf('Scratch XG5000 did not expose its main window within 60 seconds', [StringComparison]::Ordinal) -lt 0 -or
        $priorLog.IndexOf('Scratch XG5000 did not expose its main window within 60 seconds', [StringComparison]::Ordinal) -lt 0
    ) { throw 'Failed-elevated-launch prior failure is not the reviewed main-window timeout' }
    $state = $authorization.state
    if ($state.listener -cne 'absent' -or [int]$state.ownedHeadlessPid -le 0 -or [string]::IsNullOrWhiteSpace([string]$state.ownedHeadlessStartedUtc)) {
        throw 'Failed-elevated-launch state binding is invalid'
    }
    if ($null -ne (Get-Process -Id ([int]$state.ownedHeadlessPid) -ErrorAction SilentlyContinue)) {
        throw 'Failed-elevated-launch owned headless PID is still present or was reused'
    }
    return [pscustomobject]@{
        WorkspaceId = [string]$inputs.workspaceId
        ProjectPath = [string]$inputs.projectPath
        ProjectHash = [string]$inputs.projectHash
        Xg5000PidsBeforeLaunch = @(
            Get-Process -Name XG5000 -ErrorAction SilentlyContinue | ForEach-Object { [int]$_.Id } | Sort-Object -Unique
        )
    }
}

function Assert-ResumeAfterReviewedXgClose {
    param(
        [Parameter(Mandatory)][string]$AuthorizationPath,
        [Parameter(Mandatory)][string]$SpecPath,
        [Parameter(Mandatory)]$Template,
        [Parameter(Mandatory)][string]$BundlePath
    )

    if ($null -ne (Get-Process -Name XG5000 -ErrorAction SilentlyContinue)) { throw 'Resume requires no XG5000 process' }
    if (Test-Path -LiteralPath $BundlePath) { throw 'Resume requires the capability bundle to remain absent' }
    $exactAuthorizationPath = [System.IO.Path]::GetFullPath($AuthorizationPath)
    if (-not (Test-Path -LiteralPath $exactAuthorizationPath -PathType Leaf)) { throw 'Resume authorization is not a file' }
    $authorization = [System.IO.File]::ReadAllText($exactAuthorizationPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $expectedKeys = @('schema', 'priorResult', 'inputs', 'hostListener', 'expectedFailure')
    if (
        ((@($authorization.PSObject.Properties.Name | Sort-Object) -join ',')) -cne
        ((@($expectedKeys | Sort-Object) -join ','))
    ) {
        throw 'Resume authorization fields are invalid'
    }
    if ($authorization.schema -cne 'controlstudio-mne-resume-after-xg-close/v1' -or $authorization.expectedFailure -cne 'unexpected_modal_after_reviewed_clean_stop/v1') {
        throw 'Resume authorization purpose is invalid'
    }
    $listenerAuthorization = $authorization.hostListener
    if ($null -eq $listenerAuthorization -or [int]$listenerAuthorization.pid -le 0 -or [int]$listenerAuthorization.closedXgPid -le 0 -or [string]::IsNullOrWhiteSpace([string]$listenerAuthorization.startTimeUtc)) {
        throw 'Resume authorization host identity is invalid'
    }
    if ((Get-DevelopmentListenerPid) -ne [int]$listenerAuthorization.pid) { throw 'Resume requires the reviewed Host listener PID' }
    $listener = Get-Process -Id ([int]$listenerAuthorization.pid) -ErrorAction Stop
    if ($listener.StartTime.ToUniversalTime().ToString('o') -cne [string]$listenerAuthorization.startTimeUtc) {
        throw 'Resume requires the reviewed Host listener start time'
    }
    $inputs = $authorization.inputs
    if ($null -eq $inputs -or $inputs.specPath -cne $SpecPath -or $inputs.scratchPath -cne $Template.target.project_path -or $inputs.backupPath -cne $Template.scratch.backup_path -or $inputs.markerPath -cne $Template.scratch.marker_path) {
        throw 'Resume authorization does not bind these exact certification inputs'
    }
    foreach ($item in @(
        @{ Path = $SpecPath; Hash = $inputs.specSha256 },
        @{ Path = [string]$Template.scratch.backup_path; Hash = $inputs.backupSha256 },
        @{ Path = [string]$Template.scratch.marker_path; Hash = $inputs.markerSha256 }
    )) {
        if (-not (Test-Path -LiteralPath $item.Path -PathType Leaf) -or (Get-FileHash -LiteralPath $item.Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne ([string]$item.Hash).ToLowerInvariant()) {
            throw 'Resume authorization input digest mismatched'
        }
    }
    $prior = $authorization.priorResult
    $priorPath = [System.IO.Path]::GetFullPath([string]$prior.path)
    if (-not (Test-Path -LiteralPath $priorPath -PathType Leaf) -or (Get-FileHash -LiteralPath $priorPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne ([string]$prior.sha256).ToLowerInvariant() -or (Get-Item -LiteralPath $priorPath).LastWriteTimeUtc.ToString('o') -cne [string]$prior.lastWriteTimeUtc) {
        throw 'Resume authorization prior-result digest or timestamp mismatched'
    }
    $priorJson = [System.IO.File]::ReadAllText($priorPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    if (
        $priorJson.status -cne 'error' -or
        [int]$priorJson.exitCode -ne 1 -or
        [string]::IsNullOrWhiteSpace([string]$prior.outputFragment) -or
        ([string]$priorJson.output).IndexOf([string]$prior.outputFragment, [System.StringComparison]::Ordinal) -lt 0
    ) {
        throw 'Resume authorization does not bind the reviewed clean-stop failure'
    }
    return $listenerAuthorization
}

function Get-DevelopmentListenerPid {
    $netstat = Join-Path $env:SystemRoot 'System32\netstat.exe'
    $line = (& $netstat -ano -p TCP | Where-Object {
        $_ -match '^\s*TCP\s+127\.0\.0\.1:8767\s+\S+\s+LISTENING\s+(\d+)\s*$'
    } | Select-Object -First 1)
    if ($line -match 'LISTENING\s+(\d+)\s*$') {
        return [int]$Matches[1]
    }
    return $null
}

function Get-ExactProcessTree {
    param([Parameter(Mandatory)][int]$RootPid)

    $all = Get-CimInstance Win32_Process
    $records = @{}
    foreach ($record in $all) { $records[[int]$record.ProcessId] = $record }
    if (-not $records.ContainsKey($RootPid)) {
        throw "The expected listener process is gone: $RootPid"
    }
    $pending = [System.Collections.Generic.Queue[object]]::new()
    $pending.Enqueue([pscustomobject]@{ Pid = $RootPid; Depth = 0 })
    $seen = [System.Collections.Generic.HashSet[int]]::new()
    $tree = @()
    while ($pending.Count -gt 0) {
        $entry = $pending.Dequeue()
        if (-not $seen.Add([int]$entry.Pid)) { continue }
        $record = $records[[int]$entry.Pid]
        if ($null -eq $record) { continue }
        try { $started = (Get-Process -Id ([int]$record.ProcessId) -ErrorAction Stop).StartTime.ToUniversalTime() }
        catch { throw "Unable to capture start time for exact process $($record.ProcessId)" }
        $tree += [pscustomobject]@{
            Pid = [int]$record.ProcessId
            ParentPid = [int]$record.ParentProcessId
            Name = [string]$record.Name
            Depth = [int]$entry.Depth
            StartedUtc = $started
        }
        foreach ($child in $records.Values) {
            if ([int]$child.ParentProcessId -eq [int]$record.ProcessId) {
                $pending.Enqueue([pscustomobject]@{ Pid = [int]$child.ProcessId; Depth = ([int]$entry.Depth + 1) })
            }
        }
    }
    return $tree
}

function Stop-ExactDevelopmentTree {
    param(
        [Parameter(Mandatory)][int]$ListenerPid,
        [Parameter(Mandatory)]$ReviewedTarget
    )

    if ((Get-DevelopmentListenerPid) -ne $ListenerPid) {
        throw "Port 8767 is not owned by the reviewed listener PID $ListenerPid"
    }
    $reviewedProcess = $ReviewedTarget.Process
    $reviewedProcess.Refresh()
    if (
        $reviewedProcess.HasExited -or
        $reviewedProcess.StartTime.ToUniversalTime() -ne $ReviewedTarget.ProcessStartedUtc -or
        [int64]$reviewedProcess.MainWindowHandle -le 0 -or
        [int]$reviewedProcess.Id -notin @($ReviewedTarget.BaselinePids)
    ) {
        throw 'Reviewed XG5000 target changed before exact certification close'
    }
    Close-ReviewedXgForCertification -XgProcess $reviewedProcess

    # Only after the one reviewed XG5000 process has gone away may the exact
    # Host listener tree be stopped.  Other user XG5000 processes are neither
    # descendants nor targets and are intentionally outside this operation.
    $tree = Get-ExactProcessTree -RootPid $ListenerPid

    foreach ($entry in ($tree | Where-Object { $_.Pid -ne [int]$ReviewedTarget.Process.Id } | Sort-Object Depth -Descending)) {
        $current = Get-Process -Id $entry.Pid -ErrorAction SilentlyContinue
        if ($null -eq $current) { continue }
        if ($current.StartTime.ToUniversalTime() -ne $entry.StartedUtc) {
            throw "PID reuse detected for $($entry.Pid); refusing to terminate it"
        }
        Stop-Process -Id $entry.Pid -Force -ErrorAction Stop
    }
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(25)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        if ($null -eq (Get-DevelopmentListenerPid)) { return }
        Start-Sleep -Milliseconds 250
    }
    throw 'The exact prior Host listener did not release port 8767'
}

function Stop-ExactHostTreeAfterReviewedXgClose {
    param(
        [Parameter(Mandatory)][int]$ListenerPid,
        [Parameter(Mandatory)][string]$ListenerStartTimeUtc,
        [Parameter(Mandatory)][int]$ClosedXgPid,
        [switch]$NoOp
    )

    if ((Get-DevelopmentListenerPid) -ne $ListenerPid) {
        throw 'The reviewed Host listener PID changed before resume'
    }
    $tree = Get-ExactProcessTree -RootPid $ListenerPid
    $listenerRecord = $tree | Where-Object { $_.Pid -eq $ListenerPid }
    if ($null -eq $listenerRecord -or $listenerRecord.StartedUtc.ToString('o') -cne $ListenerStartTimeUtc) {
        throw 'The reviewed Host listener start time changed before resume'
    }
    if ($null -ne ($tree | Where-Object { $_.Pid -eq $ClosedXgPid -or $_.Name -ieq 'XG5000.exe' })) {
        throw 'Resume requires the reviewed XG5000 process to remain absent from the Host tree'
    }
    if ($null -ne (Get-Process -Name XG5000 -ErrorAction SilentlyContinue)) {
        throw 'Resume requires no XG5000 process'
    }
    if ($NoOp) {
        return [pscustomobject]@{ listenerPid = $ListenerPid; processCount = @($tree).Count; result = 'ready_to_stop_exact_host_tree' }
    }
    foreach ($entry in ($tree | Sort-Object Depth -Descending)) {
        $current = Get-Process -Id $entry.Pid -ErrorAction SilentlyContinue
        if ($null -eq $current) { continue }
        if ($current.StartTime.ToUniversalTime() -ne $entry.StartedUtc) {
            throw "PID reuse detected for $($entry.Pid); refusing to terminate it"
        }
        Stop-Process -Id $entry.Pid -Force -ErrorAction Stop
    }
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(25)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        if ($null -eq (Get-DevelopmentListenerPid)) { return }
        Start-Sleep -Milliseconds 250
    }
    throw 'The reviewed Host listener did not release port 8767'
}

function Wait-ForScratchXgWindow {
    param([Parameter(Mandatory)]$Process)

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(60)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $Process.Refresh()
        if ($Process.HasExited) { throw 'Scratch XG5000 exited before its main window became available' }
        if ($Process.MainWindowHandle -ne 0 -and $Process.MainWindowTitle -match 'XG5000') {
            return $Process
        }
        Start-Sleep -Milliseconds 250
    }
    throw 'Scratch XG5000 did not expose its main window within 60 seconds'
}

function Test-ExactHiddenScratchWindow {
    param(
        [Parameter(Mandatory)]$Snapshot,
        [Parameter(Mandatory)]$Reviewed
    )

    return (
        [int64]$Snapshot.Handle -eq [int64]$Reviewed.hwnd -and
        [string]$Snapshot.Title -ceq [string]$Reviewed.title -and
        [string]$Snapshot.ClassName -ceq [string]$Reviewed.className -and
        -not [ControlStudioMneModalNative]::IsVisibleAndEnabled([int64]$Snapshot.Handle)
    )
}

function Get-ArgvDigest {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $bytes = [Text.Encoding]::UTF8.GetBytes(($Arguments -join [char]0))
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Assert-ExactPreservedXgBaseline {
    param(
        [Parameter(Mandatory)]$Baseline,
        [Parameter(Mandatory)][int]$ReviewedPid,
        [switch]$RequireReviewedProcess
    )

    $entries = @($Baseline)
    if ($entries.Count -eq 0) { throw 'Visible-scratch resume requires a non-empty preserved XG5000 baseline' }
    $seenPids = @{}
    foreach ($entry in $entries) {
        if ($null -eq $entry) { throw 'Preserved XG5000 baseline contains an invalid entry' }
        $keys = @($entry.PSObject.Properties.Name | Sort-Object)
        if (($keys -join ',') -cne 'Pid,ProcessName,StartUtc') {
            throw 'Preserved XG5000 baseline entry fields are invalid'
        }
        $processId = [int]$entry.Pid
        if ($processId -le 0 -or $processId -eq $ReviewedPid -or $seenPids.ContainsKey($processId) -or [string]$entry.ProcessName -cne 'XG5000') {
            throw 'Preserved XG5000 baseline identity is invalid'
        }
        try {
            $expectedStart = [DateTime]::Parse(
                [string]$entry.StartUtc,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::RoundtripKind
            ).ToUniversalTime()
        } catch {
            throw 'Preserved XG5000 baseline start time is invalid'
        }
        if ($expectedStart.ToString('o') -cne [string]$entry.StartUtc) {
            throw 'Preserved XG5000 baseline start time is not canonical UTC'
        }
        $seenPids[$processId] = $entry
    }

    $allXg = @(Get-Process -Name XG5000 -ErrorAction SilentlyContinue)
    $expectedCount = $entries.Count + $(if ($RequireReviewedProcess) { 1 } else { 0 })
    if ($allXg.Count -ne $expectedCount) {
        throw 'Visible-scratch resume XG5000 process set differs from the reviewed target plus preserved baseline'
    }
    $observed = @{}
    foreach ($process in $allXg) { $observed[[int]$process.Id] = $process }
    $reviewed = $observed[$ReviewedPid]
    if ($RequireReviewedProcess -and $null -eq $reviewed) { throw 'Reviewed visible scratch PID is absent from the XG5000 process set' }
    if (-not $RequireReviewedProcess -and $null -ne $reviewed) { throw 'Reviewed visible scratch PID remained after the exact no-save close' }
    foreach ($processId in $seenPids.Keys) {
        $process = $observed[[int]$processId]
        if ($null -eq $process) { throw 'Preserved XG5000 baseline PID is absent' }
        $process.Refresh()
        $expected = $seenPids[$processId]
        if (
            [string]$process.ProcessName -cne [string]$expected.ProcessName -or
            $process.StartTime.ToUniversalTime().ToString('o') -cne [string]$expected.StartUtc
        ) {
            throw 'Preserved XG5000 baseline process identity changed'
        }
    }
    return @($entries)
}

function Assert-ResumeAfterHiddenScratchLaunch {
    param(
        [Parameter(Mandatory)][string]$AuthorizationPath,
        [Parameter(Mandatory)][string]$SpecPath,
        [Parameter(Mandatory)]$Template,
        [Parameter(Mandatory)][string]$BundlePath,
        [switch]$RequireVisible
    )

    if ($null -ne (Get-DevelopmentListenerPid)) { throw 'Hidden-scratch resume requires port 8767 to remain unbound' }
    if (Test-Path -LiteralPath $BundlePath) { throw 'Hidden-scratch resume requires the capability bundle to remain absent' }
    $exactAuthorizationPath = [System.IO.Path]::GetFullPath($AuthorizationPath)
    if (-not (Test-Path -LiteralPath $exactAuthorizationPath -PathType Leaf)) { throw 'Hidden-scratch resume authorization is not a file' }
    # Preserved process StartUtc values are signed authorization identities, so
    # keep ISO-8601 text intact rather than letting PowerShell coerce it to a
    # DateTime and silently drop its original precision/UTC representation.
    $authorization = [System.IO.File]::ReadAllText($exactAuthorizationPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json -DateKind String
    $expectedKeys = if ($RequireVisible) {
        @('schema', 'disabledReceipt', 'inputs', 'scratchProcess', 'preservedBaseline', 'expectedFailure')
    } else {
        @('schema', 'priorResult', 'inputs', 'scratchProcess', 'expectedFailure')
    }
    if ((@($authorization.PSObject.Properties.Name | Sort-Object) -join ',') -cne ((@($expectedKeys | Sort-Object) -join ','))) {
        throw 'Hidden-scratch resume authorization fields are invalid'
    }
    # A visible process quarantined after a disabled scratch probe is reusable
    # only for the reviewed no-save close plus official workspace reopen path.
    # It is not interchangeable with earlier COM-ready or receipt-only resume.
    $expectedSchema = if ($RequireVisible) { 'controlstudio-mne-resume-after-visible-scratch/v4' } else { 'controlstudio-mne-resume-after-hidden-scratch-launch/v1' }
    $expectedFailure = if ($RequireVisible) { 'mne_probe_quarantined_after_disabled_receipt/v1' } else { 'hidden_scratch_main_window/v1' }
    if ($authorization.schema -cne $expectedSchema -or $authorization.expectedFailure -cne $expectedFailure) {
        throw 'Hidden-scratch resume authorization purpose is invalid'
    }
    $inputs = $authorization.inputs
    if ($null -eq $inputs -or $inputs.specPath -cne $SpecPath -or $inputs.scratchPath -cne $Template.target.project_path -or $inputs.backupPath -cne $Template.scratch.backup_path -or $inputs.markerPath -cne $Template.scratch.marker_path) {
        throw 'Hidden-scratch resume authorization does not bind these exact certification inputs'
    }
    foreach ($item in @(
        @{ Path = $SpecPath; Hash = $inputs.specSha256 },
        @{ Path = [string]$Template.scratch.backup_path; Hash = $inputs.backupSha256 },
        @{ Path = [string]$Template.scratch.marker_path; Hash = $inputs.markerSha256 }
    )) {
        if (-not (Test-Path -LiteralPath $item.Path -PathType Leaf) -or (Get-FileHash -LiteralPath $item.Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne ([string]$item.Hash).ToLowerInvariant()) {
            throw 'Hidden-scratch resume authorization input digest mismatched'
        }
    }
    # XG5000 holds the live scratch exclusively.  Its pre-launch digest is bound
    # by the schema's project_hash and the reviewed failed launch receipt; once
    # the exact still-running process is identified, attempting a second read is
    # neither possible nor safer.
    if ([string]$Template.target.project_hash -cne [string]$inputs.scratchSha256) { throw 'Hidden-scratch resume scratch digest is not bound by the static spec' }
    if ($RequireVisible) {
        $disabled = $authorization.disabledReceipt
        $disabledPath = [IO.Path]::GetFullPath([string]$disabled.path)
        if (-not (Test-Path -LiteralPath $disabledPath -PathType Leaf) -or (Get-FileHash -LiteralPath $disabledPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne ([string]$disabled.sha256).ToLowerInvariant()) {
            throw 'Visible-scratch resume disabled capability receipt digest mismatched'
        }
        $disabledJson = [IO.File]::ReadAllText($disabledPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
        $capability = $disabledJson.receipt
        if (
            $disabledJson.schema -cne 'controlstudio-mne-probe-disabled/v1' -or
            $disabledJson.phase -cne 'capability_probe' -or
            $disabledJson.outcome -cne 'disabled' -or
            $null -eq $capability -or
            $capability.enabled -ne $false -or
            [IO.Path]::GetFullPath([string]$capability.scratch.project_path) -ine [IO.Path]::GetFullPath([string]$Template.target.project_path) -or
            [string]$capability.scratch.project_sha256_before -cne [string]$Template.target.project_hash
        ) {
            throw 'Visible-scratch resume disabled capability receipt is not bound to this exact scratch project'
        }
    } else {
        $prior = $authorization.priorResult
        $priorPath = [System.IO.Path]::GetFullPath([string]$prior.path)
        if (-not (Test-Path -LiteralPath $priorPath -PathType Leaf) -or (Get-FileHash -LiteralPath $priorPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne ([string]$prior.sha256).ToLowerInvariant() -or (Get-Item -LiteralPath $priorPath).LastWriteTimeUtc.ToString('o') -cne [string]$prior.lastWriteTimeUtc) {
            throw 'Hidden-scratch resume prior-result digest or timestamp mismatched'
        }
        $priorJson = [System.IO.File]::ReadAllText($priorPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
        if ($priorJson.status -cne 'error' -or [int]$priorJson.exitCode -ne 1 -or ([string]$priorJson.output).IndexOf([string]$prior.outputFragment, [System.StringComparison]::Ordinal) -lt 0) {
            throw 'Hidden-scratch resume does not bind the reviewed hidden-launch failure'
        }
    }
    $reviewed = $authorization.scratchProcess
    if ($null -eq $reviewed -or [int]$reviewed.pid -le 0 -or [int64]$reviewed.hwnd -le 0) { throw 'Hidden-scratch resume process identity is invalid' }
    if ($RequireVisible) {
        $reviewedKeys = @($reviewed.PSObject.Properties.Name | Sort-Object)
        if (($reviewedKeys -join ',') -cne 'className,hwnd,pid,projectPath,projectSha256,startTimeUtc,title') {
            throw 'Visible-scratch resume process identity fields are invalid'
        }
        if (
            [string]$reviewed.projectPath -cne [string]$Template.target.project_path -or
            [string]$reviewed.projectSha256 -cne [string]$Template.target.project_hash -or
            [int]$capability.outcomes.reopen.source_pid -ne [int]$reviewed.pid -or
            [int64]$capability.outcomes.reopen.source_hwnd -ne [int64]$reviewed.hwnd
        ) {
            throw 'Visible-scratch resume process project evidence differs from the bound scratch project'
        }
        $preservedBaseline = Assert-ExactPreservedXgBaseline -Baseline $authorization.preservedBaseline -ReviewedPid ([int]$reviewed.pid) -RequireReviewedProcess
    } else {
        [string[]]$reviewedArgv = @($reviewed.argv | ForEach-Object { [string]$_ })
        if ($reviewedArgv.Length -notin @(2,3) -or $reviewedArgv[0] -cne [string]$reviewed.executablePath -or $reviewedArgv[$reviewedArgv.Length - 1] -cne [string]$Template.target.project_path -or ($reviewedArgv.Length -eq 3 -and $reviewedArgv[1] -cne '/Automation')) {
            throw 'Hidden-scratch resume authorization argv is not the exact reviewed executable and scratch path'
        }
        $allXg = @(Get-Process -Name XG5000 -ErrorAction SilentlyContinue)
        if ($allXg.Count -ne 1 -or $allXg[0].Id -ne [int]$reviewed.pid) { throw 'Hidden-scratch resume requires exactly the reviewed global XG5000 PID' }
        $preservedBaseline = @()
    }
    $process = Get-Process -Id ([int]$reviewed.pid) -ErrorAction Stop
    if ($process.StartTime.ToUniversalTime().ToString('o') -cne [string]$reviewed.startTimeUtc) { throw 'Hidden-scratch resume detected XG5000 PID reuse' }
    if (-not $RequireVisible) {
        $record = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$reviewed.pid) | Select-Object -First 1
        if ($null -eq $record -or [string]$record.ExecutablePath -cne [string]$reviewed.executablePath) {
            throw 'Hidden-scratch resume process executable mismatched'
        }
        Initialize-ReviewedModalNativeApi
        $expectedArgv = $reviewedArgv
        [string[]]$actualArgv = [ControlStudioMneModalNative]::ParseCommandLine([string]$record.CommandLine)
        if ($expectedArgv.Length -eq 0 -or $actualArgv.Length -ne $expectedArgv.Length) {
            throw "Hidden-scratch resume process argv length mismatched; actualCount=$($actualArgv.Length); actualDigest=$(Get-ArgvDigest -Arguments $actualArgv); expectedCount=$($expectedArgv.Length); expectedDigest=$(Get-ArgvDigest -Arguments $expectedArgv)"
        }
        for ($argumentIndex = 0; $argumentIndex -lt $expectedArgv.Length; $argumentIndex++) {
            if ($actualArgv[$argumentIndex] -cne $expectedArgv[$argumentIndex]) {
                throw "Hidden-scratch resume process argv mismatched; actualDigest=$(Get-ArgvDigest -Arguments $actualArgv); expectedDigest=$(Get-ArgvDigest -Arguments $expectedArgv)"
            }
        }
    }
    Initialize-ReviewedModalNativeApi
    $snapshots = @([ControlStudioMneModalNative]::FindTopLevelWindows([uint32]$reviewed.pid))
    $matches = @($snapshots | Where-Object {
        if ($RequireVisible) {
            [int64]$_.Handle -eq [int64]$reviewed.hwnd -and [string]$_.Title -ceq [string]$reviewed.title -and [string]$_.ClassName -ceq [string]$reviewed.className -and [ControlStudioMneModalNative]::IsVisibleAndEnabled([int64]$_.Handle)
        } else {
            Test-ExactHiddenScratchWindow -Snapshot $_ -Reviewed $reviewed
        }
    })
    if ($matches.Count -ne 1) {
        $matchFailure = if ($RequireVisible) { 'Visible-scratch resume did not find exactly one reviewed visible XG5000 main window' } else { 'Hidden-scratch resume did not find exactly one reviewed hidden XG5000 main window' }
        throw $matchFailure
    }
    return [pscustomobject]@{ Process = $process; Reviewed = $reviewed; PreservedBaseline = @($preservedBaseline); ExpectedWorkingHash = [string]$inputs.scratchSha256 }
}

function Assert-ResumeAfterReviewedVisibleScratch {
    param(
        [Parameter(Mandatory)][string]$AuthorizationPath,
        [Parameter(Mandatory)][string]$SpecPath,
        [Parameter(Mandatory)]$Template,
        [Parameter(Mandatory)][string]$BundlePath
    )
    return Assert-ResumeAfterHiddenScratchLaunch -AuthorizationPath $AuthorizationPath -SpecPath $SpecPath -Template $Template -BundlePath $BundlePath -RequireVisible
}

function Restore-ReviewedHiddenScratchWindow {
    param([Parameter(Mandatory)]$Resume)

    [ControlStudioMneModalNative]::RestoreWindow([int64]$Resume.Reviewed.hwnd)
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $Resume.Process.Refresh()
        if ($Resume.Process.HasExited) { throw 'Reviewed hidden scratch XG5000 exited during restore' }
        $snapshots = @([ControlStudioMneModalNative]::FindTopLevelWindows([uint32]$Resume.Process.Id))
        $visible = @($snapshots | Where-Object {
            [int64]$_.Handle -eq [int64]$Resume.Reviewed.hwnd -and
            [string]$_.Title -ceq [string]$Resume.Reviewed.title -and
            [string]$_.ClassName -ceq [string]$Resume.Reviewed.className -and
            [ControlStudioMneModalNative]::IsVisibleAndEnabled([int64]$_.Handle)
        })
        if ($visible.Count -eq 1) {
            return [pscustomobject]@{ Process = $Resume.Process; Hwnd = [int64]$Resume.Reviewed.hwnd }
        }
        Start-Sleep -Milliseconds 250
    }
    throw 'Reviewed hidden scratch XG5000 main window did not become uniquely visible after restore'
}

function Open-ExactWorkspaceWorkingCopy {
    param(
        [Parameter(Mandatory)][string]$WorkspaceId,
        [Parameter(Mandatory)][string]$ExpectedHash,
        [Parameter(Mandatory)][string]$WorkspaceRoot,
        [Parameter(Mandatory)][string]$PythonExecutable
    )

    $openCode = @'
import json, sys, time
from pathlib import Path
from xg5000_control.project_launcher import open_workspace_in_xg5000
from xg5000_control.project.catalog import _mdi_client, _project_grid_candidates
workspace_id, expected_hash, workspace_root = sys.argv[1:]
result = open_workspace_in_xg5000(
    workspace_id, expected_hash, workspace_root=Path(workspace_root), timeout_seconds=60.0,
    automation=False, allow_official_open_fallback=True
)
instance = result.get('instance') or {}
pid, hwnd = int(instance.get('pid') or 0), int(instance.get('hwnd') or 0)
deadline = time.monotonic() + 30.0
while time.monotonic() < deadline:
    if pid > 0 and hwnd > 0 and len(_project_grid_candidates(hwnd, _mdi_client(hwnd))) == 1:
        break
    time.sleep(0.2)
else:
    raise RuntimeError('official visible workspace launch did not reach one visible Project grid before timeout')
print(json.dumps(result, ensure_ascii=False, sort_keys=True))
'@
    $saved = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $raw = & $PythonExecutable -c $openCode $WorkspaceId $ExpectedHash $WorkspaceRoot 2>&1
        $exit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $saved }
    if ($exit -ne 0) { throw "Official workspace open failed with exit code ${exit}: $($raw | Out-String)" }
    $json = @($raw | Where-Object { $_ -match '^\{' } | Select-Object -Last 1)
    if ($json.Count -ne 1) { throw 'Official workspace open returned no canonical result' }
    return ($json[0] | ConvertFrom-Json)
}

function Test-ExactOpenedWorkspaceBinding {
    param(
        [Parameter(Mandatory)]$Opened,
        [Parameter(Mandatory)][string]$ExpectedWorkspaceId,
        [Parameter(Mandatory)][string]$ExpectedWorkingPath,
        [Parameter(Mandatory)][string]$ExpectedWorkingHash
    )

    $instance = $Opened.instance
    if (
        $Opened.verified -ne $true -or
        [string]$Opened.workspace_id -cne $ExpectedWorkspaceId -or
        [string]$Opened.working_hash -cne $ExpectedWorkingHash -or
        $Opened.process_is_independent -ne $true -or
        $Opened.project_identity_read_only_bound_to_selected_pid -ne $true -or
        $null -eq $instance -or [int]$instance.pid -le 0 -or [int64]$instance.hwnd -le 0
    ) { return $false }
    try {
        $expectedPath = [IO.Path]::GetFullPath($ExpectedWorkingPath)
        $actualPath = [IO.Path]::GetFullPath([string]$Opened.working_path)
    } catch { return $false }
    if (-not [string]::Equals($actualPath, $expectedPath, [StringComparison]::OrdinalIgnoreCase)) {
        return $false
    }

    $nativeProof = (
        $Opened.native_file_usage_verified -eq $true -and
        $Opened.official_open_binding_verified -ne $true -and
        [string]$Opened.identity_assurance -ceq 'controlled_launch_native_file_read_only'
    )
    $receipt = $Opened.open_ui_fallback
    $officialProof = $false
    if ($null -ne $receipt) {
        try {
            $receiptPath = [IO.Path]::GetFullPath([string]$receipt.project_path)
            $officialProof = (
                $Opened.official_open_binding_verified -eq $true -and
                $Opened.native_file_usage_verified -ne $true -and
                [string]$Opened.identity_assurance -ceq 'controlled_launch_official_open_read_only' -and
                [string]$receipt.status -ceq 'passed' -and
                $receipt.dialog_closed -eq $true -and
                [string]::Equals($receiptPath, $expectedPath, [StringComparison]::OrdinalIgnoreCase)
            )
        } catch { $officialProof = $false }
    }
    return ($nativeProof -xor $officialProof)
}

function Write-JsonAtomically {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Value)
    $temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 20), (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Assert-ResumeAfterReviewedNoXgWorkspaceLaunch {
    param([Parameter(Mandatory)][string]$AuthorizationPath,[Parameter(Mandatory)][string]$SpecPath,[Parameter(Mandatory)]$Template,[Parameter(Mandatory)][string]$BundlePath,[Parameter(Mandatory)][string]$WorkspaceRoot,[Parameter(Mandatory)][string]$PythonExecutable)
    if ($null -ne (Get-Process -Name XG5000 -ErrorAction SilentlyContinue) -or $null -ne (Get-DevelopmentListenerPid) -or (Test-Path -LiteralPath $BundlePath)) { throw 'No-XG workspace launch preconditions are not clean' }
    $auth = [IO.File]::ReadAllText([IO.Path]::GetFullPath($AuthorizationPath),[Text.Encoding]::UTF8)|ConvertFrom-Json
    if ($auth.schema -cne 'controlstudio-mne-resume-after-no-xg-workspace-launch/v1' -or $auth.expectedFailure -cne 'official_workspace_open_title_mismatch_cleanup/v1') { throw 'No-XG workspace launch authorization purpose is invalid' }
    $priorPath=[IO.Path]::GetFullPath([string]$auth.priorResult.path)
    if ((Get-FileHash $priorPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne ([string]$auth.priorResult.sha256).ToLowerInvariant() -or (Get-Item $priorPath).LastWriteTimeUtc.ToString('o') -cne [string]$auth.priorResult.lastWriteTimeUtc) { throw 'No-XG workspace launch prior manifest mismatched' }
    foreach($s in @(@{n='stdout';p=$auth.priorResult.stdoutPath;h=$auth.priorResult.stdoutSha256},@{n='stderr';p=$auth.priorResult.stderrPath;h=$auth.priorResult.stderrSha256})) { $p=[IO.Path]::GetFullPath([string]$s.p); if((Split-Path -Parent $p) -cne (Split-Path -Parent $priorPath) -or (Get-FileHash $p -Algorithm SHA256).Hash.ToLowerInvariant() -cne ([string]$s.h).ToLowerInvariant()){throw 'No-XG workspace launch prior output mismatched'} }
    if ($auth.inputs.specPath -cne $SpecPath -or $auth.inputs.scratchPath -cne $Template.target.project_path -or [string]$auth.inputs.scratchSha256 -cne [string]$Template.target.project_hash) { throw 'No-XG workspace launch inputs mismatched' }
    $code='import json,sys; from xg5000_control.workspaces import workspace_status; s=workspace_status(sys.argv[1]); print(json.dumps(s,ensure_ascii=False))'
    $saved=$ErrorActionPreference;try{$ErrorActionPreference='Continue';$raw=& $PythonExecutable -c $code ([string]$auth.workspace.workspaceId) 2>&1;$exit=$LASTEXITCODE}finally{$ErrorActionPreference=$saved};if($exit -ne 0){throw 'No-XG workspace status failed'}
    $status=(@($raw|Where-Object{$_ -match '^\{'}|Select-Object -Last 1)[0]|ConvertFrom-Json)
    if($status.verified -ne $true -or $status.workspace_id -cne [string]$auth.workspace.workspaceId -or $status.revision_id -cne [string]$auth.workspace.revisionId -or $status.working_copy.state -cne 'clean' -or $status.working_copy.current_sha256 -cne [string]$auth.workspace.workingSha256 -or ([IO.Path]::GetFileName([string]$status.working_copy.path)) -cne [string]$auth.workspace.workingName){throw 'No-XG workspace status identity mismatched'}
    return $status
}

function Test-ExactHeadlessXgCleanupAuthorizationShape {
    param([Parameter(Mandatory)]$Authorization)

    $expected = @('schema','attempt','inputs','scratchProcess','noXgAuthorization','expectedFailure')
    if ((@($Authorization.PSObject.Properties.Name | Sort-Object) -join ',') -cne ((@($expected | Sort-Object) -join ','))) { return $false }
    return (
        $Authorization.schema -ceq 'controlstudio-mne-cleanup-headless-owned-xg/v1' -and
        $Authorization.expectedFailure -ceq 'same_com_active_object_not_found_headless_owned_xg/v1' -and
        $null -ne $Authorization.attempt -and
        $null -ne $Authorization.inputs -and $null -ne $Authorization.scratchProcess -and
        $null -ne $Authorization.noXgAuthorization
    )
}

function Assert-CleanupReviewedHeadlessXgThenNoXgWorkspaceLaunch {
    param(
        [Parameter(Mandatory)][string]$AuthorizationPath,
        [Parameter(Mandatory)][string]$SpecPath,
        [Parameter(Mandatory)]$Template,
        [Parameter(Mandatory)][string]$BundlePath
    )

    if ($null -ne (Get-DevelopmentListenerPid) -or (Test-Path -LiteralPath $BundlePath)) { throw 'Headless cleanup requires port 8767 and capability bundle to remain absent' }
    $exactPath = [IO.Path]::GetFullPath($AuthorizationPath)
    if (-not (Test-Path -LiteralPath $exactPath -PathType Leaf)) { throw 'Headless cleanup authorization is not a file' }
    $authorization = [IO.File]::ReadAllText($exactPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if (-not (Test-ExactHeadlessXgCleanupAuthorizationShape -Authorization $authorization)) { throw 'Headless cleanup authorization fields or purpose are invalid' }
    $inputs = $authorization.inputs
    if ($inputs.specPath -cne $SpecPath -or $inputs.scratchPath -cne $Template.target.project_path -or $inputs.backupPath -cne $Template.scratch.backup_path -or $inputs.markerPath -cne $Template.scratch.marker_path) { throw 'Headless cleanup authorization inputs differ' }
    foreach ($item in @(
        @{ Path = $SpecPath; Hash = $inputs.specSha256 },
        @{ Path = [string]$Template.scratch.backup_path; Hash = $inputs.backupSha256 },
        @{ Path = [string]$Template.scratch.marker_path; Hash = $inputs.markerSha256 }
    )) {
        if (-not (Test-Path -LiteralPath $item.Path -PathType Leaf) -or (Get-FileHash -LiteralPath $item.Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne ([string]$item.Hash).ToLowerInvariant()) { throw 'Headless cleanup input digest mismatched' }
    }
    $manifestPath = [IO.Path]::GetFullPath([string]$inputs.workspaceManifestPath)
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -or (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne ([string]$inputs.workspaceManifestSha256).ToLowerInvariant()) { throw 'Headless cleanup workspace manifest digest mismatched' }
    $manifest = [IO.File]::ReadAllText($manifestPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    $relativeWorking = [IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $manifestPath) ([string]$manifest.working_copy.relative_path)))
    if ($manifest.schema_version -cne '1' -or [string]$manifest.workspace_id -cne [string]$Template.target.workspace_id -or $relativeWorking -cne [string]$Template.target.project_path -or [string]$manifest.source.sha256 -cne [string]$inputs.scratchSha256 -or [string]$manifest.working_copy.baseline_sha256 -cne [string]$inputs.scratchSha256 -or [string]$manifest.source.sha256 -cne [string]$manifest.working_copy.baseline_sha256) { throw 'Headless cleanup workspace manifest is not the exact clean baseline' }
    foreach ($artifact in @(
        @{ Value = $authorization.attempt; Label = 'attempt' },
        @{ Value = $authorization.noXgAuthorization; Label = 'no-XG authorization' }
    )) {
        $value = $artifact.Value
        $path = [IO.Path]::GetFullPath([string]$value.path)
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne ([string]$value.sha256).ToLowerInvariant()) { throw "Headless cleanup $($artifact.Label) digest mismatched" }
        if ($null -ne $value.lastWriteTimeUtc -and (Get-Item -LiteralPath $path).LastWriteTimeUtc.ToString('o') -cne [string]$value.lastWriteTimeUtc) { throw "Headless cleanup $($artifact.Label) timestamp mismatched" }
    }
    $attemptPath = [IO.Path]::GetFullPath([string]$authorization.attempt.path)
    $attempt = [IO.File]::ReadAllText($attemptPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($attempt.state -cne 'failed' -or [int]$attempt.exitCode -ne 1) { throw 'Headless cleanup attempt is not the reviewed failed no-XG launch' }
    foreach ($stream in @(
        @{ Name = 'stdout'; Path = $authorization.attempt.stdoutPath; Hash = $authorization.attempt.stdoutSha256 },
        @{ Name = 'stderr'; Path = $authorization.attempt.stderrPath; Hash = $authorization.attempt.stderrSha256 }
    )) {
        $path = [IO.Path]::GetFullPath([string]$stream.Path)
        if ([string]$attempt.($stream.Name) -cne $path -or (Split-Path -Parent $path) -cne (Split-Path -Parent $attemptPath) -or -not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne ([string]$stream.Hash).ToLowerInvariant()) { throw "Headless cleanup attempt $($stream.Name) binding mismatched" }
    }
    $reviewed = $authorization.scratchProcess
    [string[]]$expectedArgv = @($reviewed.argv | ForEach-Object { [string]$_ })
    $hiddenMain = $reviewed.hiddenMain
    if ($null -eq $hiddenMain -or [int]$reviewed.pid -le 0 -or [int64]$reviewed.hwnd -ne 0 -or [int]$reviewed.visibleTopLevelCount -ne 0 -or [int64]$hiddenMain.hwnd -le 0 -or [string]::IsNullOrWhiteSpace([string]$hiddenMain.title) -or [string]::IsNullOrWhiteSpace([string]$hiddenMain.className) -or $expectedArgv.Length -ne 3 -or $expectedArgv[0] -cne [string]$reviewed.executablePath -or $expectedArgv[1] -cne '/Automation' -or $expectedArgv[2] -cne [string]$Template.target.project_path -or ([string]$hiddenMain.title).IndexOf([IO.Path]::GetFileNameWithoutExtension([string]$Template.target.project_path), [System.StringComparison]::Ordinal) -lt 0) { throw 'Headless cleanup reviewed process identity is invalid' }
    $allXg = @(Get-Process -Name XG5000 -ErrorAction SilentlyContinue)
    if ($allXg.Count -ne 1 -or $allXg[0].Id -ne [int]$reviewed.pid) { throw 'Headless cleanup requires exactly one reviewed global XG5000 process' }
    $process = Get-Process -Id ([int]$reviewed.pid) -ErrorAction Stop
    if ($process.StartTime.ToUniversalTime().ToString('o') -cne [string]$reviewed.startTimeUtc -or [int64]$process.MainWindowHandle -ne 0 -or [string]$process.MainWindowTitle -cne '') { throw 'Headless cleanup detected PID reuse or a surfaced XG5000 window' }
    $record = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$reviewed.pid) | Select-Object -First 1
    if ($null -eq $record -or [string]$record.ExecutablePath -cne [string]$reviewed.executablePath) { throw 'Headless cleanup process executable mismatched' }
    Initialize-ReviewedModalNativeApi
    [string[]]$actualArgv = [ControlStudioMneModalNative]::ParseCommandLine([string]$record.CommandLine)
    if ($actualArgv.Length -ne $expectedArgv.Length) { throw 'Headless cleanup WMI argv length mismatched' }
    for ($index = 0; $index -lt $expectedArgv.Length; $index++) { if ($actualArgv[$index] -cne $expectedArgv[$index]) { throw "Headless cleanup WMI argv mismatched; actualDigest=$(Get-ArgvDigest -Arguments $actualArgv); expectedDigest=$(Get-ArgvDigest -Arguments $expectedArgv)" } }
    $topLevel = @([ControlStudioMneModalNative]::FindTopLevelWindows([uint32]$reviewed.pid))
    if (@($topLevel | Where-Object { [ControlStudioMneModalNative]::IsVisible([int64]$_.Handle) }).Count -ne [int]$reviewed.visibleTopLevelCount) { throw 'Headless cleanup visible top-level window count changed' }
    if (@($topLevel | Where-Object { [int64]$_.Handle -eq [int64]$hiddenMain.hwnd -and [string]$_.Title -ceq [string]$hiddenMain.title -and [string]$_.ClassName -ceq [string]$hiddenMain.className -and -not [ControlStudioMneModalNative]::IsVisible([int64]$_.Handle) }).Count -ne 1) { throw 'Headless cleanup reviewed hidden main window mismatched' }
    return [pscustomobject]@{ Process = $process; Reviewed = $reviewed; ExpectedWorkingHash = [string]$inputs.scratchSha256 }
}

function Stop-ExactReviewedHeadlessXg {
    param([Parameter(Mandatory)]$Reviewed)

    $process = $Reviewed.Process
    $identity = $Reviewed.Reviewed
    $process.Refresh()
    $hiddenMain = $identity.hiddenMain
    $topLevel = @([ControlStudioMneModalNative]::FindTopLevelWindows([uint32]$process.Id))
    if ($process.HasExited -or $process.StartTime.ToUniversalTime().ToString('o') -cne [string]$identity.startTimeUtc -or [int64]$process.MainWindowHandle -ne 0 -or [string]$process.MainWindowTitle -cne '' -or @($topLevel | Where-Object { [ControlStudioMneModalNative]::IsVisible([int64]$_.Handle) }).Count -ne [int]$identity.visibleTopLevelCount -or @($topLevel | Where-Object { [int64]$_.Handle -eq [int64]$hiddenMain.hwnd -and [string]$_.Title -ceq [string]$hiddenMain.title -and [string]$_.ClassName -ceq [string]$hiddenMain.className -and -not [ControlStudioMneModalNative]::IsVisible([int64]$_.Handle) }).Count -ne 1) { throw 'Headless cleanup process changed before exact termination' }
    Stop-Process -Id $process.Id -Force -ErrorAction Stop
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        if ($null -eq (Get-Process -Name XG5000 -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Milliseconds 250
    }
    throw 'Reviewed headless XG5000 did not exit after exact termination'
}

function Assert-WorkingHashAfterExactHeadlessCleanup {
    param([Parameter(Mandatory)]$Template,[Parameter(Mandatory)][string]$ExpectedHash)

    $path = [string]$Template.target.project_path
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Working project is unavailable after exact headless cleanup' }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -cne $ExpectedHash.ToLowerInvariant()) { throw 'Working project hash differs after exact headless cleanup; refusing new XG5000 launch' }
}

if ($SelfTest) {
    Initialize-ReviewedModalNativeApi
    $valid = [pscustomobject]@{
        Parent = [int64]1575014
        Title = 'XG5000'
        ClassName = '#32770'
        ChildTexts = @("프로젝트창이 닫힙니다.`r`n저장하시겠습니까?")
    }
    $wrongText = [pscustomobject]@{
        Parent = [int64]1575014
        Title = 'XG5000'
        ClassName = '#32770'
        ChildTexts = @('다른 확인 창')
    }
    $wrongClass = [pscustomobject]@{
        Parent = [int64]1575014
        Title = 'XG5000'
        ClassName = 'OtherClass'
        ChildTexts = @("프로젝트창이 닫힙니다.`r`n저장하시겠습니까?")
    }
    $shadow = [pscustomobject]@{
        Parent = [int64]1575014
        Title = ''
        ClassName = 'BCGPShadowSideWnd'
        ChildTexts = @()
        ChildControlIds = @()
    }
    $hiddenReviewed = [pscustomobject]@{
        hwnd = [int64]3410170
        title = 'UI 레더 수직검증 20260817 - XG5000'
        className = 'Afx:00400000:8:00010003:00000000:0016083D'
    }
    $wrongHidden = [pscustomobject]@{
        hwnd = [int64]3410171
        title = 'UI 레더 수직검증 20260817 - XG5000'
        className = 'Afx:00400000:8:00010003:00000000:0016083D'
    }
    if (
        -not (Test-ExactNoSaveModalShape -Candidate $valid -ExpectedMainHwnd 1575014) -or
        (Test-ExactNoSaveModalShape -Candidate $wrongText -ExpectedMainHwnd 1575014) -or
        (Test-ExactNoSaveModalShape -Candidate $wrongClass -ExpectedMainHwnd 1575014)
    ) {
        throw 'Exact no-save modal helper self-test failed'
    }
    $withShadows = @(Get-ActionableOwnedWindows -Candidates @($shadow, $shadow, $shadow, $shadow, $valid))
    $shadowsOnly = @(Get-ActionableOwnedWindows -Candidates @($shadow, $shadow, $shadow, $shadow))
    if ($withShadows.Count -ne 1 -or -not (Test-ExactNoSaveModalShape -Candidate $withShadows[0] -ExpectedMainHwnd 1575014) -or $shadowsOnly.Count -ne 0) {
        throw 'Decorative shadow modal classifier self-test failed'
    }
    # The predicate's visibility check is exercised against only an invalid
    # HWND here; a live hidden XG window is never touched by self-test.
    if ((Test-ExactHiddenScratchWindow -Snapshot $wrongHidden -Reviewed $hiddenReviewed)) {
        throw 'Hidden scratch identity helper self-test failed'
    }
    [string[]]$parsedArgv = [ControlStudioMneModalNative]::ParseCommandLine('"C:\XG5000\XG5000.exe" /Automation "C:\scratch path\scratch.xgwx"')
    if ($parsedArgv.Length -ne 3 -or $parsedArgv[0] -cne 'C:\XG5000\XG5000.exe' -or $parsedArgv[1] -cne '/Automation' -or $parsedArgv[2] -cne 'C:\scratch path\scratch.xgwx') {
        throw 'Command-line argv parser self-test failed'
    }
    $badHeadlessAuthorization = '{"schema":"controlstudio-mne-cleanup-headless-owned-xg/v1","attempt":{},"inputs":{},"scratchProcess":{},"noXgAuthorization":{},"expectedFailure":"wrong"}' | ConvertFrom-Json
    if (Test-ExactHeadlessXgCleanupAuthorizationShape -Authorization $badHeadlessAuthorization) {
        throw 'Headless cleanup authorization negative self-test failed'
    }
    $badFailedElevatedAuthorization = '{"schema":"controlstudio-mne-resume-after-failed-elevated-launch/v1","prior":{},"inputs":{},"state":{},"expectedFailure":"wrong"}' | ConvertFrom-Json
    if (Test-FailedElevatedLaunchAuthorizationShape -Authorization $badFailedElevatedAuthorization) {
        throw 'Failed elevated-launch authorization negative self-test failed'
    }
    $bindingPath = 'C:\scratch\working.xgwx'
    $bindingHash = ('a' * 64) -join ''
    $bindingInstance = [pscustomobject]@{ pid = 1; hwnd = [int64]2 }
    $officialBinding = [pscustomobject]@{
        verified = $true; workspace_id = 'workspace'; working_path = $bindingPath; working_hash = $bindingHash
        process_is_independent = $true; project_identity_read_only_bound_to_selected_pid = $true
        native_file_usage_verified = $false; official_open_binding_verified = $true
        identity_assurance = 'controlled_launch_official_open_read_only'; instance = $bindingInstance
        open_ui_fallback = [pscustomobject]@{ status = 'passed'; dialog_closed = $true; project_path = $bindingPath }
    }
    $nativeBinding = [pscustomobject]@{
        verified = $true; workspace_id = 'workspace'; working_path = $bindingPath; working_hash = $bindingHash
        process_is_independent = $true; project_identity_read_only_bound_to_selected_pid = $true
        native_file_usage_verified = $true; official_open_binding_verified = $false
        identity_assurance = 'controlled_launch_native_file_read_only'; instance = $bindingInstance; open_ui_fallback = $null
    }
    if (
        -not (Test-ExactOpenedWorkspaceBinding -Opened $officialBinding -ExpectedWorkspaceId 'workspace' -ExpectedWorkingPath $bindingPath -ExpectedWorkingHash $bindingHash) -or
        -not (Test-ExactOpenedWorkspaceBinding -Opened $nativeBinding -ExpectedWorkspaceId 'workspace' -ExpectedWorkingPath $bindingPath -ExpectedWorkingHash $bindingHash)
    ) { throw 'Exact workspace binding proof positive self-test failed' }
    $officialBinding.open_ui_fallback.dialog_closed = $false
    if (Test-ExactOpenedWorkspaceBinding -Opened $officialBinding -ExpectedWorkspaceId 'workspace' -ExpectedWorkingPath $bindingPath -ExpectedWorkingHash $bindingHash) {
        throw 'Exact workspace binding proof accepted an unclosed official Open receipt'
    }
    'Exact no-save modal helper self-test: OK'
    exit 0
}

foreach ($entry in @(
    @{ Name = 'MNE certification executable'; Path = $certifier },
    @{ Name = 'MCP Python'; Path = $mcpPython },
    @{ Name = 'Host Python'; Path = $hostPython },
    @{ Name = 'PLC MCP sidecar'; Path = $mcp },
    @{ Name = 'XG5000 executable'; Path = $XgExecutable }
)) {
    if (-not (Test-Path -LiteralPath $entry.Path -PathType Leaf)) {
        throw "$($entry.Name) is missing: $($entry.Path)"
    }
}

$specPath = [System.IO.Path]::GetFullPath($Spec)
$bundlePath = [System.IO.Path]::GetFullPath($Bundle)
$dataRoot = [System.IO.Path]::GetFullPath($LocalAppData)
if (-not (Test-Path -LiteralPath $specPath -PathType Leaf)) { throw "Certification spec is not a file: $specPath" }
if (-not (Test-Path -LiteralPath $dataRoot -PathType Container)) { throw "LOCALAPPDATA root is not a directory: $dataRoot" }
if (Test-Path -LiteralPath $bundlePath) { throw "Bundle output already exists: $bundlePath" }
$launchTokenPath = Join-Path $dataRoot 'ControlStudio Development\session.token'
if (-not (Test-Path -LiteralPath $launchTokenPath -PathType Leaf)) { throw "Development launch token is missing: $launchTokenPath" }

$template = [System.IO.File]::ReadAllText(
    $specPath,
    [System.Text.Encoding]::UTF8
) | ConvertFrom-Json
if (-not (Test-Path -LiteralPath $template.target.project_path -PathType Leaf)) {
    throw 'Certification template does not name an existing scratch project'
}
$validateSpecShape = @'
import sys
from xg5000_control.mne_capability_cli import _load_spec

_load_spec(sys.argv[1])
'@
& $mcpPython -c $validateSpecShape $specPath
if ($LASTEXITCODE -ne 0) {
    throw 'Certification template failed the CLI JSON shape preflight'
}
$validateSpecAfterClose = @'
import json
import sys
from pathlib import Path
from xg5000_control.mne_capability_cli import _normalize_spec

_normalize_spec(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
'@

if ($RecoverOwnedHeadlessPid -gt 0 -or $RecoverOwnedHeadlessPreflightOnly) {
    $reviewedHeadless = Assert-ExactOwnedHeadlessCertificationFailure -Template $template -ExpectedPid $RecoverOwnedHeadlessPid -ExpectedStartedUtc $RecoverOwnedHeadlessStartedUtc -ExpectedExecutable $XgExecutable -PythonExecutable $mcpPython
    if ($RecoverOwnedHeadlessPreflightOnly) {
        [pscustomobject]@{
            schema = 'controlstudio-mne-owned-headless-recovery/v1'
            pid = [int]$reviewedHeadless.Process.Id
            processStartedUtc = $reviewedHeadless.Process.StartTime.ToUniversalTime().ToString('o')
            projectPath = $reviewedHeadless.ProjectPath
            projectHash = $reviewedHeadless.ProjectHash
            result = 'ready_to_terminate_only_the_exact_headless_certification_process'
        } | ConvertTo-Json -Compress
        exit 0
    }
    Stop-ExactOwnedHeadlessCertificationFailure -Reviewed $reviewedHeadless
    [pscustomobject]@{
        schema = 'controlstudio-mne-owned-headless-recovery/v1'
        pid = [int]$reviewedHeadless.Process.Id
        projectPath = $reviewedHeadless.ProjectPath
        projectHash = $reviewedHeadless.ProjectHash
        hostRestart = 'not-attempted; a new authorization secret is required to restart against a signed capability bundle'
        result = 'exact_headless_certification_process_terminated'
    } | ConvertTo-Json -Compress
    exit 0
}

$resumedFailedElevatedLaunch = $null
if ($ResumeAfterFailedElevatedLaunch) {
    if ([string]::IsNullOrWhiteSpace($FailedElevatedLaunchAuthorization)) { throw 'Failed-elevated-launch resume requires an explicit authorization file' }
    $failedElevated = Assert-ResumeAfterFailedElevatedLaunch -AuthorizationPath $FailedElevatedLaunchAuthorization -SpecPath $specPath -Template $template -BundlePath $bundlePath -PythonExecutable $mcpPython
    & $mcpPython -c $validateSpecAfterClose $specPath
    if ($LASTEXITCODE -ne 0) { throw 'Failed-elevated-launch resume template failed the CLI disposable-proof preflight' }
    if ($FailedElevatedLaunchPreflightOnly) {
        [pscustomobject]@{
            schema = 'controlstudio-mne-failed-elevated-launch-resume-preflight/v1'
            workspaceId = $failedElevated.WorkspaceId
            projectPath = $failedElevated.ProjectPath
            projectHash = $failedElevated.ProjectHash
            xg5000PidsBeforeLaunch = @($failedElevated.Xg5000PidsBeforeLaunch)
            result = 'ready_to_launch_exact_workspace_through_independent_cli'
        } | ConvertTo-Json -Compress
        exit 0
    }
    $env:LOCALAPPDATA = $dataRoot
    $env:CONTROLSTUDIO_RELEASE_CHANNEL = 'development'
    $workspaceRoot = Join-Path $dataRoot 'ControlStudio Development\workspaces'
    $opened = Open-ExactWorkspaceWorkingCopy -WorkspaceId $failedElevated.WorkspaceId -ExpectedHash $failedElevated.ProjectHash -WorkspaceRoot $workspaceRoot -PythonExecutable $mcpPython
    $instance = $opened.instance
    if (-not (Test-ExactOpenedWorkspaceBinding -Opened $opened -ExpectedWorkspaceId $failedElevated.WorkspaceId -ExpectedWorkingPath $failedElevated.ProjectPath -ExpectedWorkingHash $failedElevated.ProjectHash)) {
        throw 'Failed-elevated-launch resume did not return the exact verified working-copy instance'
    }
    if (
        [int]$opened.launch_pid -ne [int]$instance.pid -or
        @($failedElevated.Xg5000PidsBeforeLaunch) -contains [int]$instance.pid
    ) { throw 'Failed-elevated-launch resume returned a baseline or mismatched XG5000 process' }
    $template.target.pid = [int]$instance.pid
    $template.target.hwnd = [int64]$instance.hwnd
    $resumedFailedElevatedLaunch = [pscustomobject]@{ Process = (Get-Process -Id ([int]$instance.pid) -ErrorAction Stop); Hwnd = [int64]$instance.hwnd }
}

if ($VisibleTargetPreflightOnly) {
    if ((Get-DevelopmentListenerPid) -ne $ExpectedHostListenerPid) {
        throw 'Port 8767 is not owned by the reviewed listener PID'
    }
    $reviewedTarget = Assert-ExactVisibleCertificationTarget -Template $template -ExpectedPid $ExpectedXgPid -ExpectedExecutable $XgExecutable
    [pscustomobject]@{
        schema = 'controlstudio-mne-visible-target-preflight/v1'
        pid = [int]$reviewedTarget.Process.Id
        hwnd = [int64]$reviewedTarget.Process.MainWindowHandle
        processStartedUtc = $reviewedTarget.ProcessStartedUtc.ToString('o')
        xg5000PidsBeforeClose = @($reviewedTarget.BaselinePids)
        projectPath = $reviewedTarget.ProjectPath
        projectHash = $reviewedTarget.ProjectHash
        result = 'ready_to_close_only_the_reviewed_target'
    } | ConvertTo-Json -Compress
    exit 0
}

$selectedResumeModes = @($ResumeAfterReviewedXgClose, $ResumeAfterHiddenScratchLaunch, $ResumeAfterReviewedVisibleScratch, $ResumeAfterFailedElevatedLaunch) | Where-Object { $_ }
if (@($selectedResumeModes).Count -gt 1) {
    throw 'Only one reviewed resume mode may be selected'
}
if ($ResumeAfterFailedElevatedLaunch) {
    # The authorization-bound preflight above already established the absent
    # listener/owned-target state and opened one exact new workspace instance.
} elseif ($ResumeAfterReviewedXgClose) {
    if ([string]::IsNullOrWhiteSpace($ResumeAuthorization)) { throw 'Resume requires an explicit authorization file' }
    $resumeHost = Assert-ResumeAfterReviewedXgClose -AuthorizationPath $ResumeAuthorization -SpecPath $specPath -Template $template -BundlePath $bundlePath
    if ($ExpectedHostListenerPid -ne [int]$resumeHost.pid -or $ExpectedXgPid -ne [int]$resumeHost.closedXgPid) {
        throw 'Resume command identifiers do not match its reviewed authorization'
    }
    if ($ResumePreflightOnly) {
        [pscustomobject]@{
            schema = 'controlstudio-mne-resume-preflight/v1'
            listenerPid = [int]$resumeHost.pid
            listenerStartTimeUtc = [string]$resumeHost.startTimeUtc
            closedXgPid = [int]$resumeHost.closedXgPid
            result = 'ready_to_stop_exact_host_tree'
        } | ConvertTo-Json -Compress
        exit 0
    }
    if ($ResumeExecutionPathSelfTest) {
        $resolvedStop = Get-Command Stop-ExactHostTreeAfterReviewedXgClose -CommandType Function -ErrorAction Stop
        $stopReadiness = Stop-ExactHostTreeAfterReviewedXgClose -ListenerPid ([int]$resumeHost.pid) -ListenerStartTimeUtc ([string]$resumeHost.startTimeUtc) -ClosedXgPid ([int]$resumeHost.closedXgPid) -NoOp
        [pscustomobject]@{
            schema = 'controlstudio-mne-resume-execution-path/v1'
            resolvedCommand = $resolvedStop.Name
            listenerPid = $stopReadiness.listenerPid
            processCount = $stopReadiness.processCount
            result = 'ready_to_launch_scratch'
        } | ConvertTo-Json -Compress
        exit 0
    }
    Stop-ExactHostTreeAfterReviewedXgClose -ListenerPid ([int]$resumeHost.pid) -ListenerStartTimeUtc ([string]$resumeHost.startTimeUtc) -ClosedXgPid ([int]$resumeHost.closedXgPid)
} else {
    if ($CleanupReviewedHeadlessXgThenNoXgWorkspaceLaunch) {
        if ([string]::IsNullOrWhiteSpace($HeadlessXgCleanupAuthorization) -or [string]::IsNullOrWhiteSpace($NoXgWorkspaceLaunchAuthorization)) { throw 'Headless cleanup-and-certify requires both exact authorization files' }
        $headlessCleanup = Assert-CleanupReviewedHeadlessXgThenNoXgWorkspaceLaunch -AuthorizationPath $HeadlessXgCleanupAuthorization -SpecPath $specPath -Template $template -BundlePath $bundlePath
        if ($HeadlessXgCleanupPreflightOnly) {
            [pscustomobject]@{
                schema = 'controlstudio-mne-headless-cleanup-preflight/v1'
                xgPid = [int]$headlessCleanup.Reviewed.pid
                result = 'ready_to_terminate_exact_headless_xg_then_launch_workspace'
            } | ConvertTo-Json -Compress
            exit 0
        }
        Stop-ExactReviewedHeadlessXg -Reviewed $headlessCleanup
        Assert-WorkingHashAfterExactHeadlessCleanup -Template $template -ExpectedHash $headlessCleanup.ExpectedWorkingHash
    } elseif (-not $ResumeAfterHiddenScratchLaunch -and -not $ResumeAfterReviewedVisibleScratch -and -not $ResumeAfterReviewedNoXgWorkspaceLaunch) {
        $reviewedTarget = Assert-ExactVisibleCertificationTarget -Template $template -ExpectedPid $ExpectedXgPid -ExpectedExecutable $XgExecutable
        Stop-ExactDevelopmentTree -ListenerPid $ExpectedHostListenerPid -ReviewedTarget $reviewedTarget
        # The reviewed XG5000 process legitimately locks its working copy.
        # Perform the full file-hash/disposable-proof validation only after the
        # bound process is closed and before any replacement launch.
        & $mcpPython -c $validateSpecAfterClose $specPath
        if ($LASTEXITCODE -ne 0) {
            throw 'Certification template failed the CLI disposable-proof preflight after the reviewed target closed'
        }
    }
}
$restoredScratchHwnd = $null
if ($ResumeAfterFailedElevatedLaunch) {
    $scratchXg = $resumedFailedElevatedLaunch.Process
    $restoredScratchHwnd = [int64]$resumedFailedElevatedLaunch.Hwnd
} elseif ($ResumeAfterReviewedNoXgWorkspaceLaunch -or $CleanupReviewedHeadlessXgThenNoXgWorkspaceLaunch) {
    if ([string]::IsNullOrWhiteSpace($NoXgWorkspaceLaunchAuthorization)) { throw 'No-XG workspace launch requires an explicit authorization file' }
    $env:LOCALAPPDATA = $dataRoot; $env:CONTROLSTUDIO_RELEASE_CHANNEL = 'development'
    $workspaceRoot = Join-Path $dataRoot 'ControlStudio Development\workspaces'
    $noXgWorkspace = Assert-ResumeAfterReviewedNoXgWorkspaceLaunch -AuthorizationPath $NoXgWorkspaceLaunchAuthorization -SpecPath $specPath -Template $template -BundlePath $bundlePath -WorkspaceRoot $workspaceRoot -PythonExecutable $mcpPython
    $opened = Open-ExactWorkspaceWorkingCopy -WorkspaceId ([string]$noXgWorkspace.workspace_id) -ExpectedHash ([string]$noXgWorkspace.working_copy.current_sha256) -WorkspaceRoot $workspaceRoot -PythonExecutable $mcpPython
    $instance=$opened.instance
    if(-not (Test-ExactOpenedWorkspaceBinding -Opened $opened -ExpectedWorkspaceId ([string]$noXgWorkspace.workspace_id) -ExpectedWorkingPath ([string]$noXgWorkspace.working_copy.path) -ExpectedWorkingHash ([string]$noXgWorkspace.working_copy.current_sha256))){throw 'No-XG official workspace open did not verify an exact instance'}
    $template.target.workspace_id=[string]$opened.workspace_id;$template.target.project_path=[string]$opened.working_path;$template.target.pid=[int]$instance.pid;$template.target.hwnd=[int64]$instance.hwnd
    $marker=[IO.File]::ReadAllText([string]$template.scratch.marker_path,[Text.Encoding]::UTF8)|ConvertFrom-Json;$marker.project_path=[string]$opened.working_path;$marker.project_sha256=[string]$opened.working_hash;Write-JsonAtomically -Path ([string]$template.scratch.marker_path) -Value $marker;Write-JsonAtomically -Path $specPath -Value $template
    $scratchXg=Get-Process -Id ([int]$instance.pid)-ErrorAction Stop;$restoredScratchHwnd=[int64]$instance.hwnd
} elseif ($ResumeAfterReviewedVisibleScratch) {
    if ([string]::IsNullOrWhiteSpace($VisibleScratchResumeAuthorization)) { throw 'Visible-scratch resume requires an explicit authorization file' }
    $visibleScratch = Assert-ResumeAfterReviewedVisibleScratch -AuthorizationPath $VisibleScratchResumeAuthorization -SpecPath $specPath -Template $template -BundlePath $bundlePath
    if ($ExpectedXgPid -ne [int]$visibleScratch.Reviewed.pid) { throw 'Visible-scratch resume command PID does not match its reviewed authorization' }
    if ([string]::IsNullOrWhiteSpace($ReopenIntoWorkspaceId)) {
        $scratchXg = $visibleScratch.Process
        $restoredScratchHwnd = [int64]$visibleScratch.Reviewed.hwnd
    } else {
        # The selected visible scratch is never repurposed as a workspace by
        # metadata alone.  Close it through the reviewed no-save path, then use
        # the official launcher to open the immutable workspace working copy.
        Close-ReviewedXgForCertification -XgProcess $visibleScratch.Process
        $closedDeadline = [DateTimeOffset]::UtcNow.AddSeconds(5)
        $preservedBoundaryRestored = $false
        while ([DateTimeOffset]::UtcNow -lt $closedDeadline) {
            try {
                [void](Assert-ExactPreservedXgBaseline -Baseline $visibleScratch.PreservedBaseline -ReviewedPid ([int]$visibleScratch.Reviewed.pid))
                $preservedBoundaryRestored = $true
                break
            } catch {
                Start-Sleep -Milliseconds 250
            }
        }
        if (-not $preservedBoundaryRestored) {
            [void](Assert-ExactPreservedXgBaseline -Baseline $visibleScratch.PreservedBaseline -ReviewedPid ([int]$visibleScratch.Reviewed.pid))
            throw 'Reviewed scratch did not leave the exact preserved XG5000 baseline before workspace open'
        }
        Assert-WorkingHashAfterExactHeadlessCleanup -Template $template -ExpectedHash $visibleScratch.ExpectedWorkingHash
        if ($null -ne (Get-Process -Id ([int]$visibleScratch.Reviewed.pid) -ErrorAction SilentlyContinue)) {
            throw 'Reviewed scratch PID survived after the exact no-save close'
        }
        $env:LOCALAPPDATA = $dataRoot
        $env:CONTROLSTUDIO_RELEASE_CHANNEL = 'development'
        $workspaceRoot = Join-Path $dataRoot 'ControlStudio Development\workspaces'
        $opened = Open-ExactWorkspaceWorkingCopy -WorkspaceId $ReopenIntoWorkspaceId -ExpectedHash ([string]$template.target.project_hash) -WorkspaceRoot $workspaceRoot -PythonExecutable $mcpPython
        $instance = $opened.instance
        if (-not (Test-ExactOpenedWorkspaceBinding -Opened $opened -ExpectedWorkspaceId $ReopenIntoWorkspaceId -ExpectedWorkingPath ([string]$template.target.project_path) -ExpectedWorkingHash ([string]$template.target.project_hash))) {
            throw 'Official workspace open did not return one exact verified working-copy instance'
        }
        $template.target.workspace_id = [string]$opened.workspace_id
        $template.target.project_path = [string]$opened.working_path
        $template.target.pid = [int]$instance.pid
        $template.target.hwnd = [int64]$instance.hwnd
        $marker = [IO.File]::ReadAllText([string]$template.scratch.marker_path, [Text.Encoding]::UTF8) | ConvertFrom-Json
        $marker.project_path = [string]$opened.working_path
        $marker.project_sha256 = [string]$opened.working_hash
        Write-JsonAtomically -Path ([string]$template.scratch.marker_path) -Value $marker
        Write-JsonAtomically -Path $specPath -Value $template
        $scratchXg = Get-Process -Id ([int]$instance.pid) -ErrorAction Stop
        $restoredScratchHwnd = [int64]$instance.hwnd
    }
} elseif ($ResumeAfterHiddenScratchLaunch) {
    if ([string]::IsNullOrWhiteSpace($HiddenScratchResumeAuthorization)) { throw 'Hidden-scratch resume requires an explicit authorization file' }
    $hiddenScratch = Assert-ResumeAfterHiddenScratchLaunch -AuthorizationPath $HiddenScratchResumeAuthorization -SpecPath $specPath -Template $template -BundlePath $bundlePath
    if ($ExpectedXgPid -ne [int]$hiddenScratch.Reviewed.pid) { throw 'Hidden-scratch resume command PID does not match its reviewed authorization' }
    if ($HiddenScratchResumePreflightOnly) {
        [pscustomobject]@{
            schema = 'controlstudio-mne-hidden-scratch-resume-preflight/v1'
            xgPid = [int]$hiddenScratch.Reviewed.pid
            hwnd = [int64]$hiddenScratch.Reviewed.hwnd
            result = 'ready_to_certify_existing_scratch'
        } | ConvertTo-Json -Compress
        exit 0
    }
    if ($HiddenScratchResumeExecutionPathSelfTest) {
        $restoreCommand = Get-Command Restore-ReviewedHiddenScratchWindow -CommandType Function -ErrorAction Stop
        [pscustomobject]@{
            schema = 'controlstudio-mne-hidden-scratch-execution-path/v1'
            resolvedCommand = $restoreCommand.Name
            xgPid = [int]$hiddenScratch.Reviewed.pid
            hwnd = [int64]$hiddenScratch.Reviewed.hwnd
            result = 'ready_to_certify_existing_scratch'
        } | ConvertTo-Json -Compress
        exit 0
    }
    $restored = Restore-ReviewedHiddenScratchWindow -Resume $hiddenScratch
    $scratchXg = $restored.Process
    $restoredScratchHwnd = [int64]$restored.Hwnd
} else {
    # A raw /Automation launch can leave a headless XG5000 process when other
    # user sessions are open.  Delegate this launch to the independent CLI so
    # it captures the immutable baseline and accepts only a new PID/HWND bound
    # to this exact workspace working copy.
    $env:LOCALAPPDATA = $dataRoot
    $env:CONTROLSTUDIO_RELEASE_CHANNEL = 'development'
    $workspaceRoot = Join-Path $dataRoot 'ControlStudio Development\workspaces'
    $opened = Open-ExactWorkspaceWorkingCopy -WorkspaceId ([string]$template.target.workspace_id) -ExpectedHash ([string]$template.target.project_hash) -WorkspaceRoot $workspaceRoot -PythonExecutable $mcpPython
    $instance = $opened.instance
    if (-not (Test-ExactOpenedWorkspaceBinding -Opened $opened -ExpectedWorkspaceId ([string]$template.target.workspace_id) -ExpectedWorkingPath ([string]$template.target.project_path) -ExpectedWorkingHash ([string]$template.target.project_hash))) {
        throw 'Baseline-aware workspace launch did not return the exact verified working-copy instance'
    }
    $scratchXg = Get-Process -Id ([int]$instance.pid) -ErrorAction Stop
    $restoredScratchHwnd = [int64]$instance.hwnd
}
$template.target.pid = [int]$scratchXg.Id
$template.target.hwnd = if ($null -ne $restoredScratchHwnd) { $restoredScratchHwnd } else { [int64]$scratchXg.MainWindowHandle }
$runtimeSpecPath = Join-Path (Split-Path -Parent $specPath) ("mne-certification-runtime-$($scratchXg.Id).json")
[System.IO.File]::WriteAllText(
    $runtimeSpecPath,
    ($template | ConvertTo-Json -Depth 20),
    (New-Object System.Text.UTF8Encoding($false))
)

$keyBytes = New-Object byte[] 48
$random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $random.GetBytes($keyBytes) }
finally { $random.Dispose() }
$env:CONTROLSTUDIO_MCP_AUTHORIZATION_SECRET = [Convert]::ToBase64String($keyBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
$env:CONTROLSTUDIO_MNE_CAPABILITY_BUNDLE = $bundlePath
$env:LOCALAPPDATA = $dataRoot
$env:CONTROLSTUDIO_RELEASE_CHANNEL = 'development'
$env:CONTROLSTUDIO_PRODUCT_RELEASE = 'source-dev'
$env:CONTROLSTUDIO_LAUNCH_TOKEN = (Get-Content -LiteralPath $launchTokenPath -Raw).Trim()
$env:CONTROLSTUDIO_DEVELOPMENT_MODEL_BASE_URL = 'http://100.65.0.1:8978/v1'
$env:CONTROLSTUDIO_DEVELOPMENT_MODEL_NAME = 'nemotron-3-ultra'
$env:CONTROLSTUDIO_DEVELOPMENT_MODEL_TIMEOUT_SECONDS = '300'
$env:CONTROLSTUDIO_DEVELOPMENT_MODEL_API_KEY = 'sk-1234567890'
$xg5000Cli = Join-Path $workspace 'controlstudio-xg5000-cli\.venv\Scripts\controlstudio-xg5000.exe'
if (-not (Test-Path -LiteralPath $xg5000Cli -PathType Leaf)) {
    throw "Independent XG5000 CLI is missing: $xg5000Cli"
}
$env:CONTROLSTUDIO_XG5000_CLI_COMMAND = $xg5000Cli
$env:CONTROLSTUDIO_AGENT_COMMAND = if (Test-Path -LiteralPath $agentDevPython -PathType Leaf) {
    '"' + $agentDevPython + '" -m controlstudio_agent.sidecar'
} else {
    '"' + $agentExecutable + '"'
}
$env:CONTROLSTUDIO_MCP_COMMAND = $mcp

$certArguments = @('--spec', $runtimeSpecPath, '--output', $bundlePath, '--confirmed-disposable')
if ($DebugCertification) { $certArguments += '--debug' }
$certifierStdout = "$runtimeSpecPath.certifier.stdout.txt"
$certifierStderr = "$runtimeSpecPath.certifier.stderr.txt"
$certifierArgumentLine = (($certArguments | ForEach-Object {
    $value = [string]$_
    if ($value -match '[\s"]') { '"' + $value.Replace('"', '\"') + '"' } else { $value }
}) -join ' ')
$certifierProcess = Start-Process -FilePath $certifier -ArgumentList $certifierArgumentLine -PassThru -Wait -NoNewWindow -RedirectStandardOutput $certifierStdout -RedirectStandardError $certifierStderr
foreach ($diagnosticPath in @($certifierStdout, $certifierStderr)) {
    if (Test-Path -LiteralPath $diagnosticPath) {
        Get-Content -LiteralPath $diagnosticPath -Raw | Write-Output
    }
}
if ($certifierProcess.ExitCode -ne 0) {
    throw "MNE scratch certification failed with exit code $($certifierProcess.ExitCode). The development Host was not started."
}
if (-not (Test-Path -LiteralPath $bundlePath -PathType Leaf)) {
    throw 'MNE certification reported success without writing its bundle. The development Host was not started.'
}

$hostStartedUtc = [DateTime]::UtcNow
$hostProcess = Start-Process -FilePath $hostPython -ArgumentList @('-m', 'controlstudio_host') -WorkingDirectory $platform -WindowStyle Hidden -PassThru
$deadline = [DateTimeOffset]::UtcNow.AddSeconds(60)
while ([DateTimeOffset]::UtcNow -lt $deadline) {
    try {
        $health = Invoke-RestMethod -Uri "$baseUrl/api/health" -TimeoutSec 2
        $listenerPid = Get-DevelopmentListenerPid
        $listener = if ($null -ne $listenerPid) { Get-Process -Id $listenerPid -ErrorAction SilentlyContinue } else { $null }
        if ($health.status -eq 'ready' -and $null -ne $listener -and $listener.StartTime.ToUniversalTime() -ge $hostStartedUtc) {
            [pscustomobject]@{
                schema = 'controlstudio-mne-certification-restart/v1'
                hostPid = $listenerPid
                hostLauncherPid = $hostProcess.Id
                xgPid = $scratchXg.Id
                xgHwnd = [int64]$scratchXg.MainWindowHandle
                runtimeSpec = $runtimeSpecPath
                bundle = $bundlePath
                result = 'ready'
            } | ConvertTo-Json -Compress
            exit 0
        }
    } catch { }
    $hostProcess.Refresh()
    if ($hostProcess.HasExited) { throw "Restarted Host exited with code $($hostProcess.ExitCode)" }
    Start-Sleep -Milliseconds 250
}
throw 'Certified development Host did not become ready on its exact listener PID within 60 seconds'
