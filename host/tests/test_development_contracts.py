import asyncio
import hashlib
import hmac
import json
import shutil
import sys
import threading
import venv
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from controlstudio_host import development_contracts as contracts
from controlstudio_host.restart import HostRestartController, RestartBootstrapError, RestartHandoff

ENVIRONMENT = {"CONTROLSTUDIO_RELEASE_CHANNEL": "development", "CONTROLSTUDIO_PRODUCT_RELEASE": "source-dev"}


def _source(workspace):
    return workspace / "gidong-platform/packages/python/controlstudio-contracts/src/controlstudio_contracts"


def test_exact_commands_and_defaults_select_effective_interpreters(tmp_path):
    host = tmp_path / 'gidong-platform/launcher/.venv/Scripts/python.exe'
    agent = tmp_path / 'gidong-agent/.venv-dev/Scripts/python.exe'
    mcp = tmp_path / 'gidong-plc-mcp/.venv/Scripts/controlstudio-plc-mcp.exe'
    cli = tmp_path / 'controlstudio-xg5000-cli/.venv/Scripts/controlstudio-xg5000.exe'
    configured = {**ENVIRONMENT, 'CONTROLSTUDIO_AGENT_COMMAND': f'"{agent}" -m controlstudio_agent.sidecar',
                  'CONTROLSTUDIO_MCP_COMMAND': str(mcp), 'CONTROLSTUDIO_XG5000_CLI_COMMAND': f'"{cli}"'}
    selected = contracts._selected_interpreters(tmp_path, configured, host)
    assert selected == {'host_launcher': host, 'agent': agent, 'mcp': mcp.with_name('python.exe'),
                        'cli': cli.with_name('python.exe')}
    default = contracts._selected_interpreters(tmp_path, ENVIRONMENT, host)
    assert default['agent'] == tmp_path / 'gidong-agent/.venv/Scripts/python.exe'
    assert default['cli'] == default['mcp']  # cli_adapter's real no-command fallback.
    with pytest.raises(contracts.DevelopmentContractsError, match='agent: unsupported'):
        contracts._selected_interpreters(tmp_path, {**configured, 'CONTROLSTUDIO_AGENT_COMMAND': 'external-agent'}, host)
    with pytest.raises(contracts.DevelopmentContractsError, match='host_launcher'):
        contracts._selected_interpreters(tmp_path, configured, Path(sys.executable))


@pytest.mark.parametrize('environment,frozen', [({}, False),
    ({**ENVIRONMENT, 'CONTROLSTUDIO_PRODUCT_RELEASE': 'source'}, False),
    ({**ENVIRONMENT, 'CONTROLSTUDIO_RELEASE_CHANNEL': 'beta'}, False), (ENVIRONMENT, True)])
def test_non_source_development_and_frozen_keep_existing_authority(monkeypatch, environment, frozen):
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    monkeypatch.setattr(contracts, '_source_workspace', lambda: pytest.fail('Must not inspect release source'))
    contracts.verify_source_development_contracts(environment, Path(sys.executable))


def test_old_python_probe_reports_version_before_package_lookup(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'version_info', (3, 11, 9))
    monkeypatch.setattr(sys, 'argv', ['development_contracts', '--probe'])
    monkeypatch.setattr(contracts, '_installed_inventory', lambda: pytest.fail('No lookup before version rejection'))
    assert contracts.main() == 0
    assert json.loads(capsys.readouterr().out) == {'version': [3, 11, 9], 'files': {}}


def test_real_copied_package_detects_stale_same_version_without_importing_sdk(tmp_path, monkeypatch):
    workspace = tmp_path.parent / 'w'
    source = _source(workspace)
    shutil.copytree(_source(contracts._source_workspace()), source, ignore=shutil.ignore_patterns('__pycache__'))
    # Inventory must not execute even the public package's __init__.
    (source / '__init__.py').write_text('raise RuntimeError("SDK import forbidden")\n', encoding='utf-8')
    clone = workspace / 'env'
    venv.EnvBuilder(with_pip=False).create(clone)
    interpreter = clone / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    package = clone / ('Lib/site-packages' if sys.platform == 'win32' else
                       f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages') / 'controlstudio_contracts'
    shutil.copytree(source, package)
    monkeypatch.setattr(contracts, '_source_workspace', lambda: workspace)
    monkeypatch.setattr(contracts, '_selected_interpreters', lambda *args: {'mcp': interpreter})
    contracts.verify_source_development_contracts(ENVIRONMENT, interpreter)
    (package / 'native_ld_comparison.py').unlink()
    with pytest.raises(contracts.DevelopmentContractsError, match='mcp.*native_ld_comparison.py'):
        contracts.verify_source_development_contracts(ENVIRONMENT, interpreter)


@pytest.mark.parametrize('drift', ['extra', 'changed', 'source_changed', 'old_python'])
def test_inventory_drift_and_concurrent_source_edit_are_rejected(tmp_path, monkeypatch, drift):
    source = _source(tmp_path)
    source.mkdir(parents=True)
    (source / '__init__.py').write_text('# fixture', encoding='utf-8')
    expected = contracts._inventory(source)
    def probe(*args):
        files = dict(expected)
        if drift == 'extra': files['old_helper.py'] = '0' * 64
        elif drift == 'changed': files['__init__.py'] = '0' * 64
        elif drift == 'source_changed': (source / '__init__.py').write_text('# edited during probe', encoding='utf-8')
        return {'root': 'fixture', 'version': [3, 11, 9] if drift == 'old_python' else [3, 12, 0], 'files': files}
    monkeypatch.setattr(contracts, '_source_workspace', lambda: tmp_path)
    monkeypatch.setattr(contracts, '_selected_interpreters', lambda *args: {'agent': Path(sys.executable)})
    monkeypatch.setattr(contracts, '_probe', probe)
    message = {'source_changed': 'source changed', 'old_python': 'requires Python 3.12'}.get(drift, drift)
    with pytest.raises(contracts.DevelopmentContractsError, match=message):
        contracts.verify_source_development_contracts(ENVIRONMENT, Path(sys.executable))


def _controller(tmp_path, spawn):
    return HostRestartController(executable=Path(sys.executable), cwd=tmp_path, environment=ENVIRONMENT,
        port=8767, restart_log_path=tmp_path / 'restart.log', spawn=spawn)


def test_restart_mismatch_has_no_replacement_or_log_write(tmp_path, monkeypatch):
    spawned = []
    def reject(*args):
        raise contracts.DevelopmentContractsError('mcp interpreter=fixture: missing=[native_ld_comparison.py]')
    monkeypatch.setattr(contracts, 'verify_source_development_contracts', reject)
    with pytest.raises(RestartBootstrapError, match='native_ld_comparison.py'):
        _controller(tmp_path, lambda *args, **kwargs: spawned.append(args)).schedule(
            RestartHandoff('r' * 40, 's' * 40, datetime.now(UTC) + timedelta(hours=1)))
    assert spawned == [] and not (tmp_path / 'restart.log').exists()


def test_restart_route_rejects_drift_and_joins_cancelled_bootstrap(tmp_path, monkeypatch):
    from controlstudio_host.app import BROWSER_CSRF_PREFIX, create_app
    from controlstudio_host.config import HostConfig
    from controlstudio_host.mcp_client import MCPStatus

    async def scenario():
        config = HostConfig(database_path=tmp_path / 'host.sqlite3', knowledge_database_path=tmp_path / 'knowledge.sqlite3')
        app = create_app(config=config, mcp=SimpleNamespace(status=MCPStatus(status='unavailable')),
                         model_repository=SimpleNamespace(get=lambda: None))
        token = 'test-session'
        app.state.browser_sessions[token] = datetime.now(UTC) + timedelta(hours=1)
        headers = {'Authorization': 'Bearer ' + token, 'Origin': config.canonical_origin,
            'x-controlstudio-csrf': hmac.new(config.launch_token.encode(), BROWSER_CSRF_PREFIX + token.encode(), hashlib.sha256).hexdigest()}
        stopped, spawned = [], []
        app.state.request_shutdown = lambda: stopped.append(True)
        def reject(*args): raise contracts.DevelopmentContractsError('mcp: fixture drift')
        monkeypatch.setattr(contracts, 'verify_source_development_contracts', reject)
        app.state.request_restart = _controller(tmp_path, lambda *args, **kwargs: spawned.append(args)).schedule
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=config.canonical_origin, headers=headers) as client:
            response = await client.post('/api/v1/host/restart')
            assert response.status_code == 503
            assert not spawned and not stopped and not app.state.restart_receipts
            assert not app.state.shutdown_event.is_set()
            assert (await client.get('/api/v1/status')).status_code == 200
            entered, release = threading.Event(), threading.Event()
            def bootstrap(handoff):
                entered.set()
                assert release.wait(5)
                spawned.append(handoff)
            app.state.request_restart = bootstrap
            request = asyncio.create_task(client.post('/api/v1/host/restart'))
            while not entered.is_set(): await asyncio.sleep(0.001)
            request.cancel()
            await asyncio.sleep(0)
            assert not stopped and not app.state.restart_receipts
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await request
            for _ in range(100):
                if app.state.shutdown_event.is_set(): break
                await asyncio.sleep(0.01)
            assert len(spawned) == 1
            assert list(app.state.restart_receipts.values()) == ['scheduled']
            assert app.state.shutdown_event.is_set()
            await asyncio.sleep(0.1)
            assert stopped == [True]
    asyncio.run(scenario())


def test_elevation_carries_existing_source_commands_only(monkeypatch):
    import base64
    import json
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'launcher/src'))
    from controlstudio_launcher.integrity import apply_elevation_context, encode_elevation_context
    values = {**ENVIRONMENT, 'CONTROLSTUDIO_AGENT_COMMAND': 'agent', 'CONTROLSTUDIO_MCP_COMMAND': 'mcp',
              'CONTROLSTUDIO_XG5000_CLI_COMMAND': 'cli'}
    encoded = encode_elevation_context({**values, 'UNRELATED_SECRET': 'never-pass'})
    assert json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))['environment'] == values
    restored = {}
    apply_elevation_context(encoded, restored)
    assert all(restored[key] == value for key, value in values.items())
    assert 'UNRELATED_SECRET' not in restored
