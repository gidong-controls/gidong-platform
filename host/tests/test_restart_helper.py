from types import SimpleNamespace

from controlstudio_host import restart_helper as helper


def test_web_bootstrap_timeout_retains_ready_host_and_allows_fresh_handoff(monkeypatch):
    from dataclasses import replace
    from pathlib import Path
    from unittest.mock import Mock

    import pytest

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'launcher/src'))
    from controlstudio_launcher import app
    from controlstudio_launcher.config import default_settings
    from controlstudio_launcher.errors import HostStartError, LauncherAlreadyRunning
    from controlstudio_launcher.health import (
        BrowserHandoffResult,
        BrowserReadinessResult,
        HealthResult,
        HealthState,
        LaunchVerificationResult,
    )

    class Guard:
        locked = False
        entries = 0

        def __enter__(self):
            if self.locked:
                raise LauncherAlreadyRunning('startup still locked')
            self.locked = True
            self.entries += 1
            return self

        def __exit__(self, *args):
            self.locked = False

    guard = Guard()
    clock = [0.0]
    process = Mock()
    process.poll.return_value = None

    def start(*args):
        assert guard.locked
        return process

    starter = Mock(start=Mock(side_effect=start))
    tokens = Mock()
    tokens.rotate.return_value = tokens.load.return_value = 'session-token'
    probe = Mock()
    # The second launch sees a stale ABSENT pre-lock probe. Its locked probe
    # must still reuse the ready Host instead of starting another process.
    probe.probe.side_effect = [HealthResult(state, state.value) for state in (
        HealthState.ABSENT, HealthState.ABSENT, HealthState.READY,
        HealthState.ABSENT, HealthState.READY,
    )]
    probe.verify_launch_token.return_value = LaunchVerificationResult(True, 'verified')
    probe.issue_browser_handoff.side_effect = [
        BrowserHandoffResult(True, 'issued', 'old-ticket', 'old-receipt'),
        BrowserHandoffResult(True, 'issued', 'fresh-ticket', 'fresh-receipt'),
    ]
    receipts = Mock()
    receipts.begin.return_value = {}
    broker = Mock()
    broker.relaunch_if_required.return_value = False
    urls = []
    monkeypatch.setattr(app, 'write_launcher_diagnostic', lambda message: None)

    def sleep(seconds):
        clock[0] += seconds

    def launcher():
        return app.ControlStudioLauncher(
            settings=replace(default_settings(), startup_timeout_seconds=2,
                             poll_interval_seconds=1),
            health_probe=probe, host_starter=starter, ui_opener=Mock(),
            instance_guard=guard, token_store=tokens, elevation_broker=broker,
            elevation_receipts=receipts, web_url_sink=urls.append,
            monotonic=lambda: clock[0], sleep=sleep,
        )

    retried = False

    def browser_readiness(token, receipt):
        nonlocal retried
        assert token == 'session-token' and not guard.locked
        if receipt == 'fresh-receipt':
            return BrowserReadinessResult(True, 'bootstrapped')
        if not retried:
            retried = True
            launcher().run(ui_mode='web-url')
        return BrowserReadinessResult(False, 'browser delayed', retryable=True)

    probe.probe_browser_readiness.side_effect = browser_readiness
    with pytest.raises(HostStartError, match='browser did not complete'):
        launcher().run(ui_mode='web-url')

    assert retried and guard.entries == 2 and not guard.locked
    assert [url.split('handoff_ticket=')[1] for url in urls] == ['old-ticket', 'fresh-ticket']
    starter.start.assert_called_once()
    tokens.rotate.assert_called_once()
    assert probe.verify_launch_token.call_count == 2
    assert broker.relaunch_if_required.call_count == 4
    process.terminate.assert_not_called()
    process.release.assert_called_once()
    failure = receipts.finish.call_args.kwargs
    assert failure['outcome'] == 'failed'
    assert failure['host_ready'] and failure['elevated_host_retention_verified']


def test_launcher_configured_data_directory_reaches_startup_and_elevation(monkeypatch):
    import json
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'launcher/src'))
    from controlstudio_launcher import cli
    from controlstudio_launcher.integrity import apply_elevation_context, encode_elevation_context

    local_root = Path(__file__).resolve().parents[2]
    profile = local_root / 'existing profile'
    config = local_root / 'launcher-unit.json'
    config_values = {'data_directory': str(profile), 'startup_timeout_seconds': 42}
    reads = []

    def read_config(path, *, encoding):
        assert path == config and encoding == 'utf-8'
        reads.append(path)
        return json.dumps(config_values)

    monkeypatch.setattr(Path, 'read_text', read_config)
    monkeypatch.setattr(cli, 'default_config_path', lambda: config)
    names = ('CONTROLSTUDIO_DATABASE', 'CONTROLSTUDIO_WORKSPACE_ROOT', 'CONTROLSTUDIO_KNOWLEDGE_DATABASE')
    expected = dict(zip(names, map(str, (profile / 'controlstudio.sqlite3', profile / 'workspaces',
                                        profile / 'controlstudio-knowledge.sqlite3'))))
    inherited = {name: str(local_root / ('different-' + name)) for name in names}
    for name, value in inherited.items():
        monkeypatch.setenv(name, value)
    startup = []
    handoffs = []
    settings_seen = []

    def host_starter():
        startup.append({name: cli.os.environ.get(name) for name in names})
        return object()

    def elevation_broker(arguments):
        restored = {}
        apply_elevation_context(encode_elevation_context(), restored)
        handoffs.append({name: restored.get(name) for name in names})
        return object()

    def launcher(**kwargs):
        settings_seen.append(kwargs['settings'])
        return SimpleNamespace(run=lambda **kwargs: None)

    monkeypatch.setattr(cli, 'ControlStudioLauncher', launcher)
    monkeypatch.setattr(cli, 'SubprocessHostStarter', host_starter)
    monkeypatch.setattr(cli, 'create_elevation_broker', elevation_broker)
    monkeypatch.setattr(cli, 'create_instance_guard', lambda: object())
    monkeypatch.setattr(cli, 'FileLaunchTokenStore', lambda: object())
    monkeypatch.setattr(cli, 'SystemUiOpener', lambda: object())
    assert cli.main(['--no-open']) == 0
    assert startup[-1] == handoffs[-1] == expected
    assert settings_seen[-1].data_directory == profile
    assert settings_seen[-1].startup_timeout_seconds == 42
    assert reads == [config]

    for name, value in inherited.items():
        monkeypatch.setenv(name, value)
    assert cli.main(['--no-config', '--no-open']) == 0
    assert startup[-1] == handoffs[-1] == inherited
    assert settings_seen[-1].data_directory is None
    assert reads == [config]
    config_values.clear()
    config_values['startup_timeout_seconds'] = 43
    assert cli.main(['--no-open']) == 0
    assert startup[-1] == handoffs[-1] == inherited
    assert settings_seen[-1].data_directory is None
    assert settings_seen[-1].startup_timeout_seconds == 43


def test_elevation_context_preserves_profile_paths_without_unrelated_environment(monkeypatch):
    import base64
    import json
    from pathlib import Path
    import pytest

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'launcher/src'))
    from controlstudio_launcher.integrity import apply_elevation_context, encode_elevation_context
    from controlstudio_launcher.errors import ElevationError

    paths={'CONTROLSTUDIO_DATABASE':r'C:\profiles\live\controlstudio.sqlite3',
           'CONTROLSTUDIO_WORKSPACE_ROOT':r'C:\profiles\live\workspaces',
           'CONTROLSTUDIO_KNOWLEDGE_DATABASE':r'C:\profiles\live\controlstudio-knowledge.sqlite3'}
    encoded=encode_elevation_context({**paths,'UNRELATED_SECRET':'test-only',
        'CONTROLSTUDIO_DEVELOPMENT_MODEL_API_KEY':'test-only','CONTROLSTUDIO_PORT':'8767'})
    payload=json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
    assert payload['environment']==paths
    restored={'UNCHANGED':'existing'}
    apply_elevation_context(encoded,restored)
    assert all(restored[name]==value for name,value in paths.items())
    assert restored['UNCHANGED']=='existing' and restored['CONTROLSTUDIO_ELEVATION_HANDOFF_V1']=='1'
    assert not {'UNRELATED_SECRET','CONTROLSTUDIO_DEVELOPMENT_MODEL_API_KEY','CONTROLSTUDIO_PORT'} & restored.keys()
    payload['environment']['UNRELATED_SECRET']='test-only'
    unsupported=base64.urlsafe_b64encode(json.dumps(payload).encode('ascii')).decode('ascii').rstrip('=')
    with pytest.raises(ElevationError):
        apply_elevation_context(unsupported,{})


def test_restart_waits_for_parent_exit_and_preserves_explicit_timeout(monkeypatch, capsys):
    events = []
    result = {"wait": 0, "port_released": True}

    def open_process(access, inherit, pid):
        assert (access, inherit, pid) == (0x00100000, False, 123)
        events.append("open")
        return 456

    def wait(handle, milliseconds):
        assert handle == 456 and "replacement" not in events
        events.append(("wait", milliseconds))
        return result["wait"]

    def close(handle):
        assert handle == 456
        assert close.argtypes == (helper.ctypes.c_void_p,)
        assert close.restype is helper.ctypes.c_int
        events.append("close")

    kernel = SimpleNamespace(OpenProcess=open_process, WaitForSingleObject=wait, CloseHandle=close)
    monkeypatch.setattr(helper.os, "name", "nt")
    monkeypatch.setattr(helper.ctypes, "WinDLL", lambda *args, **kwargs: kernel)
    monkeypatch.setattr(helper, "_redirect_replacement_output", lambda: events.append("log"))

    def port_release(port):
        assert port == 8767
        events.append("port")
        return result["port_released"]

    monkeypatch.setattr(helper, "_wait_for_port_release", port_release)
    monkeypatch.setattr(helper, "_run_replacement_host", lambda: events.append("replacement"))
    assert helper.restart_after_parent_exits(123, 8767) == 0
    assert events == ["log", "open", ("wait", 0xFFFFFFFF), "close", "port", "replacement"]

    events.clear()
    result["wait"] = 258
    assert helper._wait_for_parent_exit(123, timeout_seconds=1.25) is False
    assert events == ["open", ("wait", 1250), "close"]

    events.clear()
    result["wait"] = 0xFFFFFFFF
    assert helper.restart_after_parent_exits(123, 8767) == 1
    assert events == ["log", "open", ("wait", 0xFFFFFFFF), "close"]
    assert "parent_wait_failed" in capsys.readouterr().err

    events.clear()
    result.update(wait=0, port_released=False)
    assert helper.restart_after_parent_exits(123, 8767) == 1
    assert events == ["log", "open", ("wait", 0xFFFFFFFF), "close", "port"]
    assert "port_release_failed" in capsys.readouterr().err

    events.clear()

    def parent_probe(pid, signal):
        assert (pid, signal) == (123, 0)
        events.append("probe")
        if events.count("probe") == 2:
            raise ProcessLookupError

    def no_deadline_clock():
        raise AssertionError("The default parent wait must not invent a finite deadline")

    monkeypatch.setattr(helper, "os", SimpleNamespace(name="posix", kill=parent_probe))
    monkeypatch.setattr(helper, "time", SimpleNamespace(
        monotonic=no_deadline_clock, sleep=lambda seconds: events.append(("sleep", seconds))))
    assert helper._wait_for_parent_exit(123) is True
    assert events == ["probe", ("sleep", 0.05), "probe"]

    events.clear()
    ticks = iter((100.0, 100.0, 100.05, 100.11))
    monkeypatch.setattr(helper.os, "kill", lambda pid, signal: events.append("probe"))
    monkeypatch.setattr(helper.time, "monotonic", lambda: next(ticks))
    assert helper._wait_for_parent_exit(123, timeout_seconds=0.1) is False
    assert events == ["probe", ("sleep", 0.05), "probe", ("sleep", 0.05)]


def test_installer_reuses_registered_channel_root_with_powershell51(tmp_path):
    import json
    import os
    import subprocess
    from pathlib import Path

    scripts = Path(__file__).resolve().parents[2] / 'packaging/scripts'
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    harness = tmp_path / 'installer-root-core.ps1'
    harness.write_text(r'''
param([string]$InstallerScript)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1) {
    throw 'The installer core must execute on Windows PowerShell 5.1.'
}
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($InstallerScript, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -ne 0) { throw ($parseErrors.Message -join '; ') }
# Execute only the real function definitions. Never execute the installer body,
# its error Popup, imported registry reader, or any install/registry mutation.
foreach ($name in @('Get-ControlStudioUninstallKeyName', 'Get-ControlStudioInstallRoot',
                    'Assert-ControlStudioUninstallRegistrationCompatible')) {
    $definition = $ast.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true)
    if ($null -eq $definition) { throw "Installer function is missing: $name" }
    . ([scriptblock]::Create($definition.Extent.Text))
}
function Get-ControlStudioRegistryEntries { return $script:MockRegistryEntries }
function Assert-Equal($Actual, $Expected) {
    if ($Actual -cne $Expected) { throw "Expected '$Expected'; received '$Actual'." }
}
function Assert-Refused([scriptblock]$Action, [string]$Message) {
    $refused = $false
    try { & $Action } catch {
        if ($_.Exception.Message -ne $Message) { throw }
        $refused = $true
    }
    if (-not $refused) { throw "Expected installer refusal: $Message" }
}
$default = 'C:\InstallerCore\Programs\ControlStudio Beta'
$registeredBeta = 'C:\InstallerCore\existing beta\app'
$registeredProduction = 'C:\InstallerCore\existing production\app'
$explicit = 'C:\InstallerCore\explicit beta\app'
$script:MockRegistryEntries = @{}
$selected = Get-ControlStudioInstallRoot -Channel beta -DefaultRoot $default -ExplicitRoot '' -RegistryEntries $script:MockRegistryEntries
Assert-Equal $selected $default
Assert-ControlStudioUninstallRegistrationCompatible -Root $selected -Channel beta
$selected = Get-ControlStudioInstallRoot -Channel beta -DefaultRoot $default -ExplicitRoot $explicit -RegistryEntries $script:MockRegistryEntries
Assert-Equal $selected $explicit
Assert-ControlStudioUninstallRegistrationCompatible -Root $selected -Channel beta
$script:MockRegistryEntries = @{
    ControlStudioBeta = @{ InstallLocation = $registeredBeta; ControlStudioChannel = 'beta' }
    ControlStudio = @{ InstallLocation = $registeredProduction; ControlStudioChannel = 'production' }
}
$selected = Get-ControlStudioInstallRoot -Channel beta -DefaultRoot $default -RegistryEntries $script:MockRegistryEntries
Assert-Equal $selected $registeredBeta
Assert-ControlStudioUninstallRegistrationCompatible -Root $selected -Channel beta
$selected = Get-ControlStudioInstallRoot -Channel production -DefaultRoot $default -RegistryEntries $script:MockRegistryEntries
Assert-Equal $selected $registeredProduction
Assert-ControlStudioUninstallRegistrationCompatible -Root $selected -Channel production
$selected = Get-ControlStudioInstallRoot -Channel beta -DefaultRoot $default -ExplicitRoot $registeredBeta -RegistryEntries $script:MockRegistryEntries
Assert-Equal $selected $registeredBeta
$selected = Get-ControlStudioInstallRoot -Channel beta -DefaultRoot $default -ExplicitRoot $explicit -RegistryEntries $script:MockRegistryEntries
Assert-Equal $selected $explicit
Assert-Refused { Assert-ControlStudioUninstallRegistrationCompatible -Root $selected -Channel beta } 'The ControlStudio uninstall registration points to a different install root.'
$script:MockRegistryEntries.ControlStudioBeta.ControlStudioChannel = 'production'
Assert-Refused { Assert-ControlStudioUninstallRegistrationCompatible -Root $registeredBeta -Channel beta } 'The ControlStudio uninstall registration channel is inconsistent.'
$script:MockRegistryEntries.ControlStudioBeta.ControlStudioChannel = 'beta'
$script:MockRegistryEntries.ControlStudio.InstallLocation = $registeredBeta
Assert-Refused { Assert-ControlStudioUninstallRegistrationCompatible -Root $registeredBeta -Channel beta } 'A different ControlStudio channel is registered at this install root.'
$script:MockRegistryEntries.Remove('ControlStudio')
$script:MockRegistryEntries.ControlStudioBeta.InstallLocation = ''
Assert-Refused { Get-ControlStudioInstallRoot -Channel beta -DefaultRoot $default -RegistryEntries $script:MockRegistryEntries } 'Existing ControlStudio uninstall registration has no install location.'
Assert-Refused { Assert-ControlStudioUninstallRegistrationCompatible -Root $registeredBeta -Channel beta } 'Existing ControlStudio uninstall registration has no install location.'
@{ status = 'PASS'; powershell = $PSVersionTable.PSVersion.ToString() } | ConvertTo-Json -Compress
''', encoding='utf-8')
    completed = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File', str(harness),
        '-InstallerScript', str(scripts / 'Install-ControlStudio.ps1')],
        capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result['status'] == 'PASS' and result['powershell'].startswith('5.1.')
    assert 'exit /b %ERRORLEVEL%' in (scripts / 'Install-ControlStudio.cmd').read_text()
