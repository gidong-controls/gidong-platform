"""HTTP/state regressions with deterministic boundary doubles; no IDE acceptance claims."""
import asyncio
import gzip
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx

from controlstudio_contracts import ExternalToolRequestV1
from controlstudio_host.agent_protocol import validated_native_checkpoint
from controlstudio_host.agent_runtime import AgentRunOutput
from controlstudio_host.app import BROWSER_CSRF_PREFIX, create_app
from controlstudio_host.config import HostConfig
from controlstudio_host.knowledge import KnowledgeStore
from controlstudio_host.mcp_client import MCPGateway, MCPStatus
from controlstudio_host.model_connection import ModelSecrets, StoredModelConnection
from controlstudio_host.models import RunState
from controlstudio_host.store import HostStore


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_native_checkpoint_reuses_only_committed_exact_snapshot(monkeypatch):
    import sqlite3
    from contextlib import contextmanager
    import pytest
    from controlstudio_contracts.checkpoint_transfer import CheckpointTransfer, encode_native_checkpoint_update
    from controlstudio_host.agent_protocol import (
        AgentProtocolError, validated_native_checkpoint_append, validated_native_checkpoint_snapshot)

    database = sqlite3.connect(':memory:')
    database.row_factory = sqlite3.Row
    database.execute('CREATE TABLE host_state(key TEXT PRIMARY KEY, value_json TEXT, updated_at TEXT)')
    store = HostStore.__new__(HostStore)
    store._native_checkpoint_snapshot = None
    @contextmanager
    def connection():
        try:yield database
        finally:database.rollback()
    monkeypatch.setattr(store, '_connection', connection)
    key = 'native_ld_checkpoint:run'
    binding = {'workspace_id': 'workspace', 'revision_id': 'revision', 'project_path': 'working.xgwx'}
    body = {'schemaVersion': '1', 'public_state': {
        'model_prompt': 'request', 'completed_pairs': [], 'records': []},
        'private_state': {'transport_records': []}}
    base = validated_native_checkpoint_snapshot(body)
    assert base.digest == digest(body)
    assert base.canonical_json == json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    assert validated_native_checkpoint_snapshot(base, base.digest) is base
    with pytest.raises(AgentProtocolError, match='digest mismatched'):
        validated_native_checkpoint_snapshot(body, 'f' * 64)
    for invalid in ((1,), float('nan')):
        invalid_body = {**body, 'public_state': {**body['public_state'], 'records': [invalid]}}
        with pytest.raises(AgentProtocolError):
            validated_native_checkpoint_snapshot(invalid_body)
    with pytest.raises(AgentProtocolError):
        validated_native_checkpoint_snapshot({**body, 'unexpected': True})
    store.set_native_checkpoint_snapshot(key, binding, base)
    queries = []

    @contextmanager
    def traced():
        with connection() as value:
            value.set_trace_callback(lambda sql: queries.append(sql)
                if sql.startswith('SELECT value_json FROM host_state WHERE key=')
                and 'native_cp_node:' in sql else None)
            yield value
    monkeypatch.setattr(store, '_connection', traced)

    def append(previous, number):
        body['public_state']['records'].append({'event': 'observed', 'number': number})
        encoded, _ = encode_native_checkpoint_update(body, previous.incremental_state)
        transfer = CheckpointTransfer()
        for chunk in encoded.chunks():transfer.append(chunk)
        return validated_native_checkpoint_append(transfer.finish_encoded(encoded.digest), previous)

    first = append(base, 1)
    store.set_native_checkpoint_snapshot(key, binding, first)
    queries.clear()
    for _ in range(2):
        restored = store.get_native_checkpoint_state(key)
        assert restored['checkpoint'] is first and restored['digest'] == first.digest
    assert not queries
    mutable = store.get_host_state(key)
    assert queries and type(mutable['checkpoint']) is dict
    mutable['checkpoint']['public_state']['records'].clear()
    assert len(first['public_state']['records']) == 1
    with pytest.raises(TypeError):first['public_state']['model_prompt'] = 'changed'

    second = append(first, 2)
    store.set_native_checkpoint_snapshot(key, binding, second)
    assert store.get_native_checkpoint_state(key)['checkpoint'] is second
    committed = store._native_checkpoint_snapshot
    with connection() as value, value:
        value.execute("CREATE TRIGGER reject_checkpoint BEFORE INSERT ON host_state "
            "WHEN NEW.key LIKE 'native_cp_node:%' BEGIN SELECT RAISE(ABORT, 'test rejection'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.set_native_checkpoint_snapshot(key, binding, append(second, 3))
    assert store._native_checkpoint_snapshot is committed
    assert store.get_native_checkpoint_state(key)['checkpoint'] is second

    with connection() as value, value:
        head = json.loads(value.execute('SELECT value_json FROM host_state WHERE key=?', (key,)).fetchone()[0])
        head['digest'] = 'f' * 64
        value.execute('UPDATE host_state SET value_json=? WHERE key=?',
            (json.dumps(head, ensure_ascii=False, sort_keys=True, separators=(',', ':')), key))
    queries.clear()
    with pytest.raises(AgentProtocolError, match='digest mismatched'):
        store.get_native_checkpoint_state(key)
    assert queries and store._native_checkpoint_snapshot is committed
    database.close()


def test_supervisor_delivers_progress_during_tool_and_preserves_order_on_cancel(monkeypatch):
    from controlstudio_host.agent_client import AgentSupervisor
    from controlstudio_host.agent_protocol import AgentProtocolMessage

    async def scenario(cancel):
        supervisor = AgentSupervisor(command=('unused-test-sidecar',))
        gate, started, progress = asyncio.Event(), asyncio.Event(), asyncio.Event()
        seen = []

        def frame(kind, **payload):
            return AgentProtocolMessage(protocol_version='1', type=kind,
                payload={'run_id': 'run', **payload})

        async def started_sidecar():
            pass

        async def send(*args):
            pass

        async def terminate():
            pass

        async def cancel_run(run_id):
            await supervisor._messages.put(frame('run.cancelled'))

        async def handle(message):
            seen.append((message.type, message.payload.get('id')))
            if message.type == 'tool.request' and message.payload.get('id') == 1:
                started.set()
                await gate.wait()
            if message.type == 'run.progress':
                progress.set()

        monkeypatch.setattr(supervisor, 'ensure_started', started_sidecar)
        monkeypatch.setattr(supervisor, '_send', send)
        monkeypatch.setattr(supervisor, '_terminate_process', terminate)
        monkeypatch.setattr(supervisor, 'cancel_run', cancel_run)
        task = asyncio.create_task(supervisor.run({'run_id': 'run'}, on_message=handle))
        await supervisor._messages.put(frame('tool.request', id=1))
        await asyncio.wait_for(started.wait(), 1)
        await supervisor._messages.put(frame('run.native_checkpoint.chunk', id=2))
        await supervisor._messages.put(frame('tool.request', id=3))
        await supervisor._messages.put(frame('run.progress', id=4))
        await asyncio.wait_for(progress.wait(), 1)
        assert seen == [('tool.request', 1), ('run.progress', 4)]
        if cancel:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            else:
                assert False, 'cancellation must propagate'
            assert seen == [('tool.request', 1), ('run.progress', 4),
                ('run.native_checkpoint.chunk', 2)]
        else:
            await supervisor._messages.put(frame('run.final', id=5))
            gate.set()
            await asyncio.wait_for(task, 1)
            assert seen == [('tool.request', 1), ('run.progress', 4),
                ('run.native_checkpoint.chunk', 2), ('tool.request', 3), ('run.final', 5)]

    asyncio.run(scenario(False))
    asyncio.run(scenario(True))


def test_knowledge_sync_reuses_only_successful_snapshot_in_same_sidecar(monkeypatch):
    from controlstudio_host.agent_client import AgentSupervisor

    async def scenario():
        supervisor = AgentSupervisor(command=('unused-test-sidecar',))
        requests, generation, count = [], [0], [1]
        async def started():
            if supervisor._process is None or supervisor._process.returncode is not None:
                await supervisor._discard_dead_process()
                generation[0] += 1
                supervisor._process = SimpleNamespace(returncode=None, stdin=None)
        async def request(command, *, expected):
            requests.append((generation[0], command['type']))
            if command['type'] == 'knowledge.chunk':
                payload = command['payload']
                return SimpleNamespace(payload={'checkpointDigest': payload['checkpointDigest'],
                    'index': payload['index']})
            assert command['type'] == 'knowledge.sync' and expected == 'knowledge.synced'
            return SimpleNamespace(payload={'count': count[0]})
        monkeypatch.setattr(supervisor, 'ensure_started', started)
        monkeypatch.setattr(supervisor, '_request', request)
        documents = [{'reference': 'knowledge://source', 'content': 'initial', 'metadata': {'revision': 1}}]
        assert await supervisor.sync_knowledge(documents) == 1
        first_requests = list(requests)
        count[0] = 9  # An identical snapshot returns the successfully acknowledged count.
        assert await supervisor.sync_knowledge(json.loads(json.dumps(documents))) == 1
        assert requests == first_requests
        for field, value in (('content', 'changed'), ('metadata', {'revision': 2})):
            documents[0][field] = value
            assert await supervisor.sync_knowledge(documents) == 9
        count[0] = 0
        assert await supervisor.sync_knowledge([]) == 0
        assert [kind for _, kind in requests].count('knowledge.sync') == 4
        before_restart = len(requests)
        supervisor._process.returncode = 0
        await supervisor.stop()
        assert await supervisor.sync_knowledge([]) == 0
        assert generation[0] == 2 and len(requests) > before_restart
        assert requests[-1] == (2, 'knowledge.sync')
        supervisor._process.returncode = 0
        await supervisor.stop()
    asyncio.run(scenario())


def test_preparation_context_dispatch_uses_shared_lock_and_private_hook(monkeypatch):
    import tempfile
    import pytest
    from controlstudio_host import native_workspace_host as native
    from controlstudio_host.execution_broker import MCPExecutionBroker
    from controlstudio_host.mcp_client import MCPToolError
    from controlstudio_host.native_project_preparation import NativeProjectPreparation
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / 'gidong-plc-mcp' / 'src'))
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / 'controlstudio-xg5000-cli' / 'src'))
    from xg5000_control import native_preparation
    temporary = tempfile.TemporaryDirectory()
    monkeypatch.setattr(native_preparation, 'default_workspace_root', lambda: Path(temporary.name))

    async def scenario():
        reservation = native_preparation.reserve_native_workspace(str(uuid.uuid4()), str(uuid.uuid4()),
            'project', display_name='Fixture project')
        frame = {'pid': 501, 'hwnd': 502, 'expected_process_create_time': 123.0}
        arguments = {'cpu_series': 'XGB', 'cpu_type': 'Fixture CPU', 'plc_name': 'PLC', 'program_name': 'Main'}
        saved = {'sha256': 'a' * 64, 'stable_during_read': True, 'exists': True}
        current, contexts, receipts, events, delegated = [None], [], [], [], []

        class Guard:
            depth = 0
            async def __aenter__(self):
                assert self.depth == 0, 'The public execution wrapper must acquire the lock only once'
                self.depth = 1
            async def __aexit__(self, *args):
                self.depth = 0

        guard = Guard()
        class PreparationGateway:
            creations = 0
            calls = []
            async def call_workspace_tool(self, name, request):
                assert guard.depth == 1
                assert owner._diagnostic_call_context['call_id']
                contexts.append(dict(owner._diagnostic_call_context))
                self.calls.append(name)
                if name == 'workspace.inspect_native_preparation':
                    return native_preparation.inspect_native_preparation(request)
                if name == 'workspace.prepare_native':
                    self.creations += 1
                    assert self.creations == 1
                    session = {**frame, 'session_id': 'prepared-session',
                               'project_path': reservation['project_path'], 'project_hash': saved['sha256']}
                    return {'ok': True, 'workspace': {**reservation, 'source': {'display_name': reservation['display_name'],
                        'captured_path': 'C:/fixture/source/project.xgwx', 'sha256': saved['sha256']}},
                        'session': session, 'program_name': 'Main', 'program_names': ['Main'], 'saved_project': saved}
                if name == 'xg5000.editor':
                    assert request['action'] == 'observe'
                    return {'ok': True, 'read_only': True, 'observation': {}}
                assert name == 'workspace.simulator' and request['action'] == 'read_devices'
                return {'ok': True, 'action': 'read_devices', 'input_state_unknown': False,
                    'saved_project': saved, 'loaded_sha256': saved['sha256'], 'trace': [{
                        'observed_at': 1234.0, 'transport': 'XGT_FENET_SIMULATOR', 'atomic_snapshot': False,
                        'devices': [{'area': 'M', 'bit': 10, 'value': False}]}]}

        async def prepared(result):
            current[0] = {**reservation, **result['session']}
            return current[0]

        gateway = PreparationGateway()
        owner = NativeProjectPreparation(reservation=reservation, frame=frame, initial_target=None,
            on_prepared=prepared, gateway=gateway, current_target=lambda: current[0],
            record_raw=lambda *args: receipts.append(args), refresh_saved=lambda value: None,
            allow_simulator=True, expected_process_create_time=frame['expected_process_create_time'])
        owner._execution_lock = guard
        original = native.NativeWorkspaceExecution._execute

        async def base_hook(self, name, args, **kwargs):
            assert guard.depth == 1
            if name == 'initialize_ld_program':
                return {'ok': True, 'element_input': {'native_readback_verified': True},
                        'ld': {'decoded': {'rows': [{'visual_row': 0, 'row_header': {
                            'opcode': '67', 'coordinate': 0}, 'records': []}]}}}
            if name == 'native_editor' and args.get('action') == 'apply_ld_edits':
                assert args['entries'][0]['rungs'] == rungs
                return {'ok': True}
            delegated.append((name, dict(kwargs)))
            return await original(self, name, args, **kwargs)

        monkeypatch.setattr(native.NativeWorkspaceExecution, '_execute', base_hook)
        monkeypatch.setattr(native, 'saved_project_observation', lambda path, **kwargs: dict(saved))
        broker = MCPExecutionBroker(gateway)
        conversation_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
        broker.bind_native_run(conversation_id, run_id, owner)

        async def event(kind, payload):
            events.append((kind, payload))

        async def dispatch(name, args, call_id):
            request = ExternalToolRequestV1(request_id=str(uuid.uuid4()), conversation_id=conversation_id,
                run_id=run_id, tool_name=name, arguments=args, tool_call_id=call_id)
            try:
                return await broker.execute_external_tool_request(request, sink=event)
            finally:
                assert owner._diagnostic_call_context == {} and guard.depth == 0

        pending = Path(reservation['project_path']).parent.parent / 'native-preparation.json'
        original_reservation = pending.read_bytes()
        cli_calls = []
        def unresolved(command, request, **kwargs):
            cli_calls.append(command)
            assert kwargs['authorization_context'] == 'read_only'
            return {'ok': False, 'input_state_unknown': True, 'creation_input_attempted': True,
                    'automatic_replay': False, 'error': {'kind': 'unresolved'}}
        monkeypatch.setattr(native_preparation, 'run_xg5000_cli', unresolved)
        inspected = await dispatch('inspect_project_preparation', arguments, 'inspect-reserved')
        assert inspected == {'ok': True, 'read_only': True, 'outcome': 'not_dispatched',
            'preparation_state': 'reserved', 'prepared': False, 'creation_input_attempted': False,
            'input_state_unknown': False, 'destination_absent': True, 'reserved_project_files_absent': True,
            'prepare_available': True, 'fresh_attempt_required': True, 'automatic_replay': False,
            'creation_replayed': False}
        assert pending.read_bytes() == original_reservation and cli_calls == []
        assert not owner.prepared and gateway.creations == 0
        assert 'prepare_project' in [tool.tool_name for tool in owner.tool_catalog()]
        rungs = [{'branches': [[{'kind': 'NO', 'operand': 'P00000'}]],
                  'terminal': {'kind': 'APP', 'text': 'TON T000 10'}},
                 {'branches': [[{'kind': 'NO', 'operand': 'T000'}]],
                  'terminal': {'kind': 'OUT', 'operand': 'P00020'}}]
        for invalid in ([{'branches': [], 'terminal': {'kind': 'OUT', 'operand': 'P00020'}}],
                        rungs + [{'branches': [[]], 'terminal': {'kind': 'APP', 'text': 'END'}}],
                        [{'branches': [[]], 'terminal': {'kind': 'APP', 'text': 'TON T000 10'}}]):
            rejected = await dispatch('prepare_project', {**arguments, 'rungs': invalid}, 'invalid-rungs')
            assert rejected['ok'] is False and rejected['action_attempted'] is False
            assert rejected['input_state_unknown'] is False and rejected['status'] == 'not_started'
            assert rejected['error']['nativeFailure']['details']['phase'] == 'preflight'
            assert not owner.prepared and gateway.creations == 0

        failure = {'ok': False, 'creation_input_attempted': False, 'input_state_unknown': False,
            'error': {'kind': 'NATIVE_PREPARATION_CHOICE_UNAVAILABLE', 'details': {
                'choices': [f'CPU-{index}' for index in range(11)]}},
            'preparation_receipts': [{'action': 'set_field', 'receipt': {
                'action_attempted': True, 'input_events_inserted': 2}, 'private_destination': 'private'}]}
        def setup_failure(command, request, **kwargs):
            cli_calls.append(command)
            assert command == 'prepare-project' and kwargs['authorization_context'] == 'approved_change'
            return failure
        monkeypatch.setattr(native_preparation, 'run_xg5000_cli', setup_failure)
        assert native_preparation.prepare_native_workspace({**reservation, **frame, **arguments}) == failure
        failed_journal = pending.read_bytes()
        inspected_failure = await dispatch('inspect_project_preparation', arguments, 'inspect-setup-failed')
        assert inspected_failure['ok'] is True and inspected_failure['preparation_state'] == 'setup_failed'
        assert inspected_failure['prepare_available'] is True and inspected_failure['automatic_replay'] is False
        assert inspected_failure['creation_input_attempted'] is False and inspected_failure['input_state_unknown'] is False
        assert inspected_failure['failure']['error']['kind'] == failure['error']['kind']
        assert inspected_failure['failure']['native_choices'] == failure['error']['details']['choices']
        assert inspected_failure['failure']['preparation_receipts'] == [
            {'action': 'set_field', 'action_attempted': True, 'input_events_inserted': 2}]
        assert 'private' not in json.dumps(inspected_failure)
        assert pending.read_bytes() == failed_journal and cli_calls == ['prepare-project']
        assert not owner.prepared and gateway.creations == 0

        cli_calls.clear()
        monkeypatch.setattr(native_preparation, 'run_xg5000_cli', unresolved)
        unknown = native_preparation.reserve_native_workspace(str(uuid.uuid4()), str(uuid.uuid4()),
            'project', display_name='Unknown fixture')
        unknown_pending = Path(unknown['project_path']).parent.parent / 'native-preparation.json'
        for phase in ('unknown', 'dispatched'):
            retained = {'reservation': unknown, 'state': phase, 'result': {'creation_input_attempted': True}}
            native_preparation._write_manifest(unknown_pending, retained)
            params = {**unknown, **frame, **arguments}
            observed = native_preparation.inspect_native_preparation(params)
            assert observed['input_state_unknown'] is True and 'prepare_available' not in observed
            after = json.loads(unknown_pending.read_text(encoding='utf-8'))
            assert after['state'] == phase and after['result'] == retained['result']
            assert native_preparation.prepare_native_workspace(params)['input_state_unknown'] is True
        assert cli_calls == ['inspect-native-preparation', 'inspect-native-preparation']
        constructed = await dispatch('prepare_project', {**arguments, 'rungs': rungs}, 'prepare')
        assert constructed['ok'] is True, json.dumps(constructed.get('construction'))
        assert (await dispatch('prepare_project', arguments, 'repeat-prepare'))['already_prepared'] is True
        assert gateway.creations == 1
        assert (await dispatch('native_editor', {'action': 'observe'}, 'editor'))['ok'] is True
        result = await dispatch('execute_simulation_scenario', {'loaded_sha256': saved['sha256'],
            'stop_on_mismatch': True, 'steps': [{'case_label': 'read', 'action': 'read_devices',
                'devices': [{'area': 'M', 'bit': 10}], 'expected': [{'area': 'M', 'bit': 10, 'value': False}]}]}, 'scenario')
        assert result['batch']['all_native_steps_completed'] and result['batch']['expectations_matched'], json.dumps(result)
        assert ('simulator', {'expected_simulator_sha256': saved['sha256'], 'persist_observation': True}) in delegated
        before = list(gateway.calls)
        async with guard:
            with pytest.raises(MCPToolError, match='Only an owned pending-close read'):
                await owner._execute('native_editor', {'action': 'observe'}, persist_observation=False)
        assert gateway.calls == before
        assert delegated[-1][1]['persist_observation'] is False
        assert [context['call_id'] for context in contexts] == ['inspect-reserved', 'inspect-setup-failed', 'prepare', 'editor', 'scenario']
        assert gateway.calls.count('workspace.prepare_native') == 1

    try:
        asyncio.run(scenario())
    finally:
        temporary.cleanup()


class Gateway(MCPGateway):
    def __init__(self, root):
        super().__init__()
        self.root, self.creations = root, 0

    @property
    def status(self):
        return MCPStatus(status="ready")

    async def call_read_tool(self, name, arguments=None):
        assert name == "xg5000.instances"
        return {"instances": [{"pid": 501, "hwnd": 502, "process_create_time": 123.0}]}

    async def call_workspace_tool(self, name, arguments):
        if name == "workspace.reserve_native":
            path = self.root / arguments["workspace_id"] / "working" / f"{arguments['project_name']}.xgwx"
            path.parent.mkdir(parents=True)
            return {**arguments, "schema_version": "1", "project_path": str(path)}
        assert name == "workspace.prepare_native"
        self.creations += 1
        path = self.root / arguments["workspace_id"] / "working" / f"{arguments['project_name']}.xgwx"
        xml = f'<Project><Configuration><Program>{arguments["program_name"]}<Body><LDRoutine/></Body></Program></Configuration></Project>'.encode()
        path.write_bytes(b"test native project" + gzip.compress(xml))
        saved_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        captured = path.parent.parent / "source" / path.name
        captured.parent.mkdir()
        captured.write_bytes(path.read_bytes())
        provenance = {"schema": "controlstudio-native-creation-provenance/v1", "workspace_id": arguments["workspace_id"],
                      "creation_receipt_digest": "b" * 64, "saved_project_sha256": saved_hash}
        provenance["receipt_digest"] = digest(provenance)
        workspace = {"workspace_id": arguments["workspace_id"], "revision_id": arguments["revision_id"],
            "source_kind": "new_build", "vendor": "ls", "engineering_system": "XG5000", "created_at": datetime.now(timezone.utc).isoformat(),
            "source": {"original_path": str(path), "captured_path": str(captured), "sha256": saved_hash,
                       "original_state": "unchanged", "original_current_sha256": saved_hash},
            "working_copy": {"path": str(path), "baseline_sha256": saved_hash, "current_sha256": saved_hash, "state": "clean"},
            "provenance": provenance}
        proof = {"pid": arguments["pid"], "main_hwnd": arguments["hwnd"], "project_path": str(path),
                 "project_hash": saved_hash, "process_create_time": 123.0}
        session = {"session_id": "native-session-1", "pid": arguments["pid"], "hwnd": arguments["hwnd"],
                   "project_path": str(path), "project_hash": saved_hash, "metadata": {
                   "project_identity_verified_against_selected_pid": True, "project_identity_assurance": "strong_readback",
                   "project_identity_proof": proof}}
        return {"ok": True, "workspace": workspace, "session": session, "program_name": arguments["program_name"],
                "program_names": [arguments["program_name"]], "saved_project": {"sha256": saved_hash, "exists": True,
                "size": path.stat().st_size, "stable_during_read": True}}


class RuntimeFactory:
    def __init__(self, fail_after_creation=False):
        self.calls, self.synced, self.fail_after_creation = [], 0, fail_after_creation

    def __call__(self, settings, broker):
        factory = self
        class Runtime:
            def bind_knowledge_snapshot(self, callback):
                factory.synced += 1

            async def run(self, **kwargs):
                factory.calls.append(kwargs)
                native = kwargs["trusted_context"].get("native_ld_execution")
                if native is None:
                    return AgentRunOutput(content="추가 설비 조건을 확인해 주세요.", model=settings.model,
                        workflow="new_project_planning", input_tokens=10, output_tokens=8)
                checkpoint = kwargs.get("native_checkpoint") or {"schemaVersion": "1", "public_state": {
                    "model_prompt": json.dumps({"request": kwargs["prompt"], "binding": native}),
                    "completed_pairs": [], "records": []}, "private_state": {"transport_records": []}}
                if native.get("preparation"):
                    result = await broker.execute_external_tool_request(ExternalToolRequestV1(
                        request_id=str(uuid.uuid4()), conversation_id=kwargs["conversation_id"], run_id=kwargs["run_id"],
                        tool_name="prepare_project", tool_call_id="prepare-1", arguments={"cpu_series": "XGK",
                        "cpu_type": "XGK-CPUH", "plc_name": "PLC", "program_name": "Main"}), sink=kwargs["on_tool_event"])
                    assert result["ok"] is True
                    checkpoint["public_state"]["completed_pairs"].append({"results": [{"tool_call_id": "prepare-1", "value": result}]})
                canonical, checkpoint_digest = validated_native_checkpoint(checkpoint)
                await kwargs["on_native_checkpoint"](canonical, checkpoint_digest)
                if native.get("preparation") and factory.fail_after_creation:
                    factory.fail_after_creation = False
                    raise RuntimeError("model interrupted after the completed preparation receipt")
                return AgentRunOutput(content="native 요청 완료", model=settings.model, workflow="native_ld_partial_edit",
                                      input_tokens=10, output_tokens=8)
        return Runtime()


def make_app(root, factory):
    store = HostStore(root / "host.sqlite3")
    knowledge = KnowledgeStore(root / "knowledge.sqlite3")
    gateway = Gateway(root / "workspaces")
    settings = StoredModelConnection(protocol="openai_chat_completions", base_url="http://127.0.0.1:9999/v1",
        model="gpt-6-luna", timeout_seconds=3600, updated_at=datetime.now(timezone.utc),
        capability_verified_at=datetime.now(timezone.utc), reasoning_effort="max", secrets=ModelSecrets(None, {}))
    config = HostConfig(database_path=store.database_path, knowledge_database_path=root / "knowledge.sqlite3")
    app = create_app(config=config, store=store, knowledge_store=knowledge, mcp=gateway,
                     model_repository=SimpleNamespace(get=lambda: settings), runtime_factory=factory)
    token = "test-browser-session"
    app.state.browser_sessions[token] = datetime.now(timezone.utc) + timedelta(hours=1)
    headers = {"Authorization": "Bearer " + token, "Origin": config.canonical_origin,
        "x-controlstudio-csrf": hmac.new(config.launch_token.encode(), BROWSER_CSRF_PREFIX + token.encode(), hashlib.sha256).hexdigest()}
    return app, store, gateway, headers


BRIEF = {"projectName": "Test Project", "machinePurpose": "문서화한 설비의 LD 제어", "sequence": "",
         "hardwareAndIo": "", "safetyAndInterlocks": "", "operatingModes": "",
         "performanceAndAcceptance": "", "attachmentSourceIds": []}


async def complete_runs(app):
    tasks = list(app.state.run_tasks.values())
    if tasks:
        await asyncio.gather(*tasks)


def test_planning_advice_without_artifact_completes(tmp_path):
    async def scenario():
        factory = RuntimeFactory()
        app, store, gateway, headers = make_app(tmp_path, factory)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8767", headers=headers) as client:
            created = await client.post("/api/v1/conversations", json={"workflow": "new_project_planning", "projectBrief": BRIEF})
            assert created.status_code == 201, created.text
            conversation = created.json()["conversationId"]
            response = await client.post(f"/api/v1/conversations/{conversation}/messages", json={
                "content": "계획 상담", "operationId": "planning-001", "workflow": "new_project_planning"})
            assert response.status_code == 202, response.text
            await complete_runs(app)
            assert store.get_run(response.json()["runId"]).state is RunState.COMPLETED
            assert store.get_conversation(conversation).messages[-1].content == "추가 설비 조건을 확인해 주세요."
            assert gateway.creations == 0
    asyncio.run(scenario())


def test_new_project_plan_is_published_before_preparation_and_not_duplicated(tmp_path):
    import pytest
    from controlstudio_host.store import EngineeringMissionConflict

    store = HostStore(tmp_path / 'host.sqlite3')
    conversation = store.create_conversation()
    prompt = '환기팬 계획을 세우고 LD 작성과 시뮬레이션까지 해줘.'
    plan = '구현 계획\n시작·정지·과부하 입력으로 자기유지를 구성하고 팬 출력을 제어한다.\n검사·저장 후 기동·유지·정지·과부하 정지를 시험한다.'
    message, run, _ = store.create_user_message_and_run(conversation_id=conversation.id,
        content=prompt, operation_id='plan-before-preparation',
        message_metadata={'change_request_scope': 'analysis_only'},
        run_metadata={'change_request_scope': 'analysis_only'})
    handoff = {'schema_version': '1', 'conversation_id': conversation.id, 'run_id': run.id,
        'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
        'project_name': 'VentilationFan', 'simulate': True}
    arguments = dict(conversation_id=conversation.id, run_id=run.id,
        request_message_id=message.id, prompt=prompt, handoff=handoff,
        project_brief={'project_name': 'VentilationFan'}, usage={'input_tokens': 10, 'output_tokens': 20},
        public_plan=plan)
    store.consume_unbound_project_start(**arguments)
    with store._connection() as connection:
        messages = connection.execute('SELECT * FROM messages WHERE conversation_id = ? ORDER BY sequence',
            (conversation.id,)).fetchall()
        binding = connection.execute('SELECT workspace_id FROM conversations WHERE id = ?',
            (conversation.id,)).fetchone()
    assert [row['content'] for row in messages] == [prompt, plan]
    assert json.loads(messages[1]['metadata_json'])['phase'] == 'implementation_plan'
    assert binding['workspace_id'] is None and store.get_run(run.id).state == RunState.ANALYZING
    events = store.list_events(conversation_id=conversation.id)
    published = next(event for event in events if event.payload.get('message_id') == messages[1]['id'])
    selected = next(event for event in events if event.event_type == 'conversation.native_project_start_selected')
    assert published.sequence < selected.sequence and selected.payload['creation_input_attempted'] is False
    with pytest.raises(EngineeringMissionConflict):
        store.consume_unbound_project_start(**arguments)
    assert len(store.list_events(conversation_id=conversation.id)) == len(events)


def test_new_preparation_revalidates_retained_loaded_frame_after_restart(tmp_path):
    async def scenario(root, variant):
        root.mkdir()
        source_gateway = Gateway(root / 'workspaces')
        old_id = str(uuid.uuid4())
        (root / 'workspaces' / old_id / 'working').mkdir(parents=True)
        old = await source_gateway.call_workspace_tool('workspace.prepare_native', {
            'workspace_id': old_id, 'revision_id': str(uuid.uuid4()), 'project_name': 'old',
            'pid': 501, 'hwnd': 502, 'program_name': 'Main'})
        workspace, session = old['workspace'], old['session']
        seeded = HostStore(root / 'host.sqlite3')
        seeded.set_host_state('active_workspace', workspace)
        key = digest({field: workspace[field] for field in ('workspace_id', 'revision_id')})
        seeded.set_host_state('native_workspace_bindings:v1', {key: {
            'workspace_id': workspace['workspace_id'], 'revision_id': workspace['revision_id'], 'session': session}})
        factory = RuntimeFactory()
        app, store, gateway, headers = make_app(root, factory)
        file_target = {'workspace_id': workspace['workspace_id'], 'revision_id': workspace['revision_id'],
            'source_kind': workspace['source_kind'], 'provenance_receipt_digest': workspace['provenance']['receipt_digest'],
            'source_sha256': workspace['source']['sha256'], 'working_path': workspace['working_copy']['path'],
            'working_sha256': workspace['working_copy']['current_sha256']}
        gateway.bind_agent_workspace(file_target)
        original_workspace_call = gateway.call_workspace_tool
        calls = []
        async def workspace_call(name, arguments):
            if name == 'workspace.prepare_native':
                calls.append(dict(arguments))
                if variant != 'valid':
                    return {'ok': False, 'error': {'code': 'NATIVE_PREPARATION_IDLE_FRAME_REQUIRED'},
                            'action_attempted': False, 'input_state_unknown': False}
            return await original_workspace_call(name, arguments)
        async def read(name, arguments=None):
            if name == 'xg5000.instances':
                return {'instances': [{'pid': 501 if variant != 'wrong_frame' else 503,
                    'hwnd': 502, 'process_create_time': 123.0, 'visible': True}]}
            assert name == 'xg5000.attach'
            return {'session': session}
        gateway.call_workspace_tool = workspace_call
        gateway.call_read_tool = read
        if variant == 'changed_file':
            Path(workspace['working_copy']['path']).write_bytes(b'changed')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                base_url='http://127.0.0.1:8767', headers=headers) as client:
            created = await client.post('/api/v1/conversations', json={'workflow': 'new_project_planning', 'projectBrief': BRIEF})
            conversation = created.json()['conversationId']
            reservation = await gateway.call_workspace_tool('workspace.reserve_native', {
                'workspace_id': str(uuid.uuid4()), 'revision_id': str(uuid.uuid4()),
                'project_name': 'project', 'display_name': 'Test Project'})
            store.set_host_state('native_project_preparation:' + conversation, {
                'reservation': reservation, 'initial_target': {**file_target,
                    **({'provenance_receipt_digest': 'f' * 64} if variant == 'changed_provenance' else {})},
                'frame': {'pid': 501, 'hwnd': 502, 'expected_process_create_time': 123.0}})
            response = await client.post(f'/api/v1/conversations/{conversation}/messages', json={
                'content': '작성', 'projectBrief': BRIEF, 'operationId': 'prepare-001',
                'workflow': 'new_project_planning', 'changeRequestScope': 'permanent_offline_revision'})
            assert response.status_code == 202, response.text
            await complete_runs(app)
            if variant == 'valid':
                assert len(calls) == 1
                assert calls[0]['previous_project_path'] == workspace['working_copy']['path']
                assert calls[0]['previous_project_hash'] == workspace['working_copy']['current_sha256']
            else:
                assert all('previous_project_path' not in call for call in calls)
    async def run():
        for variant in ('valid', 'wrong_frame', 'changed_file', 'changed_provenance'):
            await scenario(tmp_path / variant, variant)
    asyncio.run(run())


def test_creation_resume_and_followup_keep_one_conversation_and_completed_results(tmp_path):
    async def scenario():
        factory = RuntimeFactory(fail_after_creation=True)
        app, store, gateway, headers = make_app(tmp_path, factory)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8767", headers=headers) as client:
            created = await client.post("/api/v1/conversations", json={"workflow": "new_project_planning", "projectBrief": BRIEF})
            assert created.status_code == 201, created.text
            conversation = created.json()["conversationId"]
            response = await client.post(f"/api/v1/conversations/{conversation}/messages", json={"content": "작성", "projectBrief": BRIEF,
                "operationId": "creation-001", "workflow": "new_project_planning", "changeRequestScope": "permanent_offline_revision"})
            assert response.status_code == 202, response.text
            await complete_runs(app)
            first = store.get_run(response.json()["runId"])
            assert first.state is RunState.FAILED
            detail = (await client.get(f"/api/v1/conversations/{conversation}")).json()
            assert detail["workflow"] == "engineering" and detail["nativeExecution"]["resumable"] is True
            assert detail["messages"][-1]["runState"] == "failed"
            assert detail["requestScope"] == "permanent_offline_revision"
            resumed = await client.post(f"/api/v1/conversations/{conversation}/native-execution/resume",
                json={"operationId": "resume-001", "sourceRunId": first.id})
            assert resumed.status_code == 202, resumed.text
            await complete_runs(app)
            assert store.get_run(resumed.json()["runId"]).state is RunState.COMPLETED
            assert (await client.get(f"/api/v1/conversations/{conversation}")).json()["messages"][-1]["runState"] == "completed"
            assert factory.calls[-1]["native_checkpoint"]["public_state"]["completed_pairs"][0]["results"][0]["tool_call_id"] == "prepare-1"
            replay = await client.post(f"/api/v1/conversations/{conversation}/native-execution/resume",
                json={"operationId": "resume-001", "sourceRunId": first.id})
            assert replay.status_code == 202 and replay.json()["runId"] == resumed.json()["runId"]
            other_token = "other-browser-session"
            app.state.browser_sessions[other_token] = datetime.now(timezone.utc) + timedelta(hours=1)
            other_headers = {"Authorization": "Bearer " + other_token,
                "x-controlstudio-csrf": hmac.new(app.state.config.launch_token.encode(),
                    BROWSER_CSRF_PREFIX + other_token.encode(), hashlib.sha256).hexdigest()}
            unauthorized_replay = await client.post(f"/api/v1/conversations/{conversation}/native-execution/resume",
                json={"operationId": "resume-001", "sourceRunId": first.id}, headers=other_headers)
            assert unauthorized_replay.status_code == 409
            followup = await client.post(f"/api/v1/conversations/{conversation}/messages", json={"content": "정지 조건을 보완해 주세요",
                "operationId": "followup-001", "workflow": "engineering", "changeRequestScope": "permanent_offline_revision"})
            assert followup.status_code == 202, followup.text
            await complete_runs(app)
            assert store.get_run(followup.json()["runId"]).state is RunState.COMPLETED
            restored = factory.calls[-1]["native_checkpoint"]["public_state"]
            assert json.loads(restored["model_prompt"])["request"] == "정지 조건을 보완해 주세요"
            assert len(restored["completed_pairs"]) == 1 and gateway.creations == 1
            events = store.list_events(conversation_id=conversation)
            preparation = next(event for event in events if event.event_type == "tool.completed")
            assert preparation.payload["native_result"]["programName"] == "Main"
            assert "pid" not in preparation.payload["native_result"]
            assert Path(gateway.agent_session_identity_snapshot["project_path"]).name == "Test_Project.xgwx"
            assert store.get_conversation(conversation).project_brief["project_name"] == "Test Project"
            assert any(event.event_type == "host.status" and event.run_id == first.id for event in events)
    asyncio.run(scenario())
