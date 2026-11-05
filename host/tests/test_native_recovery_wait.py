import ast
import asyncio
import ctypes
import hashlib
import json
import math
import runpy
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import pytest

from controlstudio_host import native_recovery_wait as recovery
from controlstudio_host import native_workspace_host
from controlstudio_host.store import HostStore
from controlstudio_host.models import RunState
from controlstudio_host.api_models import ConversationNativeExecutionV1, LocalWorkspaceV1
from controlstudio_host.agent_protocol import native_checkpoint_boundary
from controlstudio_host.mcp_client import MCPToolError


def test_healthy_recovery_accepts_verified_inactive_navigation_without_relaunch(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from controlstudio_host import app as host_app
    monkeypatch.setattr(host_app, '_engineering_session_process_create_time', lambda session: 123.0)
    anchor = {'workspace_id': 'w', 'revision_id': 'r', 'pid': 7, 'hwnd': 8,
              'project_path': 'C:/fixture.xgwx', 'process_create_time': 123.0}
    workspace = SimpleNamespace(working_copy=SimpleNamespace(current_sha256='a' * 64))
    observed = {'ok': True, 'action': 'observe', 'load_state': 'ended', 'read_only': False, 'action_attempted': True,
        'simulator_control_action_attempted': False, 'input_state_unknown': False,
        'end_reconciled_without_simulator_control': True, 'current_session_inactive_verified': True,
        'current_session_observation': {'current_session_inactive_verified': True},
        'navigation_input': {'attempted': True, 'outcome': 'opened', 'close_outcome': 'closed',
                             'control_action_attempted': False},
        'saved_project': {'stable_during_read': True, 'sha256': 'a' * 64},
        'current_constraints': {'schema': 'xg5000-simulator-current-constraints/v1',
            'evidence_scope': 'current_binding_journal_snapshot', 'start_behavior': 'fresh_native_preflight',
            'start_journal_blocker': None, 'journal_state': 'ended'}}
    async def observe(name, arguments):
        assert (name, arguments) == ('simulator', {'action': 'observe'})
        return observed
    async def revalidate(value):
        return {'pid': 7, 'hwnd': 8, 'project_path': anchor['project_path'], 'project_hash': 'a' * 64}
    owner = SimpleNamespace(allow_simulator=True, recovery_only=False, recovery_pending=False,
                            operation_recoveries={}, _execute=observe)
    async def run():
        result = await host_app._healthy_current_recovery(owner, None, anchor, workspace,
            'generation', revalidate, lambda _: 'generation')
        assert result['relaunch_not_required'] and not result['rebound']
        assert result['current_request_input_attempted'] is True
        observed['navigation_input']['close_outcome'] = 'unknown'
        assert await host_app._healthy_current_recovery(owner, None, anchor, workspace,
            'generation', revalidate, lambda _: 'generation') is None
        from controlstudio_contracts.native_operation_continuation import SCHEMA, operation_constraints_digest
        generation = 'host_verified_native_binding/v2:' + 'b' * 64
        owner._diagnostic_call_context = {'call_id': 'recover', 'request_id': 'request'}
        constraints = {'schema': 'xg5000-simulator-current-constraints/v1',
            'evidence_scope': 'current_binding_journal_snapshot',
            'start_behavior': 'blocked_by_current_journal_unknown',
            'start_journal_blocker': 'SIMULATOR_END_OUTCOME_UNKNOWN', 'journal_state': 'end_outcome_unknown'}
        continuation = {'schema': SCHEMA, 'provenance': 'host_current_operation_continuation/v1',
            'source_tool_name': 'simulator', 'source_call_id': 'recover', 'host_request_id': 'request',
            'constraints_schema': constraints['schema'], 'constraints_sha256': operation_constraints_digest(constraints),
            'admitted_generation': generation, 'saved_sha256': 'a' * 64, 'route_tool_name': 'simulator',
            'native_input_authority': False, 'original_outcomes_preserved': True}
        observed.clear(); observed.update({'ok': True, 'action': 'observe', 'load_state': 'end_outcome_unknown',
            'saved_project': {'stable_during_read': True, 'sha256': 'a' * 64}, 'current_constraints': constraints,
            'current_operation_continuation': continuation, 'end_reconciliation': {
                'explicit_action': 'end', 'purpose': 'verify_current_session_inactive', 'simulator_control_replay': False}})
        result = await host_app._healthy_current_recovery(owner, None, anchor, workspace,
            generation, revalidate, lambda _: generation)
        assert result['relaunch_not_required'] and result['rebound'] is False
        assert result['simulator_observation']['current_operation_continuation'] == continuation
        assert result['current_request_input_attempted'] is False and result['automatic_replay'] is False
        observed['current_operation_continuation'] = {**continuation, 'source_call_id': 'other'}
        assert await host_app._healthy_current_recovery(owner, None, anchor, workspace,
            generation, revalidate, lambda _: generation) is None
    asyncio.run(run())


def test_new_saved_barrier_preserves_known_rejection_and_never_replays_unknown_close(monkeypatch):
    import uuid
    from controlstudio_contracts.native_failure import build_native_failure
    source = Path(__file__).parents[1] / 'src/controlstudio_host/app.py'
    node = next(node for node in ast.walk(ast.parse(source.read_text(encoding='utf-8')))
                if isinstance(node, ast.AsyncFunctionDef) and node.name == 'recover_native_workspace_operation')
    class Globals(ast.NodeTransformer):
        def visit_Nonlocal(self, node):
            return ast.copy_location(ast.Global(names=node.names), node)
    module = ast.Module(body=[Globals().visit(node)], type_ignores=[])
    ast.fix_missing_locations(module)
    binding = {'workspace_id': 'w', 'revision_id': 'r', 'session_id': 's', 'pid': 501,
               'hwnd': 502, 'project_path': 'working.xgwx', 'process_create_time': 123.0}
    no_input = build_native_failure('CanonicalRequestError', 'deadline before close binding', {
        'action_attempted': False, 'input_state_unknown': False, 'source_closed': False,
        'phase': 'backend_import_completed'})
    previous = {'operation_id': 'old-operation', 'source_run_id': 'old-run', 'conversation_id': 'conversation',
        'binding': deepcopy(binding), 'phase': 'saved', 'saved_sha256': 'a' * 64,
        'saved_result': {'sha256': 'a' * 64}, 'close_rejection': no_input,
        'completed_operations': [{'operation_id': 'earlier-completed', 'phase': 'rebound'}]}
    state, calls = deepcopy(previous), []
    class Store:
        def get_host_state(self, key):
            assert key == 'recovery'
            return deepcopy(state)
        def set_host_state(self, key, value):
            assert key == 'recovery'
            state.clear(); state.update(deepcopy(value))
    workspace = SimpleNamespace(workspace_id='w', revision_id='r',
        source=SimpleNamespace(original_state='unchanged', original_current_sha256='source', sha256='source'),
        working_copy=SimpleNamespace(path='working.xgwx', current_sha256='b' * 64))
    async def status(name, arguments):
        assert (name, arguments) == ('workspace.status', {'workspace_id': 'w'})
        return workspace
    async def primitive(name, arguments):
        assert arguments == {}
        calls.append(name)
        if name == 'save_project':
            return {'ok': True}
        assert name == 'read_saved_project'
        return {'ok': True, 'stable_during_read': True, 'sha256': 'b' * 64}
    async def close(*args, before_dispatch, **kwargs):
        calls.append('close')
        before_dispatch()
        assert state['saved_sha256'] == 'b' * 64 and state['operation_id'] != previous['operation_id']
        # This fake normal-close receipt preserves a dirty editor; no actual UI is used.
        return {'source_closed': False, 'discardUnsavedChanges': False, 'unsavedChangesDiscarded': False}
    namespace = {'__package__': 'controlstudio_host', 'Any': object, 'deepcopy': deepcopy,
        'math': math, 'uuid': uuid, 'Path': Path, 'MCPToolError': MCPToolError,
        'store': Store(), 'conversation_id': 'conversation', 'run_id': 'new-explicit-run',
        'active_workspace': workspace, 'active_engineering_session': {}, 'target': deepcopy(binding),
        '_native_workspace_recovery_state_key': lambda *args: 'recovery',
        '_normalized_workspace_path': lambda value: value,
        '_local_workspace_v1': lambda value: value, '_workspace_resume_stable_identity': lambda value: 'same',
        'mcp': SimpleNamespace(agent_session_identity_snapshot=deepcopy(binding), call_workspace_tool=status),
        'publish_workspace_recovery_scope': lambda *args: None,
        '_close_existing_workspace_for_fresh_reopen': close}
    exec(compile(module, str(source), 'exec'), namespace)
    executor = SimpleNamespace(target=deepcopy(binding), expected_process_create_time=123.0,
        _check_target=lambda: None, _execute=primitive)
    async def scenario():
        result = await namespace['recover_native_workspace_operation'](executor)
        assert result['phase'] == 'saved' and result['input_state_unknown'] is False
        assert calls == ['save_project', 'read_saved_project', 'read_saved_project', 'close']
        assert state['source_run_id'] == 'new-explicit-run' and state['saved_sha256'] == 'b' * 64
        assert state['completed_operations'] == [previous['completed_operations'][0],
            {key: value for key, value in previous.items() if key != 'completed_operations'}]
        assert previous['close_rejection'] == no_input and previous['saved_sha256'] == 'a' * 64
        # A stale saved barrier without positive no-input proof is not renewable.
        for proof in (None, build_native_failure('CloseUnknown', 'outcome unknown', {
                'input_state_unknown': True, 'source_closed': False})):
            state.clear(); state.update(deepcopy(previous)); calls.clear()
            state['close_rejection'] = proof
            failed = await namespace['recover_native_workspace_operation'](executor)
            assert failed['ok'] is False and calls == ['read_saved_project']
            assert state['operation_id'] == 'old-operation' and state['saved_sha256'] == 'a' * 64
            assert state['completed_operations'] == previous['completed_operations']
        state.clear(); state.update(deepcopy(previous)); calls.clear()
        state.update(phase='close_pending', source_run_id='new-explicit-run',
            close_result={'input_state_unknown': True, 'dispatch_state': 'dispatched'})
        ambiguous = deepcopy(state)
        pending = await namespace['recover_native_workspace_operation'](executor)
        assert pending['input_state_unknown'] is True and state['phase'] == 'close_pending'
        assert calls == [] and state['operation_id'] == ambiguous['operation_id']
        assert state['close_result'] == ambiguous['close_result']
        assert state['saved_sha256'] == ambiguous['saved_sha256']
        assert state['completed_operations'] == ambiguous['completed_operations']
    asyncio.run(scenario())


def test_public_resume_rejoins_exact_parked_agent_launch_without_new_input():
    import contextvars
    import logging
    import os
    import sqlite3
    import uuid
    from contextlib import asynccontextmanager, contextmanager
    from datetime import datetime, timedelta, timezone
    from fastapi import Depends, HTTPException
    from controlstudio_host.api_models import XG5000ProjectLaunchV1
    from controlstudio_host.store import TargetOperationLeaseConflict

    class MemoryStore(HostStore):
        def __init__(self):
            self.instance_id = 'current-host'
            self.connection = sqlite3.connect(':memory:')
            self.connection.row_factory = sqlite3.Row
            self.initialize()
        @contextmanager
        def _connection(self):
            try:
                yield self.connection
            finally:
                # Match closing a real connection: uncommitted work is rolled back.
                if self.connection.in_transaction:
                    self.connection.rollback()
    store = MemoryStore()
    conversation = store.create_conversation(workspace_id='w', revision_id='r', engineering_session_id='old')
    _, source_run, _ = store.create_user_message_and_run(
        conversation_id=conversation.id, content='recover', operation_id='source-run')
    path = str((Path(__file__).resolve().parent / 'uncreated-working.xgwx').resolve())
    target = {'operation_id': 'original-operation', 'source_run_id': source_run.id,
        'workspace_id': 'w', 'revision_id': 'r', 'project_path': path, 'project_hash': 'a' * 64}
    lease = store.acquire_target_operation_lease(operation_id=target['operation_id'],
        owner_kind='workspace_resume', holder_token='previous-host', target_identity=target)
    def digest(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    scope = {'target_lease_id': lease['id'], **target}
    owner = {'owner_ref': digest(scope), 'scope': scope}
    frame = {'pid': 701, 'hwnd': 702, 'process_create_time': 323.0}
    wait_key = 'owned_launch_wait_operation:' + target['operation_id']
    wait = {'owner': owner, 'state': 'launch_unresolved', 'exclusion_lease_id': lease['id'],
        'pending': {**frame, 'launch_operation_id': 'original-native-launch', 'lease_ref': 'original-native-lease'}}
    store.set_host_state(wait_key, wait)
    store.park_target_operation_lease(operation_id=target['operation_id'], holder_token='previous-host', reason='restart')
    recovery_key = 'native_workspace_recovery:v1:' + hashlib.sha256(json.dumps(
        (conversation.id, 'w', 'r'), ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    retained = {'operation_id': target['operation_id'], 'source_run_id': source_run.id,
        'conversation_id': conversation.id, 'phase': 'launch_pending', 'saved_sha256': 'a' * 64,
        'binding': {'workspace_id': 'w', 'revision_id': 'r', 'project_path': path},
        'launch_pending_frame': frame, 'launch_operation_id': 'original-native-launch',
        'last_failure': {'phase': 'close_pending', 'input_state_unknown': True}}
    store.set_host_state(recovery_key, retained)
    workspace = SimpleNamespace(workspace_id='w', revision_id='r',
        working_copy=SimpleNamespace(path=path, current_sha256='a' * 64))
    proof = {'method': 'same_rot_object_project_tree_and_path_v1', 'same_active_object': True,
        'stable_repeat': True, 'pid': 701, 'main_hwnd': 702, 'process_create_time': 323.0,
        'project_hash': 'a' * 64, 'project_path': path}
    session = {'session_id': 'fresh', 'pid': 701, 'hwnd': 702, 'project_path': path, 'project_hash': 'a' * 64,
        'metadata': {'project_identity_verified_against_selected_pid': True,
            'project_identity_assurance': 'controlled_launch_read_only', 'project_identity_proof': proof}}
    raw = {'operation': 'xg5000.open_project', 'verified': True, 'workspace_id': 'w',
        'working_path': path, 'working_hash': 'a' * 64, 'launch_pid': 701, 'process_create_time': 323.0,
        'window_detected': True, 'project_identity_verified_against_selected_pid': False,
        'project_identity_read_only_bound_to_selected_pid': True, 'identity_assurance': 'controlled_launch_native_file_read_only',
        'instance': {'pid': 701, 'hwnd': 702}, 'observed_instances': [], 'detail': 'exact original launch'}
    calls = []
    async def workspace_call(name, arguments, *, private_meta):
        claims = private_meta['controlstudio/launch-binding-wait']
        assert name == 'xg5000.open_project' and claims['action'] == 'observe' and claims['owner'] == owner
        assert arguments['expected_working_hash'] == 'a' * 64
        calls.append('observe-original')
        return deepcopy(raw)
    async def read(name, arguments):
        assert name == 'xg5000.attach' and arguments['pid'] == frame['pid'] and arguments['hwnd'] == frame['hwnd']
        calls.append('attach-original')
        return deepcopy(session)
    async def existing(workspace): return deepcopy(session)
    async def absent(*args, **kwargs): return None
    async def body(): return {'timeoutSeconds': 120}
    async def connected(): return False
    request = SimpleNamespace(url=SimpleNamespace(path='/api/v1/workspaces/w/resume'),
        json=body, is_disconnected=connected)
    source = Path(__file__).parents[1] / 'src/controlstudio_host/app.py'
    names = {'pending_owned_launch_resume', 'complete_owned_launch_resume', 'target_switch_lease',
        'join_owned_workspace_launch', 'complete_workspace_launch', 'open_workspace_in_xg5000',
        'resume_workspace_editor', '_validated_engineering_session_result', '_engineering_session_read_ready',
        '_engineering_session_process_create_time', '_engineering_session_exact_lifetime_binding',
        '_engineering_session_identity_verified', '_native_workspace_recovery_state_key',
        '_close_existing_workspace_for_fresh_reopen', '_validate_reviewed_no_save_close_binding'}
    class Globals(ast.NodeTransformer):
        def visit_Nonlocal(self, node): return ast.copy_location(ast.Global(names=node.names), node)
    nodes = [Globals().visit(node) for node in ast.walk(ast.parse(source.read_text(encoding='utf-8')))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {'__package__': 'controlstudio_host', 'deepcopy': deepcopy, 'Path': Path, 'math': math,
        'os': os, 'json': json, 'hashlib': hashlib, 'uuid': uuid, 'datetime': datetime, 'timezone': timezone,
        'timedelta': timedelta, 'asynccontextmanager': asynccontextmanager, 'asyncio': asyncio, 'Depends': Depends,
        'HTTPException': HTTPException, 'MCPToolError': MCPToolError, 'RunState': RunState,
        'TargetOperationLeaseConflict': TargetOperationLeaseConflict, 'XG5000ProjectLaunchV1': XG5000ProjectLaunchV1,
        'logger': logging.getLogger(__name__), 'store': store, 'engineering_target_run_guard': lambda: None,
        '_canonical_json_digest': digest, '_normalized_workspace_path': lambda value: os.path.normcase(str(Path(value).resolve())),
        'launch_wait_request_context': contextvars.ContextVar('test-original-web-rejoin', default=None),
        'owned_launch_wait_requires_park': recovery.owned_launch_wait_requires_park, 'shutdown_event': asyncio.Event(),
        'LATE_WORKSPACE_RECONCILE_SECONDS': 1, 'active_workspace': None, 'active_engineering_session': None,
        'revalidate_native_workspace_binding': existing, '_find_existing_workspace_session': absent,
        'workspace_candidate_session': lambda value: value, 'bind_engineering_session': lambda value: value,
        '_native_ld_edit_ready': lambda *args: False, 'record_workspace_attach_failure': lambda *args, **kwargs: None,
        'mcp': SimpleNamespace(call_workspace_tool=workspace_call, call_read_tool=read, sign_mutation_authorization=lambda claims: claims)}
    exec(compile(module, str(source), 'exec'), namespace)
    async def resume():
        dependency = namespace['target_switch_lease'](request)
        await anext(dependency)
        try:
            result = await namespace['resume_workspace_editor'](workspace, None, timeout_seconds=120, candidate_only=True)
            namespace['complete_owned_launch_resume'](workspace, result[1])
            return result
        finally:
            await dependency.aclose()
    async def scenario():
        with pytest.raises(HTTPException): await resume()  # The original run is still active.
        assert calls == []
        store.update_run_state(run_id=source_run.id, state=RunState.CANCELLED)
        for field, value in [('launch_pending_frame', {**frame, 'pid': 999}), ('saved_sha256', 'b' * 64)]:
            store.set_host_state(recovery_key, {**retained, field: value})
            with pytest.raises(HTTPException): await resume()
            assert calls == [] and store.active_target_operation_lease()['id'] == lease['id']
        store.set_host_state(recovery_key, retained)
        request.url.path = '/api/v1/workspaces/foreign/resume'
        with pytest.raises(HTTPException): await resume()
        request.url.path = '/api/v1/workspaces/w/resume'
        assert calls == []
        raw['working_hash'] = 'b' * 64
        with pytest.raises(HTTPException): await resume()
        assert calls == ['observe-original', 'attach-original'] and store.get_host_state(recovery_key) == retained
        assert store.active_target_operation_lease()['state'] == 'STALE_BLOCKED'
        raw['working_hash'] = 'a' * 64; calls.clear()
        result = await resume()
        assert result[0].verified is True and calls == ['observe-original', 'attach-original']
        completed = store.get_host_state(recovery_key)
        assert completed['phase'] == 'rebound' and completed['new_binding']['pid'] == frame['pid']
        assert completed['new_binding']['process_create_time'] == frame['process_create_time']
        assert completed['last_failure'] == retained['last_failure'] and completed['binding'] == retained['binding']
        assert store.get_host_state(wait_key)['owner'] == owner and store.get_host_state(wait_key)['state'] == 'ready'
        assert store.active_target_operation_lease() is None

        # A different retained owned workspace is freshly proved and normally
        # closed before the requested workspace lookup/open. The same real
        # public lease and safe-close helper must block dirty/unknown outcomes.
        owned_path = str(Path(path).with_name('other-owned-working.xgwx'))
        owned_workspace = SimpleNamespace(workspace_id='other-w', revision_id='other-r',
            working_copy=SimpleNamespace(path=owned_path, current_sha256='c' * 64))
        owned = {'workspace_id': 'other-w', 'revision_id': 'other-r'}
        owned_proof = {**proof, 'pid': 801, 'main_hwnd': 802, 'process_create_time': 423.0,
            'project_path': owned_path, 'project_hash': 'c' * 64}
        owned_session = {**session, 'session_id': 'fresh-owned', 'pid': 801, 'hwnd': 802,
            'project_path': owned_path, 'project_hash': 'c' * 64,
            'metadata': {**session['metadata'], 'project_identity_proof': owned_proof}}
        closed_bindings = []
        async def revalidate(value):
            if value is owned_workspace:
                calls.append('revalidate-current')
                if case == 'unverified':
                    raise HTTPException(status_code=409, detail='Current ownership not freshly proved')
                return deepcopy(owned_session)
            assert value is workspace
            calls.append('lookup-requested')
            return None
        async def close_current(name, arguments):
            assert name == 'xg5000.reviewed_no_save_close'
            assert store.active_target_operation_lease()['owner_kind'] == 'workspace_resume'
            assert arguments['pid'] == 801 and arguments['hwnd'] == 802
            assert arguments['process_create_time'] == 423.0
            assert arguments['project_path'] == owned_path and arguments['project_hash'] == 'c' * 64
            assert arguments['discard_unsaved_changes'] is False
            calls.append('close-current')
            if case == 'unknown':
                return {'source_closed': False, 'input_state_unknown': True}
            return {'operation': name, 'verified': True, 'source_closed': case != 'dirty',
                'automatic_workspace_open': False, 'process_terminated': False,
                'discardUnsavedChanges': False, 'unsavedChangesDiscarded': False,
                'reviewed_binding': {key: arguments[key] for key in (
                    'pid', 'hwnd', 'process_create_time', 'session_id', 'project_path', 'project_hash')}}
        async def retired(name, arguments):
            assert name == 'xg5000.instances' and arguments == {}
            calls.append('observe-retired')
            return {'instances': []}
        async def open_requested(value, **kwargs):
            assert value is workspace and kwargs['candidate_only'] is True
            assert namespace['active_engineering_session'] is None
            calls.append('open-requested')
            return SimpleNamespace(launch_pid=901), deepcopy(session)
        namespace.update(_local_workspace_v1=lambda value: owned_workspace,
            revalidate_native_workspace_binding=revalidate,
            _close_existing_workspace_for_fresh_reopen=namespace['_close_existing_workspace_for_fresh_reopen'],
            _find_existing_workspace_session=absent, open_workspace_in_xg5000=open_requested,
            status=SimpleNamespace(HTTP_502_BAD_GATEWAY=502),
            update_native_workspace_binding=lambda **value: closed_bindings.append(value),
            execution_broker=SimpleNamespace(clear_agent_session=lambda: calls.append('clear-session')),
            mcp=SimpleNamespace(call_workspace_tool=close_current, call_read_tool=retired))
        for case in ('closed', 'dirty', 'unknown', 'unverified'):
            calls.clear(); closed_bindings.clear()
            namespace['active_workspace'] = deepcopy(owned)
            namespace['active_engineering_session'] = deepcopy(owned_session)
            if case == 'closed':
                await resume()
                assert calls == ['revalidate-current', 'close-current', 'observe-retired',
                    'clear-session', 'lookup-requested', 'open-requested']
                assert closed_bindings == [{'closed_target': (801, 802), 'closed_process_create_time': 423.0}]
            else:
                with pytest.raises(HTTPException):
                    await resume()
                assert calls == (['revalidate-current'] if case == 'unverified' else
                    ['revalidate-current', 'close-current'])
                assert closed_bindings == [] and namespace['active_engineering_session'] == owned_session
            assert namespace['active_workspace'] == owned
            assert store.active_target_operation_lease() is None
    try:
        asyncio.run(scenario())
    finally:
        store.connection.close()


def test_completed_owned_cancel_reconsiders_one_exact_safe_close(monkeypatch, tmp_path):
    # Exercise the actual scoped app callbacks without starting Host or a native process.
    source = Path(__file__).parents[1] / 'src/controlstudio_host/app.py'
    import importlib
    finalization = vars(importlib.import_module('controlstudio_agent.native_finalization'))
    validate_agent=finalization['validate_recovery_continuation']
    names = {'pending_recovery_idle', 'wait_workspace_recovery', 'recover_native_workspace_operation',
             'restore_pending_recovery_owner', 'publish_workspace_recovery_scope',
             'workspace_agent_binding', 'bind_agent_workspace_target', 'recovery_workspace_only_binding',
             '_pending_native_recovery_saved_matches', '_pending_native_recovery_target_anchor',
             'validate_pending_recovery_owned_ui','current_workspace_recovery',
             'recover_native_workspace','admitted_native_workspace_generation'}
    names.update({'_healthy_current_recovery', '_pending_recovery_conversation_target', 'pending_native_recovery_anchor', 'native_resume_source_state_allowed',
        'native_resume_target_matches_checkpoint', '_native_checkpoint_target_matches',
        '_native_resume_latest_attempt_matches_checkpoint', 'get_conversation_v1'})
    nodes = [node for node in ast.walk(ast.parse(source.read_text(encoding='utf-8')))
             if isinstance(node, (ast.AsyncFunctionDef,ast.FunctionDef)) and node.name in names]
    for node in nodes:
        if node.name == 'get_conversation_v1':
            node.decorator_list = []
    class Globals(ast.NodeTransformer):
        def visit_Nonlocal(self, node):
            return ast.copy_location(ast.Global(names=node.names), node)
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
                              *[Globals().visit(node) for node in nodes]], type_ignores=[])
    ast.fix_missing_locations(module)
    binding = {'workspace_id': 'w', 'revision_id': 'r', 'session_id': 's', 'pid': 501,
               'hwnd': 502, 'project_path': 'working.xgwx', 'process_create_time': 123.0}
    identity = {'operation_id': 'op', 'source_run_id': 'run', 'binding': binding, 'saved_sha256': 'a' * 64}
    ui = {'ui_ref': 'ui:cancel', 'ui_kind': 'modal', 'hwnd': 503,
          'pending_recovery_identity': deepcopy(identity)}
    response = {'ok': True, 'action_attempted': True, 'target_closed': True,
                'target_lifecycle': {'target_closed': True, 'post_observation_verified': True},
                'owned_ui_response': {'ui_ref': ui['ui_ref'], 'ui_kind': 'modal', 'requested_response': 'cancel'}}
    state = {**deepcopy(identity), 'conversation_id': 'conversation', 'phase': 'close_pending',
             'last_failure': {'input_state_unknown': True, 'phase': 'close_pending'}}
    original_failure = deepcopy(state['last_failure'])
    scope={k:deepcopy(identity[k]) for k in ('operation_id','binding','saved_sha256')}
    continuation={'operation_id':'op','phase':'close_pending',
                  'provenance':'host_current_recovery_continuation/v1','native_input_authority':False}
    records=[{'key':'native_ld_receipt:run:00000020','receipt':{'tool_name':'recover_workspace','arguments':{},
        'result':{'current_request_input_attempted':False,'input_state_unknown':True,
                  'current_workspace_continuation':continuation}}},
        {'key':'native_ld_receipt:run:00000019','receipt':{'tool_name':'recover_workspace','arguments':{},
            'result':{'ok':False,'input_state_unknown':True,'current_request_input_attempted':False,
                'current_workspace_recovery':{'operation_id':'op','phase':'close_pending','recovery_only':True,
                    'provenance':'host_current_recovery_state/v1'}}}},
        {'key':'native_ld_receipt:run:00000018','receipt':{'tool_name':'recover_workspace','arguments':{},
            'result':{**response,'current_workspace_continuation':continuation}}}]
    class Store:
        def get_host_state(self, key):
            if key.startswith('native_ld_receipts:'):
                return {'binding':binding}
            return deepcopy(state)
        def set_host_state(self, key, value):
            state.clear()
            state.update(deepcopy(value))
        def recent_native_receipts(self, run_id):
            assert run_id=='run'  # Keep the original receipt source across exact resume.
            return deepcopy(records)
        def get_run(self, run_id):
            return SimpleNamespace(id=run_id,conversation_id='conversation',state=RunState.CANCELLED,
                metadata={'change_request_scope':'permanent_offline_revision',
                          'verification_scope':'offline_native_check_only'})
        def append_event(self, **kwargs):
            pass
    workspace = SimpleNamespace(workspace_id='w', revision_id='r',
        source=SimpleNamespace(original_state='unchanged', original_current_sha256='source', sha256='source'),
        working_copy=SimpleNamespace(path='working.xgwx', current_sha256='a' * 64))
    scene = {'owned_modals': [], 'controls': [], 'native_status': {'items': []}}
    observation = {'ok': True, 'read_only': True, 'action_attempted': False, 'observation': scene}
    events = []
    async def validate(executor, **kwargs):
        return deepcopy(identity)
    async def execute(name, arguments, **kwargs):
        assert (name, arguments) == ('native_editor', {'action': 'observe'})
        return deepcopy(observation)
    async def workspace_tool(name, arguments):
        assert name == 'workspace.status'
        return workspace
    async def normal_close(*args, before_dispatch, **kwargs):
        assert kwargs['return_recovery_outcome'] is True
        assert state['close_reconsideration']['consumed'] is True
        assert state['close_attempt_history'][0]['failure'] == original_failure
        before_dispatch()
        events.append('safe_close')
        return {'source_closed': False, 'discardUnsavedChanges': False, 'unsavedChangesDiscarded': False}
    class WindowFlag:
        def __init__(self, enabled=False):
            self.enabled = enabled
        def __call__(self, hwnd):
            return hwnd == binding['hwnd']
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *args, **kwargs: SimpleNamespace(
        IsWindowVisible=WindowFlag(), IsWindowEnabled=WindowFlag()))
    monkeypatch.setattr(native_workspace_host, 'native_recovery_target_retired', lambda *args: False)
    namespace = {'__package__': 'controlstudio_host', 'deepcopy': deepcopy, 'Path': Path, 'math': math,
        'store': Store(), 'conversation_id': 'conversation', 'run_id': 'run', 'pending_recovery_ui_identity': identity,
        'validate_pending_recovery_owned_ui': validate, 'current_workspace_recovery': lambda executor: {
            'phase':state['phase'],'operation_id':state['operation_id'],'recovery_only':state['phase']=='close_pending'},
        'MCPToolError': RuntimeError, 'native_checkpoint': None, 'active_workspace': workspace,
        'active_engineering_session': {}, 'target': binding,
        '_native_workspace_recovery_state_key': lambda *args: 'recovery',
        '_local_workspace_v1': lambda value: value, '_workspace_resume_stable_identity': lambda value: 'same',
        '_normalized_workspace_path': lambda value: value,
        'saved_project_observation': lambda path: {'stable_during_read': True, 'sha256': 'a' * 64},
        'mcp': SimpleNamespace(agent_session_identity_snapshot=binding, call_workspace_tool=workspace_tool),
        'recovery_workspace_only_binding': lambda *args: False,
        '_close_existing_workspace_for_fresh_reopen': normal_close}
    exec(compile(module, str(source), 'exec'), namespace)
    from fastapi import HTTPException
    namespace['HTTPException'] = HTTPException
    namespace['_engineering_session_process_create_time'] = lambda session: session['created']
    async def healthy_fastpath():
        anchor = {**binding, 'process_create_time':123.0}
        calls = []
        session = {**binding, 'created':123.0, 'project_hash':'a'*64}
        async def revalidate(value):
            calls.append('binding')
            return session
        constraints = {'schema':'xg5000-simulator-current-constraints/v1',
            'evidence_scope':'current_binding_journal_snapshot', 'start_behavior':'fresh_native_preflight',
            'start_journal_blocker':None, 'journal_state':'not_loaded'}
        observed = {'ok':True, 'action':'observe', 'current_constraints':constraints,
            'saved_project':{'stable_during_read':True, 'sha256':'a'*64}}
        async def observe(name, args):
            calls.append((name,args))
            return observed
        owner = SimpleNamespace(allow_simulator=True,recovery_only=False,recovery_pending=False,
                                operation_recoveries={},_execute=observe)
        value = SimpleNamespace(working_copy=SimpleNamespace(current_sha256='a'*64))
        fast = namespace['_healthy_current_recovery']
        result = await fast(owner,None,anchor,value,'generation',revalidate,lambda _: 'generation')
        assert result['relaunch_not_required'] is True and result['rebound'] is False
        assert calls == ['binding',('simulator',{'action':'observe'})]
        previous = {**anchor, 'session_id':'older-session'}
        rotated = await fast(owner,{'phase':'rebound','new_binding':previous},anchor,value,
                             'generation',revalidate,lambda _: 'generation')
        assert rotated['relaunch_not_required'] is True and rotated['rebound'] is False
        assert await fast(owner,{'phase':'rebound','new_binding':{**previous,'pid':999}},anchor,value,
                          'generation',revalidate,lambda _: 'generation') is None
        constraints['journal_state']='start_outcome_unknown'
        constraints['start_journal_blocker']='SIMULATOR_START_OUTCOME_UNKNOWN'
        assert await fast(owner,None,anchor,value,'generation',revalidate,lambda _: 'generation') is None
        before = len(calls)
        assert await fast(owner,{'phase':'close_pending'},anchor,value,'generation',revalidate,lambda _: 'generation') is None
        owner.operation_recoveries['pending'] = {}
        assert await fast(owner,None,anchor,value,'generation',revalidate,lambda _: 'generation') is None
        assert len(calls) == before
    asyncio.run(healthy_fastpath())
    restored_callbacks={name:namespace[name] for name in (
        'recovery_workspace_only_binding','_pending_native_recovery_target_anchor',
        'validate_pending_recovery_owned_ui','current_workspace_recovery',
        'recover_native_workspace','admitted_native_workspace_generation')}
    namespace['recovery_workspace_only_binding']=lambda *args:False
    namespace['validate_pending_recovery_owned_ui']=validate
    namespace['current_workspace_recovery']=lambda executor,**kwargs:{
        'phase':state['phase'],'operation_id':state['operation_id'],'recovery_only':state['phase']=='close_pending'}
    executor = SimpleNamespace(target=binding, expected_process_create_time=123.0,
                               _execute=execute, owned_ui={}, record_raw=lambda *args: None)
    async def scenario():
        result = await namespace['wait_workspace_recovery'](executor, response, prior_ui_identity=ui, defer_only=True)
        assert result['current_workspace_continuation']['state'] == 'physical_wait'
        result = await namespace['wait_workspace_recovery'](executor, {'ok': False})
        assert result['current_workspace_continuation']['route_tool_name'] == 'recover_workspace'
        assert validate_agent(result['current_workspace_continuation'],result['current_workspace_recovery']) == result['current_workspace_continuation']
        for invalid in ({**result['current_workspace_continuation'],'route_tool_name':'simulator'},
                        {**result['current_workspace_continuation'],'operation_id':'other'}):
            with pytest.raises(ValueError):
                validate_agent(invalid,result['current_workspace_recovery'])
        with pytest.raises(ValueError):
            validate_agent(result['current_workspace_continuation'],{**result['current_workspace_recovery'],'recovery_only':False})
        assert state['phase'] == 'close_pending' and state['last_failure'] == original_failure
        ready=deepcopy(state['close_reconsideration'])
        result=await namespace['wait_workspace_recovery'](executor,{'ok':False})
        assert result['current_workspace_continuation']['route_tool_name']=='recover_workspace'
        assert state['close_reconsideration']==ready  # Resume after Ready; do not mint or consume another claim.
        changed = deepcopy(state['close_reconsideration']['scope'])
        changed['binding']['process_create_time'] += 1
        assert not recovery.consume_close_reconsideration(state, changed)
        result = await namespace['recover_native_workspace_operation'](executor)
        assert result['phase'] == 'saved' and result['close_result']['unsavedChangesDiscarded'] is False
        assert events == ['safe_close']
        assert state['close_attempt_history'][0]['failure'] == original_failure
        assert not recovery.consume_close_reconsideration(state, state['close_reconsideration']['scope'])
        # A failed PostMessage call did not enqueue this new close. Release only
        # that delivery reservation after another fresh idle observation.
        state['phase']='close_pending'
        state['last_failure']={'phase':'close_pending','input_state_unknown':True,'failure':{
            'kind':'XG5000_WIN32_POST_FAILED','details':{'phase':'close_dispatch','hwnd':binding['hwnd'],
                'message':16,'wparam':0,'lparam':0,'win32_error':1816,'input_state_unknown':True}}}
        failed_post=deepcopy(state['last_failure'])
        namespace['pending_recovery_ui_identity']=deepcopy(identity)
        result=await namespace['wait_workspace_recovery'](executor,{'ok':False})
        assert result['current_workspace_continuation']['route_tool_name']=='recover_workspace'
        assert state['phase']=='close_pending' and state['last_failure']==failed_post
        assert state['close_reconsideration']['consumed'] is False
        assert recovery.consume_close_reconsideration(state,scope)
        assert recovery.undelivered_close_reservation(state,scope) is None
        notify=deepcopy(state)
        notify['last_failure']['failure']={'kind':'XG5000_WIN32_NOTIFY_CLOSE_FAILED','details':{
            'phase':'close_dispatch','hwnd':binding['hwnd'],'message':16,'wparam':0,'lparam':0,
            'api':'SendNotifyMessageW','dispatch_state':'not_delivered','input_state_unknown':False,
            'handler_execution_verified':False}}
        assert recovery.undelivered_close_reservation(notify,scope) is not None
        notify['last_failure']['failure']['details']['input_state_unknown']=True
        assert recovery.undelivered_close_reservation(notify,scope) is None
        wrong_target=deepcopy(state)
        wrong_target['last_failure']['failure']['details']['hwnd']+=1
        assert recovery.undelivered_close_reservation(wrong_target,scope) is None
        # An old persisted barrier predates the positive-proof fields. Restore only
        # the exact Host receipt, independent of the later unknown Agent callback.
        state.clear()
        state.update({**deepcopy(identity),'source_run_id':'resumed','resumed_source_runs':['run'],
            'conversation_id':'conversation','phase':'close_pending','last_failure':original_failure,
            'owned_ui_response_wait':{'scope':scope,'ui_identity':ui,'input_state_unknown':False}})
        identity['source_run_id']='resumed'
        namespace['run_id']='resumed'
        namespace['pending_recovery_ui_identity']=deepcopy(identity)
        result=await namespace['wait_workspace_recovery'](executor,{'error':{'input_state_unknown':True}})
        assert result['current_workspace_continuation']['route_tool_name']=='recover_workspace'
        assert state['owned_ui_response_wait']['response_receipt_ref']['key'].endswith('00000018')
        assert state['last_failure']==original_failure
    asyncio.run(scenario())
    uncertain = {**response, 'input_state_unknown': True}
    assert not recovery.completed_owned_cancel(uncertain, ui)
    invalid=deepcopy(records)
    invalid[0]['receipt']['result']['current_request_input_attempted']=True
    assert recovery.restored_cancel_receipt(invalid,ui,scope) is None
    async def live_frame(name, arguments):
        assert name=='xg5000.instances'
        return {'instances':[{**binding,'visible':True}]}
    async def restore_owners():
        # The validated checkpoint may include a failed intervening request,
        # while the interrupted lifecycle still belongs to its original run.
        namespace.update(native_checkpoint=object(),preparation_run=False,run_id='next-run',
            prior_run=SimpleNamespace(id='failed-between',state=RunState.FAILED),
            resume_source='failed-between',request_message=None,
            recovery_source_run_matches=lambda *args:True,
            AgentRunPreconditionError=RuntimeError,RunState=RunState,
            _pending_native_recovery_target_anchor=lambda *args:deepcopy(binding))
        namespace['mcp'].call_read_tool=live_frame
        for file_only in (False,True):
            executor.target=deepcopy(binding) if file_only else {**binding,'session_id':'fresh-live-session'}
            namespace['mcp'].agent_session_identity_snapshot=executor.target
            state.clear()
            state.update({**deepcopy(identity),'source_run_id':'run','conversation_id':'conversation','phase':'close_pending'})
            namespace['recovery_workspace_only_binding']=lambda *args:file_only
            await namespace['restore_pending_recovery_owner'](executor)
            assert state['source_run_id']=='next-run' and state['resumed_source_runs']==['run']
            assert executor.recovery_only is True and executor.recovery_pending is True
            assert namespace['pending_recovery_ui_identity']['source_run_id']=='next-run'
            if not file_only:
                assert state['current_binding']['session_id']=='fresh-live-session'
                assert state['binding']['session_id']=='s'
        state['source_run_id']='run'
        async def changed_lifetime(name,arguments):
            return {'instances':[{**binding,'visible':True,'process_create_time':124.0}]}
        namespace['recovery_workspace_only_binding']=lambda *args:False
        namespace['mcp'].call_read_tool=changed_lifetime
        try:
            await namespace['restore_pending_recovery_owner'](executor)
        except RuntimeError:
            pass
        else:
            raise AssertionError('A different physical frame must not inherit the pending owner')
        assert state['source_run_id']=='run'
    asyncio.run(restore_owners())
    async def absent_original_owner():
        # An absent inventory row withdraws readiness only. Actual retirement
        # remains a separate backend read before the existing controlled Open.
        namespace.update(restored_callbacks)
        namespace['validate_pending_recovery_owned_ui']=validate
        namespace['current_workspace_recovery']=lambda executor,**kwargs:{
            'phase':state['phase'],'operation_id':state['operation_id'],'recovery_only':state['phase']=='close_pending'}
        namespace.update(LocalWorkspaceV1=LocalWorkspaceV1, native_checkpoint_boundary=native_checkpoint_boundary,
            pending_anchor=deepcopy(binding), active_engineering_session=deepcopy(binding),
            _engineering_session_process_create_time=lambda session: session.get('process_create_time'),
            _engineering_session_identity_verified=lambda session: session.get('session_id')=='fresh-open',
            _native_ld_edit_ready=lambda *args: False)
        workspace.source_kind,workspace.provenance='new_build',None
        transitions=[]
        namespace['update_native_workspace_binding']=lambda **kwargs:transitions.append('file_only')
        namespace['execution_broker']=SimpleNamespace(bind_agent_workspace=lambda value:
            setattr(namespace['mcp'],'agent_session_identity_snapshot',deepcopy(value)),
            clear_agent_session=lambda:setattr(namespace['mcp'],'agent_session_identity_snapshot',None))
        async def absent(name, arguments):
            assert (name,arguments)==('xg5000.instances',{})
            return {'instances':[]}
        namespace['mcp'].call_read_tool=absent
        namespace['mcp'].agent_session_identity_snapshot=deepcopy(binding)
        identity['source_run_id']='absent-run'
        namespace['run_id']='absent-run'
        state.clear()
        state.update({**deepcopy(identity),'source_run_id':'run','conversation_id':'conversation',
            'phase':'close_pending','last_failure':deepcopy(original_failure)})
        native=native_workspace_host.NativeWorkspaceExecution(gateway=namespace['mcp'],target=binding,
            current_target=lambda:None if namespace['recovery_workspace_only_binding'](
                namespace['mcp'].agent_session_identity_snapshot,binding) else namespace['mcp'].agent_session_identity_snapshot,
            record_raw=lambda *args:None,refresh_saved=lambda *args:None,
            expected_process_create_time=123.0,recover_workspace=namespace['recover_native_workspace_operation'])
        await namespace['restore_pending_recovery_owner'](native)
        assert state['source_run_id']=='absent-run' and state['resumed_source_runs']==['run']
        assert state['phase']=='close_pending' and 'close_result' not in state
        assert state['last_failure']==original_failure and transitions==['file_only']
        assert namespace['active_engineering_session'] is None
        assert native.recovery_only is True and native.recovery_pending is True
        with pytest.raises(MCPToolError,match='Only preserved recovery'):
            await native._execute('save_project',{})
        retired=[False]
        def read_retirement(target, created):
            assert target==binding and created==123.0
            transitions.append('retirement_read')
            return retired[0]
        monkeypatch.setattr(native_workspace_host,'native_recovery_target_retired',read_retirement)
        pending=await namespace['recover_native_workspace_operation'](native)
        assert pending['phase']=='close_pending' and state['phase']=='close_pending'
        async def no_retained(workspace):
            return None
        async def controlled_open(workspace, **kwargs):
            assert retired[0] and state['phase']=='launch_pending'
            assert kwargs['require_controlled_launch'] is True
            assert kwargs['binding_wait_identity']=={'operation_id':'op','source_run_id':'absent-run'}
            transitions.append('controlled_open')
            fresh={**binding,'session_id':'fresh-open','pid':601,'hwnd':602,'process_create_time':223.0}
            namespace['mcp'].agent_session_identity_snapshot=deepcopy(fresh)
            return object(),fresh
        namespace['revalidate_native_workspace_binding']=no_retained
        namespace['open_workspace_in_xg5000']=controlled_open
        retired[0]=True
        result=await namespace['wait_workspace_recovery'](native,{'ok':False})
        assert result['rebound'] is True and result['phase']=='rebound',result
        assert result['source_retirement_observed'] is True and result['normal_close_verified'] is False
        assert state['last_failure']==original_failure and transitions[-1]=='controlled_open'
        assert transitions.count('controlled_open')==1 and native.recovery_only is False
        # Fresh inventory cannot transfer ownership to reused or unrelated frames.
        for row in ({**binding,'process_create_time':124.0,'visible':True},
                    {**binding,'hwnd':999,'visible':True}):
            async def changed(name, arguments):
                return {'instances':[row]}
            namespace['mcp'].call_read_tool=changed
            namespace['mcp'].agent_session_identity_snapshot=deepcopy(binding)
            native.target=deepcopy(binding);native.expected_process_create_time=123.0
            state.clear()
            state.update({**deepcopy(identity),'source_run_id':'run','conversation_id':'conversation','phase':'close_pending'})
            with pytest.raises(RuntimeError,match='live frame/lifetime changed'):
                await namespace['restore_pending_recovery_owner'](native)
            assert state['source_run_id']=='run'
        namespace['mcp'].call_read_tool=absent
        namespace['saved_project_observation']=lambda path:{'stable_during_read':True,'sha256':'b'*64}
        with pytest.raises(RuntimeError,match='original target differs'):
            await namespace['restore_pending_recovery_owner'](native)
        assert state['source_run_id']=='run'
    asyncio.run(absent_original_owner())
    async def replacement_owner():
        namespace.update(restored_callbacks)
        namespace.update(pending_anchor=None,run_id='candidate-run',
            native_admission_generation=native_workspace_host.native_admission_generation,
            _engineering_session_process_create_time=lambda session:session.get('process_create_time') if isinstance(session,dict) else None,
            _engineering_session_identity_verified=lambda session:isinstance(session,dict) and session.get('session_id')=='candidate',
            _native_ld_edit_ready=lambda *args:False,
            saved_project_observation=lambda path:{'stable_during_read':True,'sha256':'a'*64},
            current_status_v1=lambda:SimpleNamespace(model_dump=lambda **kwargs:{}))
        candidate={**binding,'session_id':'candidate','pid':701,'hwnd':702,
            'process_create_time':323.0,'project_hash':'a'*64}
        namespace['active_engineering_session']=deepcopy(candidate)
        namespace['mcp'].agent_session_identity_snapshot={**namespace['workspace_agent_binding'](workspace),**candidate}
        namespace['mcp'].status=SimpleNamespace(status='ready')
        namespace['retained_native_workspace_binding']=lambda workspace:{
            'workspace_id':'w','revision_id':'r','session':deepcopy(candidate)}
        rows=[{**candidate,'visible':True}]
        async def inventory(name,arguments):
            assert (name,arguments)==('xg5000.instances',{})
            return {'instances':deepcopy(rows)}
        namespace['mcp'].call_read_tool=inventory
        checked=[]
        retired=[False]
        def retirement(target,created):
            assert target==binding and created==123.0
            checked.append('original_retirement')
            return retired[0]
        monkeypatch.setattr(native_workspace_host,'native_recovery_target_retired',retirement)
        response_candidate=deepcopy(candidate)
        async def revalidate(workspace):
            assert retired[0]
            checked.append('candidate_read')
            return deepcopy(response_candidate)
        namespace['revalidate_native_workspace_binding']=revalidate
        def bind(session):
            checked.append('bind_candidate')
            namespace['active_engineering_session']=deepcopy(session)
            namespace['mcp'].agent_session_identity_snapshot={**namespace['workspace_agent_binding'](workspace),**session}
            return session
        namespace['bind_engineering_session']=bind
        async def forbidden(*args,**kwargs):
            raise AssertionError('Adopting an existing candidate must not save, close, or open it')
        namespace['open_workspace_in_xg5000']=forbidden
        namespace['_close_existing_workspace_for_fresh_reopen']=forbidden
        state.clear()
        state.update({**deepcopy(identity),'source_run_id':'run','conversation_id':'conversation',
            'phase':'close_pending','last_failure':deepcopy(original_failure),
            'close_attempt_history':[{'failure':deepcopy(original_failure)}]})
        history=deepcopy(state['close_attempt_history'])
        native=native_workspace_host.NativeWorkspaceExecution(gateway=namespace['mcp'],target=candidate,
            current_target=lambda:namespace['mcp'].agent_session_identity_snapshot,
            record_raw=lambda *args:None,refresh_saved=lambda *args:None,
            expected_process_create_time=323.0,recover_workspace=namespace['recover_native_workspace'],
            pending_recovery_owned_ui=namespace['validate_pending_recovery_owned_ui'],
            wait_workspace_recovery=namespace['wait_workspace_recovery'])
        await namespace['restore_pending_recovery_owner'](native)
        assert state['binding']==binding and 'current_binding' not in state
        assert state['phase']=='close_pending' and state['source_run_id']=='candidate-run'
        assert namespace['pending_recovery_projection_identity']['binding']==binding
        assert namespace['pending_recovery_ui_identity'] is None
        assert namespace['pending_recovery_ui_anchor'] is None
        assert native.recovery_only is True and native.recovery_pending is True and checked==[]
        with pytest.raises(MCPToolError,match='Only preserved recovery'):
            await native._execute('save_project',{})
        with pytest.raises(RuntimeError,match='no owned-UI scope'):
            await native._execute('native_editor',{'action':'observe'})
        pending=await native.execute('recover_workspace',{})
        assert pending['phase']=='close_pending' and pending['current_workspace_recovery']['recovery_only'] is True
        assert pending['current_workspace_recovery']['native_binding_available'] is False
        assert checked==['original_retirement'] and state['binding']==binding
        admission=finalization['ContinuationFinalizationAdmission']()
        def fact(result,call_id,status):
            def digest(value):
                return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            return {'name':'recover_workspace','arguments':{},'call_id':call_id,'status':status,
                'receipt_reference':{'result_id':'tool-result:sha256:'+digest({'tool_name':'recover_workspace','content':result}),
                    'content_sha256':digest(result)}}
        old_generation=native_workspace_host.native_admission_generation(binding,123.0)
        admission.observe(fact(pending,'pending','unknown'),pending,
            binding={'available':False,'basis':'host_admission_recovery_state'},
            recovery_state=pending['current_workspace_recovery'],host_callback_entered=True,
            invocation_binding={'admitted_generation':old_generation},host_result=pending)
        assert admission._basis is not None
        # Rejected business, file, and live-lifetime changes cannot settle this basis.
        native.target['revision_id']='other'
        with pytest.raises(RuntimeError,match='original target differs'):
            await namespace['restore_pending_recovery_owner'](native)
        native.target['revision_id']='r'
        namespace['saved_project_observation']=lambda path:{'stable_during_read':True,'sha256':'b'*64}
        with pytest.raises(RuntimeError,match='original target differs'):
            await namespace['restore_pending_recovery_owner'](native)
        namespace['saved_project_observation']=lambda path:{'stable_during_read':True,'sha256':'a'*64}
        rows[0]['process_create_time']=324.0
        with pytest.raises(RuntimeError,match='live frame/lifetime changed'):
            await namespace['restore_pending_recovery_owner'](native)
        rows[0]['process_create_time']=323.0
        assert admission._basis is not None and state['phase']=='close_pending'
        retired[0]=True
        response_candidate['process_create_time']=324.0
        rejected=await native.execute('recover_workspace',{})
        assert rejected['ok'] is False and state['phase']=='close_pending'
        assert admission._basis is not None and 'bind_candidate' not in checked
        response_candidate['process_create_time']=323.0
        result=await native.execute('recover_workspace',{})
        assert result['ok'] is True and result['rebound'] is True and result['phase']=='rebound',result
        assert result['operation_id']=='op' and result['launch_attempted'] is False
        assert result['current_request_input_attempted'] is False and result['retained_target_revalidated'] is True
        assert result['current_workspace_recovery']['recovery_only'] is False
        assert result['current_workspace_recovery']['native_binding_available'] is True
        assert isinstance(result.get('native_binding_generation'),str)
        assert state['binding']==binding and 'current_binding' not in state
        assert state['last_failure']==original_failure and state['close_attempt_history']==history
        assert 'completed_operations' not in state and state['new_binding']['pid']==701
        assert checked.count('bind_candidate')==1 and native.recovery_only is False
        admission.observe(fact(result,'adopted','completed'),result,
            binding={'available':True,'admitted_generation':result['native_binding_generation']},
            recovery_state=result['current_workspace_recovery'],host_callback_entered=True,
            invocation_binding={'admitted_generation':old_generation},host_result=result)
        assert admission._basis is None
        assert admission._request_continuation['kind']=='request_after_verified_rebound'
        assert admission._request_continuation['original_outcome_preserved'] is True
        # A new follow-up after normal Web reconnect must not inherit an old
        # terminal launch or compare its superseded saved bytes with this file.
        terminal_failure={'details':{'launch_retirement':{
            'schema':'controlstudio-launch-retirement/v1','launched_identity_observed':True,
            'launched_pid':801,'process_create_time':423.0,'terminal':True,
            'retired_verified':True,'observation_method':'psutil_exact_identity_after_existing_cleanup/v1',
            'retirement_state':'absent','cleanup_effect':'not_running_or_cleanup_failed',
            'automatic_replay':False}}}
        state.clear()
        state.update({**deepcopy(identity),'source_run_id':'old-run','conversation_id':'conversation',
            'phase':'launch_terminal','saved_sha256':'b'*64,'terminal_failure':terminal_failure})
        old_terminal=deepcopy(state)
        namespace.update(resume_source=None,request_message=None)
        await namespace['restore_pending_recovery_owner'](native)
        assert state==old_terminal and native.recovery_only is False and native.recovery_pending is False
        assert namespace['current_workspace_recovery'](native) is None
        # Resuming the failed follow-up is still unrelated to the old recovery.
        namespace.update(resume_source='failed-between',recovery_source_run_matches=lambda *args:False)
        await namespace['restore_pending_recovery_owner'](native)
        assert state==old_terminal and native.recovery_only is False
        namespace.update(resume_source=None,recovery_source_run_matches=lambda *args:True)
        # A live original, unknown late launch, or missing launch-retirement
        # proof still blocks a new request without transferring the old owner.
        for case in ('live-original','launch-pending','unproven-launch'):
            state.clear();state.update(deepcopy(old_terminal))
            retired[0]=case!='live-original'
            if case=='launch-pending':state['phase']='launch_pending'
            if case=='unproven-launch':state['terminal_failure']['details']['launch_retirement']['retired_verified']=False
            unchanged=deepcopy(state)
            with pytest.raises(RuntimeError,match='original request resume'):
                await namespace['restore_pending_recovery_owner'](native)
            assert state==unchanged and native.recovery_only is False
        # Explicit resume retains the exact saved-source check; it cannot use
        # the newly admitted generation to silently settle the old operation.
        retired[0]=True
        state.clear();state.update(deepcopy(old_terminal))
        namespace['resume_source']='failed-between'
        with pytest.raises(RuntimeError,match='original target differs'):
            await namespace['restore_pending_recovery_owner'](native)
        assert state==old_terminal
        state['saved_sha256']='a'*64
        state['phase']='close_pending'
        await namespace['restore_pending_recovery_owner'](native)
        assert state['source_run_id']=='candidate-run' and state['phase']=='close_pending'
        assert native.recovery_only is True and native.recovery_pending is True
    asyncio.run(replacement_owner())
    # A pending launch deliberately has no current MCP session. The public
    # Resume projection and final submission predicate share the same validated
    # recovery owner; displaying Resume grants neither a frame nor editor input.
    source_run=SimpleNamespace(id='run',conversation_id='conversation',state=RunState.CANCELLED,
        request_message_id='message',created_at=1,metadata={'allow_simulator':True,
            'change_request_scope':'permanent_offline_revision',
            'verification_scope':'offline_native_check_only'})
    detail=SimpleNamespace(id='conversation',workflow='engineering',workspace_id='w',revision_id='r',
        runs=[source_run],messages=[SimpleNamespace(id='message')])
    checkpoint={'source_run_id':'run','binding':deepcopy(binding),'completed_tool_calls':3}
    pending_state={**deepcopy(identity),'source_run_id':'run','conversation_id':'conversation',
        'phase':'launch_pending','launch_operation_id':'original-launch',
        'launch_pending_frame':{'pid':801,'hwnd':802,'process_create_time':423.0},
        'last_failure':deepcopy(original_failure)}
    state.clear();state.update(deepcopy(pending_state))
    namespace['mcp'].agent_session_identity_snapshot=None
    namespace.update(asyncio=asyncio,RunState=RunState,AgentProtocolError=ValueError,
        ConversationNativeExecutionV1=ConversationNativeExecutionV1,
        active_workspace=workspace,active_engineering_session=None,
        _conversation_v1=lambda *args,**kwargs:SimpleNamespace(),
        completion_state_key=lambda *args:'completion',
        latest_native_checkpoint_summary=lambda *args:deepcopy(checkpoint),
        _ENGINEERING_TARGET_PRECONDITION_MESSAGE='target unavailable',
        saved_project_observation=lambda path:{'stable_during_read':True,'sha256':'a'*64})
    store_fixture=namespace['store']
    store_fixture.get_conversation=lambda conversation_id:detail if conversation_id==detail.id else None
    store_fixture.get_engineering_mission=lambda conversation_id:None
    store_fixture.get_run=lambda run_id:source_run if run_id==source_run.id else None
    store_fixture.native_resume_latest_attempt_matches_checkpoint=lambda **kwargs:(
        kwargs['conversation_id']=='conversation' and kwargs['source_run_id']=='run'
        and kwargs['latest_run_id']=='run')
    async def resume_projection():
        view=await namespace['get_conversation_v1']('conversation')
        assert view.native_execution.resumable is True
        assert view.native_execution.source_run_id=='run'
        assert namespace['native_resume_target_matches_checkpoint'](detail,'run',binding) is True
        anchor = namespace['pending_native_recovery_anchor'](detail, 'run')
        target = (binding['workspace_id'], binding['revision_id'], binding['session_id'])
        allowed = namespace['_pending_recovery_conversation_target']
        assert allowed(target, None, anchor, file_only=False)
        assert not allowed(('other', *target[1:]), None, anchor, file_only=False)
        assert namespace['pending_native_recovery_anchor'](detail, 'unrelated-run') is None
        assert not allowed(target, None, namespace['pending_native_recovery_anchor'](detail, 'unrelated-run'), file_only=False)
        assert not allowed(target, {'workspace_id':'foreign'}, anchor, file_only=False)
        assert namespace['mcp'].agent_session_identity_snapshot is None
        assert namespace['active_engineering_session'] is None and state==pending_state
        namespace['saved_project_observation']=lambda path:{'stable_during_read':True,'sha256':'b'*64}
        assert (await namespace['get_conversation_v1']('conversation')).native_execution.resumable is False
        namespace['saved_project_observation']=lambda path:{'stable_during_read':True,'sha256':'a'*64}
        state['binding']['revision_id']='foreign'
        assert (await namespace['get_conversation_v1']('conversation')).native_execution.resumable is False
        state['binding']['revision_id']='r'
        state['conversation_id']='foreign'
        assert (await namespace['get_conversation_v1']('conversation')).native_execution.resumable is False
        state['conversation_id']='conversation'
        source_run.conversation_id='foreign'
        assert (await namespace['get_conversation_v1']('conversation')).native_execution.resumable is False
        source_run.conversation_id='conversation'
        namespace['mcp'].agent_session_identity_snapshot={**binding,'workspace_id':'foreign'}
        assert (await namespace['get_conversation_v1']('conversation')).native_execution.resumable is False
        assert state==pending_state  # No launch, lease, phase, or unknown-history mutation.
    asyncio.run(resume_projection())
    # Compose the real HTTP lease dependency, existing-frame reuse, original
    # launch waiter, and common attach admission. A successful fresh attach alone
    # must not leave a previous delegated launch parked forever.
    import contextvars
    import logging
    import os
    import uuid
    from contextlib import asynccontextmanager
    from datetime import datetime, timedelta, timezone
    from fastapi import Depends, HTTPException
    from controlstudio_host.api_models import XG5000ProjectLaunchV1
    launch_names={'pending_owned_launch_resume','target_switch_lease','join_owned_workspace_launch','complete_workspace_launch',
        'open_workspace_in_xg5000','resume_workspace_editor','_validated_engineering_session_result',
        '_engineering_session_read_ready','_engineering_session_process_create_time',
        '_engineering_session_exact_lifetime_binding','_engineering_session_identity_verified'}
    launch_nodes=[node for node in ast.walk(ast.parse(source.read_text(encoding='utf-8')))
        if isinstance(node,(ast.AsyncFunctionDef,ast.FunctionDef)) and node.name in launch_names]
    launch_module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),
        *[Globals().visit(node) for node in launch_nodes]],type_ignores=[])
    ast.fix_missing_locations(launch_module)
    launch_store=HostStore(tmp_path/'launch.sqlite3')
    path=str((tmp_path/'working.xgwx').resolve())
    launch_workspace=SimpleNamespace(workspace_id='w',revision_id='r',
        working_copy=SimpleNamespace(path=path,current_sha256='a'*64))
    route='/api/v1/workspaces/w/resume'
    request_identity={'route':route,'request':{'timeoutSeconds':120}}
    launch_op=str(uuid.uuid5(uuid.NAMESPACE_URL,json.dumps(request_identity,ensure_ascii=False,
        separators=(',',':'),sort_keys=True)))
    def digest(value):
        return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    proof={'method':'same_rot_object_project_tree_and_path_v1','same_active_object':True,'stable_repeat':True,
        'pid':701,'main_hwnd':702,'process_create_time':323.0,'project_hash':'a'*64,'project_path':path}
    attached={'session_id':'fresh','pid':701,'hwnd':702,'project_path':path,'project_hash':'a'*64,
        'metadata':{'project_identity_verified_against_selected_pid':True,
            'project_identity_assurance':'controlled_launch_read_only','project_identity_proof':proof}}
    raw_launch={'operation':'xg5000.open_project','verified':True,'workspace_id':'w',
        'working_path':path,'working_hash':'a'*64,'launch_pid':701,'window_detected':True,
        'project_identity_verified_against_selected_pid':False,
        'project_identity_read_only_bound_to_selected_pid':True,
        'identity_assurance':'controlled_launch_native_file_read_only',
        'instance':{'pid':701,'hwnd':702},'observed_instances':[],'detail':'durable original launch'}
    calls=[];available=[True];observed_state=[None]
    async def existing(workspace):
        return deepcopy(attached) if available[0] else None
    async def absent(*args,**kwargs):
        return None
    async def read(name,arguments):
        assert name=='xg5000.attach'
        calls.append('attach')
        return deepcopy(attached)
    async def workspace_call(name,arguments,*,private_meta):
        assert name=='xg5000.open_project'
        claims=private_meta['controlstudio/launch-binding-wait']
        assert claims['action']=='observe'  # No launch command or native input.
        assert claims['owner']==launch_store.get_host_state('owned_launch_wait_operation:'+launch_op)['owner']
        assert arguments['expected_working_hash']=='a'*64
        calls.append('observe')
        if observed_state[0] is not None:
            return {'schema':'controlstudio-owned-launch-binding-wait/v1','state':observed_state[0],
                'owner_ref':claims['owner']['owner_ref'],'scope':claims['owner']['scope'],
                'verified':False,'native_binding_available':False,'automatic_replay':False,
                'pid':701,'hwnd':702,'process_create_time':323.0}
        return deepcopy(raw_launch)
    async def body():return request_identity['request']
    async def connected():return False
    request=SimpleNamespace(url=SimpleNamespace(path=route),json=body,is_disconnected=connected)
    launch_namespace={'__package__':'controlstudio_host','deepcopy':deepcopy,'Path':Path,'math':math,'os':os,
        'json':json,'uuid':uuid,'datetime':datetime,'timezone':timezone,'timedelta':timedelta,
        'asynccontextmanager':asynccontextmanager,'asyncio':asyncio,'Depends':Depends,
        'HTTPException':HTTPException,'MCPToolError':MCPToolError,'XG5000ProjectLaunchV1':XG5000ProjectLaunchV1,
        'logger':logging.getLogger(__name__),'store':launch_store,
        'engineering_target_run_guard':lambda:None,'_canonical_json_digest':digest,
        '_normalized_workspace_path':lambda value:str(Path(value).resolve()),
        'launch_wait_request_context':contextvars.ContextVar('test_launch_request',default=None),
        'owned_launch_wait_requires_park':recovery.owned_launch_wait_requires_park,
        'shutdown_event':asyncio.Event(),'LATE_WORKSPACE_RECONCILE_SECONDS':1,
        'active_workspace':None,'active_engineering_session':None,
        'revalidate_native_workspace_binding':existing,'_find_existing_workspace_session':absent,
        'workspace_candidate_session':lambda value:value,'bind_engineering_session':lambda value:value,
        '_native_ld_edit_ready':lambda *args:False,'record_workspace_attach_failure':lambda *args,**kwargs:None,
        'mcp':SimpleNamespace(call_workspace_tool=workspace_call,call_read_tool=read,
            sign_mutation_authorization=lambda claims:claims)}
    exec(compile(launch_module,str(source),'exec'),launch_namespace)
    def park_original():
        lease=launch_store.acquire_target_operation_lease(operation_id=launch_op,owner_kind='workspace_resume',
            holder_token='original',target_identity=request_identity)
        scope={'target_lease_id':lease['id'],'operation_id':launch_op,'workspace_id':'w','revision_id':'r',
            'project_path':path,'project_hash':'a'*64,'source_run_id':None}
        launch_store.set_host_state('owned_launch_wait_operation:'+launch_op,{
            'owner':{'owner_ref':digest(scope),'scope':scope},'state':'parked','pending':None,
            'exclusion_lease_id':lease['id']})
        launch_store.park_target_operation_lease(operation_id=launch_op,holder_token='original',reason='previous_wait')
    async def public_resume():
        lease=launch_namespace['target_switch_lease'](request)
        await anext(lease)
        try:
            return await launch_namespace['resume_workspace_editor'](
                launch_workspace,None,timeout_seconds=120,candidate_only=True)
        finally:
            await lease.aclose()
    async def launch_lifecycle():
        park_original()
        result=await public_resume()
        assert result[0].verified is True and calls==['observe','attach']
        assert launch_store.get_host_state('owned_launch_wait_operation:'+launch_op)['state']=='ready'
        assert launch_store.active_target_operation_lease() is None
        # A failed common admission never completes or releases the old owner.
        park_original();calls.clear();raw_launch['working_hash']='b'*64
        with pytest.raises(HTTPException):await public_resume()
        assert calls==['observe','attach']
        assert launch_store.active_target_operation_lease()['state']=='STALE_BLOCKED'
        assert launch_store.get_host_state('owned_launch_wait_operation:'+launch_op)['state']=='parked'
        raw_launch['working_hash']='a'*64
        # Changed saved scope is for retirement observation only, never ready.
        available[0]=False;launch_workspace.working_copy.current_sha256='b'*64;calls.clear()
        with pytest.raises(HTTPException,match='only its retirement'):await public_resume()
        assert calls==['observe'] and launch_store.active_target_operation_lease()['state']=='STALE_BLOCKED'
        observed_state[0]='original_retired';calls.clear()
        with pytest.raises(HTTPException) as retired:await public_resume()
        assert retired.value.detail['material_state']=='original_retired' and calls==['observe']
        assert launch_store.get_host_state('owned_launch_wait_operation:'+launch_op)['state']=='original_retired'
        assert launch_store.active_target_operation_lease() is None
    asyncio.run(launch_lifecycle())
    # The real lease manager must rejoin the original parked native launch
    # after its recovery callback transfers to a new run. The launch source
    # remains immutable; changing it would turn this into an ordinary acquire.
    launch_op=str(uuid.uuid4())
    launch_workspace.working_copy.current_sha256='a'*64
    observed_state[0]=None
    raw_launch['process_create_time']=323.0
    native_identity={'operation_id':launch_op,'source_run_id':'original-run',
        'workspace_id':'w','revision_id':'r','project_path':path,'project_hash':'a'*64}
    native_lease=launch_store.acquire_target_operation_lease(operation_id=launch_op,
        owner_kind='workspace_resume',holder_token='original-native',target_identity=native_identity)
    native_scope={'target_lease_id':native_lease['id'],**native_identity}
    native_owner={'owner_ref':digest(native_scope),'scope':native_scope}
    pending_frame={'pid':701,'hwnd':702,'process_create_time':323.0}
    native_wait={'owner':deepcopy(native_owner),'state':'launch_unresolved',
        'exclusion_lease_id':native_lease['id'],'pending':{**pending_frame,
            'lease_ref':'original-native-lease','launch_operation_id':'original-native-launch'}}
    launch_store.set_host_state('owned_launch_wait_operation:'+launch_op,native_wait)
    launch_store.park_target_operation_lease(operation_id=launch_op,holder_token='original-native',reason='previous_wait')
    async def native_rejoin(operation_id=launch_op):
        return await launch_namespace['open_workspace_in_xg5000'](launch_workspace,
            timeout_seconds=120,require_controlled_launch=True,candidate_only=True,
            binding_wait_identity={'operation_id':operation_id,'source_run_id':'resumed-run','reconcile_only':True},
            expected_pending_frame=pending_frame)
    async def native_launch_lifecycle():
        calls.clear()
        launch_workspace.working_copy.current_sha256='b'*64
        with pytest.raises(HTTPException):await native_rejoin()
        launch_workspace.working_copy.current_sha256='a'*64
        with pytest.raises(HTTPException):await native_rejoin('foreign-operation')
        corrupted=deepcopy(native_wait);corrupted['owner']['owner_ref']='not-the-original-owner'
        launch_store.set_host_state('owned_launch_wait_operation:'+launch_op,corrupted)
        with pytest.raises(HTTPException):await native_rejoin()
        assert calls==[] and launch_store.active_target_operation_lease()['state']=='STALE_BLOCKED'
        launch_store.set_host_state('owned_launch_wait_operation:'+launch_op,native_wait)
        # A binding result without the exact saved-version admission cannot
        # release the original lease even though its read-only rejoin succeeded.
        raw_launch['working_hash']='b'*64
        with pytest.raises(HTTPException):await native_rejoin()
        assert calls==['observe','attach']
        parked=launch_store.active_target_operation_lease()
        assert parked['state']=='STALE_BLOCKED' and parked['id']==native_lease['id']
        assert json.loads(parked['target_identity_json'])==native_identity
        assert launch_store.get_host_state('owned_launch_wait_operation:'+launch_op)['owner']==native_owner
        raw_launch['working_hash']='a'*64;calls.clear()
        result=await native_rejoin()
        assert result[0].verified is True and calls==['observe','attach']
        assert launch_store.active_target_operation_lease() is None
        completed=launch_store.get_host_state('owned_launch_wait_operation:'+launch_op)
        assert completed['state']=='ready' and completed['owner']==native_owner
        assert completed['exclusion_lease_id']==native_lease['id']
        assert completed['pending']['launch_operation_id']=='original-native-launch'
    asyncio.run(native_launch_lifecycle())
    # A structured native failure can prove exact retirement before a pending
    # frame was returned. The real manager must release that original owner.
    retirement={'schema':'controlstudio-launch-retirement/v1','launched_identity_observed':True,
        'launched_pid':701,'process_create_time':323.0,'terminal':True,'retired_verified':True,
        'observation_method':'psutil_exact_identity_after_existing_cleanup/v1',
        'retirement_state':'absent','cleanup_effect':'terminated','automatic_replay':False}
    terminal_failure={'schema':'controlstudio-native-failure/v1','kind':'WorkspaceError',
        'message':'Workspace opening did not complete.','details':{'launch_retirement':retirement}}
    async def retired_call(*args,**kwargs):
        error=MCPToolError('Original launch retired')
        error.mcp_error_payload={'error':{'nativeFailure':terminal_failure}}
        raise error
    launch_namespace['mcp'].call_workspace_tool=retired_call
    launch_store.set_host_state('owned_launch_wait_operation:'+launch_op,native_wait)
    async def terminal_launch_lifecycle():
        with pytest.raises(HTTPException):await native_rejoin()
        assert launch_store.active_target_operation_lease() is None
        terminal_wait=launch_store.get_host_state('owned_launch_wait_operation:'+launch_op)
        assert terminal_wait['state']=='original_retired'
        assert terminal_wait['terminal_failure']==terminal_failure
    asyncio.run(terminal_launch_lifecycle())

    # Repair the historical ordering defect through normal HostStore APIs:
    # terminal retirement was archived, but the old global lease stayed parked.
    retired_store=HostStore(tmp_path/'terminal.sqlite3')
    conversation=retired_store.create_conversation(workspace_id='w',revision_id='r',engineering_session_id='s')
    _,source_run,_=retired_store.create_user_message_and_run(
        conversation_id=conversation.id,content='recover',operation_id='source')
    retired_identity={**native_identity,'source_run_id':source_run.id,'operation_id':'retired-op'}
    old_lease=retired_store.acquire_target_operation_lease(operation_id='retired-op',
        owner_kind='workspace_resume',holder_token='old-holder',target_identity=retired_identity)
    old_scope={'target_lease_id':old_lease['id'],**retired_identity}
    old_wait={'owner':{'owner_ref':digest(old_scope),'scope':old_scope},
        'state':'parked','exclusion_lease_id':old_lease['id'],'pending':None}
    wait_key='owned_launch_wait_operation:retired-op'
    retired_store.set_host_state(wait_key,old_wait)
    terminal_operation={'operation_id':'retired-op','source_run_id':source_run.id,
        'conversation_id':conversation.id,'binding':{'workspace_id':'w','revision_id':'r','project_path':path},
        'saved_sha256':'a'*64,'phase':'launch_terminal','terminal_failure':terminal_failure}
    durable={'operation_id':'new-pending-op','phase':'launch_pending',
        'completed_operations':[terminal_operation]}
    recovery_key='native_workspace_recovery:v1:'+hashlib.sha256(json.dumps(
        (conversation.id,'w','r'),ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    retired_store.set_host_state(recovery_key,durable)
    assert retired_store.reconcile_retired_owned_launch_target_lease()==0  # Active owner.
    retired_store.park_target_operation_lease(operation_id='retired-op',holder_token='old-holder',reason='old_wait')
    assert retired_store.reconcile_retired_owned_launch_target_lease()==0  # Source run still active.
    retired_store.update_run_state(run_id=source_run.id,state=RunState.CANCELLED)
    for invalid in ({**terminal_operation,'phase':'launch_pending'},
                    {**terminal_operation,'saved_sha256':'b'*64},
                    {**terminal_operation,'terminal_failure':{'details':{}}}):
        retired_store.set_host_state(recovery_key,{**durable,'completed_operations':[invalid]})
        assert retired_store.reconcile_retired_owned_launch_target_lease()==0
    retired_store.set_host_state(recovery_key,durable)
    retired_store.set_host_state(wait_key,None)
    assert retired_store.reconcile_retired_owned_launch_target_lease()==0
    retired_store.set_host_state(wait_key,{**old_wait,'pending':{'pid':702,'process_create_time':323.0}})
    assert retired_store.reconcile_retired_owned_launch_target_lease()==0
    retired_store.set_host_state(wait_key,old_wait)
    assert retired_store.reconcile_retired_owned_launch_target_lease()==1
    assert retired_store.active_target_operation_lease() is None
    assert retired_store.get_host_state(wait_key)==old_wait
    assert retired_store.get_host_state(recovery_key)==durable
    audit=retired_store.get_host_state('owned_launch_terminal_settlement:retired-op')
    assert audit['launch_retirement']==retirement and audit['terminal_operation']==terminal_operation
    assert retired_store.reconcile_retired_owned_launch_target_lease()==0
    next_lease=retired_store.acquire_target_operation_lease(operation_id='new-pending-op',
        owner_kind='workspace_resume',holder_token='new-holder',target_identity={'next':'request'})
    assert next_lease['id']!=old_lease['id']
    database=HostStore(tmp_path/'receipts.sqlite3')
    for index in range(20):
        database.set_host_state(f'native_ld_receipt:run:{index:08d}',{'sequence':index})
    database.set_host_state('native_ld_receipt:other:00000099',{'sequence':99})
    tail=database.recent_native_receipts('run')
    assert [item['receipt']['sequence'] for item in tail]==list(range(19,3,-1))


def test_resume_lineage_uses_typed_pre_native_rejection_not_display_text():
    import json
    import sqlite3
    from contextlib import nullcontext
    from controlstudio_host.store import HostStore
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE runs(id TEXT,conversation_id TEXT,state TEXT,created_at TEXT,updated_at TEXT,
                          request_message_id TEXT,error_code TEXT,error_detail TEXT,metadata_json TEXT);
        CREATE TABLE messages(id TEXT,conversation_id TEXT,role TEXT,content TEXT,metadata_json TEXT);
        CREATE TABLE host_state(key TEXT,value_json TEXT);
        CREATE TABLE events(sequence INTEGER,run_id TEXT,conversation_id TEXT,event_type TEXT,payload_json TEXT);
    """)
    scope = {'change_request_scope':'permanent_offline_revision','verification_scope':'offline_native_check_only'}
    resume = {**scope, 'resume_native_run_id':'source', 'workspace_recovery_source_run_id':'source',
              'native_resume_admission_version':1}
    message = {key:value for key,value in resume.items() if key != 'native_resume_admission_version'}
    connection.executemany('INSERT INTO messages VALUES(?,?,?,?,?)', [
        ('original','c','user','original request',json.dumps(scope)),
        ('retry','c','user','original request',json.dumps(message))])
    connection.executemany('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)', [
        ('source','c','FAILED','2026-01-01T00:00:00+00:00','2026-01-01T00:01:00+00:00',
         'original','failed','failure',json.dumps(scope)),
        ('retry','c','FAILED','2026-01-01T00:02:00+00:00','2026-01-01T00:03:00+00:00',
         'retry','runtime_precondition','localized display text with diagnostics',json.dumps(resume))])
    connection.executemany('INSERT INTO host_state VALUES(?,?)', [
        ('native_ld_checkpoint_latest:c',json.dumps({'key':'native_ld_checkpoint:source'})),
        ('native_ld_checkpoint:source',json.dumps({'source_run_id':'source'}))])
    rejection = {'stage':'engineering_target_binding','native_execution_started':False}
    connection.executemany('INSERT INTO events VALUES(?,?,?,?,?)', [
        (1,'retry','c','run.created','{}'),
        (2,'retry','c','run.admission_rejected',json.dumps(rejection)),
        (3,'retry','c','run.state_changed','{}')])
    owner = type('Owner',(),{'_connection':lambda self:nullcontext(connection)})()
    def admitted():
        connection.commit()
        return HostStore.native_resume_latest_attempt_matches_checkpoint(owner,
            conversation_id='c',source_run_id='source',latest_run_id='retry',expected_error_detail='original text')
    assert admitted()
    connection.execute('UPDATE events SET payload_json=? WHERE sequence=2',
                       (json.dumps({**rejection,'native_execution_started':True}),))
    assert not admitted()
    connection.execute('UPDATE events SET payload_json=? WHERE sequence=2',(json.dumps(rejection),))
    connection.execute("INSERT INTO host_state VALUES('native_ld_receipt:retry:1','{}')")
    assert not admitted()
    connection.close()
