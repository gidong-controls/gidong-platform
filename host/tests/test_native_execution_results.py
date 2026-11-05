from controlstudio_host.native_execution_results import native_result_event, native_interrupted_event
from controlstudio_host.native_project_preparation import public_preparation_failure


def test_completion_sources_empty_allowed_run_has_no_support():
    from types import SimpleNamespace
    import pytest
    from controlstudio_host.native_completion import NativeCompletionLedger

    records = {}
    store = SimpleNamespace(get_host_state=lambda key: records.get(key),
                            set_host_state=lambda key, value: records.__setitem__(key, value),
                            get_native_checkpoint_state=lambda key: records.get(key))
    ledger = NativeCompletionLedger(store=store, conversation_id='c', run_id='current',
        request_message_id='request', workspace_id='w', revision_id='r', request_content='test',
        user_messages=[{'message_id': 'request', 'content': 'test'}], project_brief=None,
        snapshot=lambda full: {}, receipt_sequence=lambda: 0,
        allowed_runs={'current': 'request', 'old': 'request', 'empty': 'request'})
    from controlstudio_host.native_completion import canonical_digest
    from controlstudio_host.agent_protocol import scrub_native_tool_result
    raw = {'sequence': 0, 'tool_name': 'simulator', 'arguments': {'action': 'end', 'pid': 42, 'hwnd': 43,
           'expected_process_create_time': 123.0, 'project_path': 'project.xgwx', 'project_hash': 'a' * 64,
           'workspace_id': 'w', 'revision_id': 'r'},
           'result': {'ok': True, 'action': 'end', 'ended': True,
                      'current_session_inactive_verified': True, 'input_state_unknown': False}}
    records['native_ld_receipts:old'] = {'binding': {'workspace_id': 'w', 'revision_id': 'r'}}
    records['native_ld_receipt:old:00000000'] = raw
    evidence = {**ledger.scope, 'run_id': 'old', 'tool_name': raw['tool_name'], 'eligible_support': True,
        'outcome': 'completed', 'arguments_sha256': canonical_digest({'action': 'end'}),
        'result_sha256': canonical_digest(scrub_native_tool_result(raw['result'])),
        'raw_receipts': [{'sequence': 0, 'sha256': canonical_digest(raw)}]}
    evidence_ref = 'completion-evidence:' + canonical_digest(evidence)
    records['native_' + evidence_ref] = evidence
    from jsonschema import Draft202012Validator
    from controlstudio_contracts.native_completion import READ_COMPLETION_SOURCES_SCHEMA
    validator = Draft202012Validator(READ_COMPLETION_SOURCES_SCHEMA)
    assert validator.is_valid({'offset': 0, 'limit': 1})
    assert validator.is_valid({'run_id': 'empty'}) and validator.is_valid({'evidence_ref': evidence_ref})
    assert not validator.is_valid({'evidence_ref': evidence_ref, 'offset': 0})
    direct = ledger.read_sources({'evidence_ref': evidence_ref})
    assert direct['run_id'] == 'old' and direct['rows'][0]['completion_evidence_ref'] == evidence_ref
    before_digest = canonical_digest(raw)
    import json
    from controlstudio_host.agent_protocol import validated_native_checkpoint_snapshot
    archived_end = {'action': 'end', 'ok': True, 'completion_evidence_ref': evidence_ref,
                    'current_session_inactive_verified': True,
                    'current_session_observation': {'menu_direction_observation': {'inventory': 'x' * 19000}}}
    snapshot = validated_native_checkpoint_snapshot({'schemaVersion': '1', 'public_state': {
        'model_prompt': 'request', 'records': [], 'completed_pairs': [{
            'assistant': {'role': 'assistant', 'tool_calls': [{'id': 'end-call', 'type': 'function',
                'function': {'name': 'simulator', 'arguments': '{\"action\":\"end\"}'}}]},
            'results': [{'role': 'tool', 'tool_call_id': 'end-call', 'content': json.dumps(archived_end)}],
            'checkpoint_metadata': [{}],
        }]}, 'private_state': {'transport_records': []}})
    records['native_ld_checkpoint:old'] = {'binding': {'workspace_id': 'w', 'revision_id': 'r'}, 'checkpoint': snapshot}
    records['native_ld_checkpoint:current'] = {'binding': {'workspace_id': 'w', 'revision_id': 'r'},
                                               'checkpoint': {'public_state': {'records': [], 'completed_pairs': []}}}
    catalog = ledger.read_sources({'run_id': 'old', 'offset': 0, 'limit': 1})['rows'][0]
    assert catalog['completion_evidence_ref'] == evidence_ref
    assert catalog['support_available'] == direct['rows'][0]['support_available'] is True
    assert catalog['outcome'] == direct['rows'][0]['outcome'] == 'completed'
    assert canonical_digest(records['native_ld_receipt:old:00000000']) == before_digest
    raw['arguments']['action'] = 'start'
    with pytest.raises(ValueError, match='completion_receipt_changed'):
        ledger.read_sources({'evidence_ref': evidence_ref})
    altered = {**evidence, 'raw_receipts': [{'sequence': 0, 'sha256': canonical_digest(raw)}]}
    altered_ref = 'completion-evidence:' + canonical_digest(altered)
    records['native_' + altered_ref] = altered
    with pytest.raises(ValueError, match='completion_source_receipt_unresolved'):
        ledger.read_sources({'evidence_ref': altered_ref})
    raw['arguments']['action'] = 'end'
    foreign = {**evidence, 'workspace_id': 'other'}
    foreign_ref = 'completion-evidence:' + canonical_digest(foreign)
    records['native_' + foreign_ref] = foreign
    ledger.index_issued_references([foreign_ref, 'completion-evidence:' + '0' * 64])
    assert records['native_ld_receipts:old']['issued_evidence_by_sequence']['0'] == [evidence_ref]
    unknown = {**evidence, 'eligible_support': False, 'outcome': 'unknown'}
    unknown_ref = 'completion-evidence:' + canonical_digest(unknown)
    records['native_' + unknown_ref] = unknown
    records['native_ld_receipts:old']['issued_evidence_by_sequence']['0'] = [unknown_ref]
    unknown_row = ledger.read_sources({'run_id': 'old', 'offset': 0, 'limit': 1})['rows'][0]
    assert unknown_row['completion_evidence_ref'] == unknown_ref and unknown_row['support_available'] is False
    assert unknown_row['outcome'] == 'unknown'
    with pytest.raises(ValueError, match='completion_source_arguments_invalid'):
        ledger.read_sources({'evidence_ref': evidence_ref, 'offset': 0})
    page = ledger.read_sources({'run_id': 'empty', 'offset': 4, 'limit': 2})
    assert page['ok'] and page['read_only'] and not page['action_attempted']
    assert page['input_state_unknown'] is False and page['rows'] == [] and page['next_offset'] is None
    with pytest.raises(ValueError, match='completion_source_run_unavailable'):
        ledger.read_sources({'run_id': 'unknown'})
    with pytest.raises(ValueError, match='completion_source_page_invalid'):
        ledger.read_sources({'run_id': 'empty', 'limit': 0})
    for header in ({}, {'binding': {'workspace_id': 'other', 'revision_id': 'r'}}, 'invalid'):
        records['native_ld_receipts:empty'] = header
        with pytest.raises(ValueError, match='completion_source_workspace_mismatch'):
            ledger.read_sources({'run_id': 'empty'})


def test_saved_timer_configuration_uses_exact_hardware_context(monkeypatch, tmp_path):
    import asyncio
    import copy
    import hashlib
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from xml.etree import ElementTree
    from controlstudio_host import native_workspace_host as host

    import gzip
    runtime = ''.join(f'<{table}><{field}/></{table}>' for table, field in [
        ('ForAllocationTable', 'PulseTableData'), ('LabelAllocationTable', 'StringTableData'),
        ('ProgramTable', 'TableData'), ('PulseAllocationTable', 'PulseTableData'),
        ('SubroutineAllocationTable', 'StringTableData'), ('TaskTable', 'TableData')])
    project = ElementTree.fromstring('<Project><Configurations><Configuration WriteSignature="a">'
        '<Program>LD P00000; OUT P00020; END<Body><LDRoutine/></Body></Program>'
        '<OnlineElements>' + runtime + '<XGCommSettings Connnections="a"><RWItem_StateMemento/>'
        '</XGCommSettings></OnlineElements></Configuration></Configurations></Project>')
    saved_path = tmp_path / 'source.xgwx'
    def observe_saved():
        saved_path.write_bytes(gzip.compress(ElementTree.tostring(project)))
        return host.saved_project_observation(saved_path)
    original_saved = observe_saved()
    project.find('./Configurations/Configuration').set('WriteSignature', 'changed')
    project.find('./Configurations/Configuration/OnlineElements/XGCommSettings').set('Connnections', 'changed')
    rewritten_saved = observe_saved()
    assert original_saved['stable_during_read'] and rewritten_saved['stable_during_read']
    assert original_saved['sha256'] != rewritten_saved['sha256']
    assert original_saved['source_sha256'] == rewritten_saved['source_sha256']
    project.find('./Configurations/Configuration/Program').text = 'LD P00001; OUT P00020; END'
    assert observe_saved()['source_sha256'] != original_saved['source_sha256']
    assert '_source_snapshot' not in original_saved

    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / 'gidong-plc-mcp/src'))
    from xg5000_control.project import hardware_context as hardware
    sha, source = 'a' * 64, 'b' * 64
    xml = ElementTree.fromstring('''<Project><Configuration/><Parameter Type="BASIC PARAMETER"><XGTBasicParam
        T_100US_AREA_RANGE_START="0" T_100US_AREA_RANGE_END="0"
        T_001MS_AREA_RANGE_START="1000" T_001MS_AREA_RANGE_END="1023"
        T_010MS_AREA_RANGE_START="500" T_010MS_AREA_RANGE_END="999"
        T_100MS_AREA_RANGE_START="0" T_100MS_AREA_RANGE_END="499"/></Parameter></Project>''')
    monkeypatch.setattr(hardware, '_read_project_xml', lambda *args, **kwargs: (xml,
        {'project_sha256': sha, 'xml_sha256': 'c' * 64}))
    monkeypatch.setattr(hardware, 'catalog_path_for_bound_xg5000', lambda pid: None)
    identity = {'workspace_id': 'w', 'revision_id': 'r', 'session_id': 's', 'pid': 1, 'hwnd': 2,
        'project_path': 'C:/fixture/project.xgwx', 'project_hash': sha, 'source_kind': 'local_file',
        'provenance_receipt_digest': None, 'source_sha256': source,
        'working_path': 'C:/fixture/project.xgwx', 'working_sha256': sha}
    context = hardware.build_project_hardware_context(session_id='workspace:w:r', pid=0, hwnd=0,
        project_path=identity['project_path'], project_hash=sha)
    assert len(context['timer_area_sentinels']) == 1
    unsigned = {'schema': 'xg5000.workspace-hardware-context/1.0', 'workspace_id': 'w',
        'revision_id': 'r', 'source_kind': 'local_file', 'source_sha256': source,
        'working_sha256': sha, 'hardware_context': context}
    digest = lambda value: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()
    response = {**unsigned, 'receipt_digest': digest(unsigned), 'operation': 'workspace.hardware_context',
                'schema_version': '1'}
    monkeypatch.setattr(host, 'saved_project_observation', lambda *args, **kwargs:
        {'exists': True, 'size': 1, 'sha256': sha, 'stable_during_read': True})
    class Gateway:
        async def execute_agent_tool(self, name, arguments, *, session_identity):
            assert name == 'workspace.hardware_context' and session_identity == identity
            return copy.deepcopy(response)
    owner = host.NativeWorkspaceExecution(gateway=Gateway(), target=identity,
        current_target=lambda: identity, record_raw=lambda *args: None, refresh_saved=lambda value: None)
    async def symbols(arguments):
        return {'status': 'complete', 'source': {'sha256': sha, 'size': 1, 'stable_during_read': True},
                'symbols': {'coverage': {'truncated': False}}}
    monkeypatch.setattr(owner, '_read_saved_symbols', symbols)
    async def scenario():
        result = await owner._read_saved_project({})
        timers = result['timer_configuration']
        assert timers['status'] == 'observed' and timers['source_sha256'] == sha and not timers['blockers']
        assert [(x['startIndex'], x['endIndex'], x['timeBaseMs']) for x in timers['areas']] == [
            (0, 499, 100), (500, 999, 10), (1000, 1023, 1)]
        basic = next(node for node in xml.iter() if node.tag == 'XGTBasicParam')
        basic.set('T_010MS_AREA_RANGE_START', '400')
        overlap = hardware.build_project_hardware_context(session_id='workspace:w:r', pid=0, hwnd=0,
            project_path=identity['project_path'], project_hash=sha)
        unsigned['hardware_context'] = overlap
        response.update(hardware_context=overlap, receipt_digest=digest(unsigned))
        ambiguous = (await owner._read_saved_project({}))['timer_configuration']
        assert ambiguous['status'] == 'ambiguous' and 'timer_area_ranges_overlap' in ambiguous['blockers']
        response['working_sha256'] = 'd' * 64
        rejected = await owner._read_saved_project({})
        assert rejected['timer_configuration']['status'] == 'unavailable'
        assert rejected['timer_configuration']['areas'] == [] and rejected['ok'] is True
    asyncio.run(scenario())


def test_precontrol_end_navigation_unknown_has_current_bound_continuation(monkeypatch, capsys):
    import asyncio
    import copy
    import json
    import time
    from pathlib import Path
    from types import SimpleNamespace
    from controlstudio_host import native_workspace_host as host
    from controlstudio_contracts.native_operation_continuation import operation_constraints_digest
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / 'controlstudio-xg5000-cli/src'))
    monkeypatch.syspath_prepend(str(root / 'gidong-agent/src'))
    from controlstudio_xg5000_cli import simulator_entry as cli
    from controlstudio_xg5000_cli.vendor.app import uia_read_session
    from controlstudio_agent.native_current_state import simulator_observed_state

    saved = {'sha256': 'a' * 64, 'stable_during_read': True, 'size': 123}
    generation = ['host_verified_native_binding/v2:' + 'b' * 64]
    target = {'workspace_id': 'workspace', 'revision_id': 'revision', 'session_id': 'session',
              'pid': 1, 'hwnd': 2, 'project_path': 'C:/fixture/project.xgwx'}
    journal = {'state': 'loaded', 'loaded_sha256': saved['sha256']}
    fail_navigation, controls, fresh_directions, raw_receipts = [True], [], [], []

    class ReadSession:
        def __init__(self, **kwargs):
            assert (kwargs['expected_pid'], kwargs['root_hwnd'], kwargs['expected_process_create_time']) == (1, 2, 3.0)
            self.reusable = True
        def __enter__(self): return self
        def __exit__(self, *args): self.reusable = False

    def frame(request, **kwargs):
        assert (request['pid'], request['hwnd'], request['expected_process_create_time']) == (1, 2, 3.0)
        assert request['project_hash'] == saved['sha256']
        return SimpleNamespace()

    def saved_read(request, **kwargs):
        frame(request)
        return copy.deepcopy(saved)

    def prepare(pid, hwnd, *, navigation, deadline_monotonic, before_input, **kwargs):
        before_input()
        navigation.update(attempted=True, outcome='unknown')
        if fail_navigation[0]:
            raise cli.XG5000Error('SIMULATOR_MENU_NAVIGATION_NOT_OBSERVED', 'fixture observation failure')
        navigation.update(outcome='opened')
        return {'popup_hwnd': 3, 'deadline_monotonic': deadline_monotonic}

    def close(pid, hwnd, *, navigation, **kwargs):
        navigation['close_outcome'] = 'closed'

    def persist(request, value):
        journal.clear(); journal.update(copy.deepcopy(value))

    def direction(pid, hwnd, **kwargs):
        fresh_directions.append((pid, hwnd))
        return {'direction': 'end', 'end_direction_verified': True}

    def dispatch(pid, hwnd, *, preflight, before_input, navigation, **kwargs):
        assert journal['state'] == 'end_outcome_unknown'
        assert kwargs['expected_process_create_time'] == 3.0 and kwargs['preclosed_control'] is True
        preflight(); before_input()
        navigation['control_action_attempted'] = True
        controls.append((pid, hwnd))
        return {'end_direction_observation': {'direction': 'end'}, 'dispatch_state': 'dispatched'}

    monkeypatch.setattr(uia_read_session, 'MsaaReadSession', ReadSession)
    monkeypatch.setattr(cli, '_saved', saved_read)
    monkeypatch.setattr(cli, '_frame', frame)
    monkeypatch.setattr(cli, '_read_journal', lambda request: copy.deepcopy(journal))
    monkeypatch.setattr(cli, '_persist', persist)
    monkeypatch.setattr(cli, 'prepare_main_simulator_menu_observation', prepare)
    monkeypatch.setattr(cli, 'close_main_simulator_menu_observation', close)
    monkeypatch.setattr(cli, 'observe_main_simulator_end_direction', direction)
    monkeypatch.setattr(cli, 'invoke_main_simulator_end_resource_command', dispatch)
    monkeypatch.setattr(cli, 'verify_main_simulator_resource', lambda *args: {})
    monkeypatch.setattr(cli, '_post_end_menu_readiness', lambda *args, **kwargs: None)
    monkeypatch.setattr(cli.ui, 'verify_inactive_simulator_session', lambda *args, **kwargs: {
        'current_session_inactive_verified': True, 'read_only': True})
    monkeypatch.setattr(cli.ui, 'verify_simulator_target_status', lambda *args, **kwargs: {'verified': True})
    monkeypatch.setattr(cli, 'run_on_default_desktop', lambda operation, **kwargs: operation())
    monkeypatch.setattr(cli, 'operation_deadline', lambda seconds: time.monotonic() + seconds)
    monkeypatch.setattr(host, 'saved_project_observation', lambda *args, **kwargs: copy.deepcopy(saved))

    async def gateway(name, request):
        assert name == 'workspace.simulator'
        native = {key: value for key, value in request.items() if key not in {'workspace_id', 'revision_id'}}
        assert cli.main(['--request-json', json.dumps(native, sort_keys=True, separators=(',', ':'))]) == 0
        return json.loads(capsys.readouterr().out)['result']

    owner = host.NativeWorkspaceExecution(gateway=SimpleNamespace(call_workspace_tool=gateway),
        target=target, current_target=lambda: copy.deepcopy(target),
        record_raw=lambda name, arguments, raw: raw_receipts.append(copy.deepcopy(raw)),
        refresh_saved=lambda receipt: None, allow_simulator=True, expected_process_create_time=3.0,
        current_process_create_time=lambda: 3.0, recover_workspace=lambda owner: None,
        current_recovery_admission=lambda owner: generation[0])
    context = {'conversation_id': 'conversation', 'run_id': 'run', 'request_id': 'request', 'call_id': 'end-failed'}

    async def scenario():
        failed = await owner.execute_with_diagnostic_context('simulator', {'action': 'end'}, context)
        assert failed['input_state_unknown'] is True and not controls and not fresh_directions, failed
        assert journal['state'] == 'loaded'
        source = copy.deepcopy(owner._simulator_end_precontrol_source)
        assert source['source_raw_receipt_sha256'] == operation_constraints_digest(raw_receipts[-1])
        observed = await owner.execute_with_diagnostic_context('simulator', {'action': 'observe'},
            {**context, 'call_id': 'observe-current'})
        continuation = observed['current_operation_continuation']
        assert continuation['route_tool_name'] == 'simulator' and continuation['native_input_authority'] is False
        local = simulator_observed_state(observed)['current_constraints']['operation_local_continuation']
        assert local['previous_end_simulator_control_dispatched'] is False
        assert local['navigation_outcome_unknown'] is True and local['explicit_action'] == 'end'
        assert failed['input_state_unknown'] is True  # No historical settlement.
        for flag in (True, None):
            invalid = copy.deepcopy(raw_receipts[0])
            invalid['simulator_control_action_attempted'] = flag
            owner._retain_simulator_end_precontrol_source({'action': 'end'}, invalid, saved)
            assert owner._simulator_end_precontrol_source is None
        owner._simulator_end_precontrol_source = copy.deepcopy(source)
        generation[0] = 'host_verified_native_binding/v2:' + 'c' * 64
        assert owner._current_operation_continuation({'action': 'observe'}, copy.deepcopy(observed), saved) is None
        generation[0] = source['admitted_generation']
        owner._simulator_end_precontrol_source = copy.deepcopy(source)
        changed = {**saved, 'sha256': 'd' * 64}
        assert owner._current_operation_continuation({'action': 'observe'}, copy.deepcopy(observed), changed) is None
        assert owner._simulator_end_precontrol_source is None
        owner._simulator_end_precontrol_source = copy.deepcopy(source)
        fail_navigation[0] = False
        ended = await owner.execute_with_diagnostic_context('simulator', {'action': 'end'},
            {**context, 'call_id': 'fresh-end'})
        assert ended['ok'] is True and ended['current_session_inactive_verified'] is True
        assert controls == [(1, 2)] and fresh_directions == [(1, 2)]
        assert journal['state'] == 'ended' and failed['input_state_unknown'] is True
    asyncio.run(scenario())


def test_simulation_scenario_composes_requested_modes_without_replaying_unknown(monkeypatch):
    import asyncio
    import copy
    import sys
    import time
    import psutil
    from pathlib import Path
    from types import SimpleNamespace
    from controlstudio_host import native_workspace_host as module
    from controlstudio_host import native_simulator_sequence_control as control
    from controlstudio_host.native_completion import NativeCompletionLedger, canonical_digest
    import pytest
    from xg5000_control import server as mcp_server
    timer_step = {'case_label':'timer current', 'action':'read_words',
                  'devices':[{'area':'T','word':0}], 'expected':[{'area':'T','word':0,'value':5}]}
    timer_scenario = {'loaded_sha256':'a'*64,'stop_on_mismatch':True,'steps':[timer_step]}
    assert module._validated_simulation_scenario(timer_scenario) == [timer_step]
    timer_trace = {'trace':[{'devices':[{'area':'T','word':0,'value':5}]}]}
    assert module._simulation_expectations(timer_step,timer_trace)['matched'] is True
    acquisition = {'started_at': 100.0, 'completed_at': 100.02, 'duration_ms': 20.0,
                   'device_windows': [{'area':'T','word':0,'started_at':100.001,
                                       'completed_at':100.015,'duration_ms':14.0}]}
    timed_trace = {'trace':[{**timer_trace['trace'][0], 'acquisition':acquisition}]}
    assert module._simulation_expectations(timer_step,timed_trace)['matched'] is True
    assert timed_trace['trace'][0]['acquisition'] == acquisition
    invalid_trace = {'trace':[{**timer_trace['trace'][0], 'acquisition':{**acquisition,'duration_ms':float('nan')}}]}
    assert module._simulation_expectations(timer_step,invalid_trace)['native_samples_complete'] is False
    write_step = {'devices':[{'area':'M','bit':0,'value':True}],
                  'readback_devices':[{'area':'P','bit':1}]}
    sample = {'devices':write_step['devices'], 'observed_at':100.02,
              'transport':'XGT_FENET_SIMULATOR', 'atomic_snapshot':False}
    extra = {**sample,'devices':[{'area':'P','bit':1,'value':False}]}
    def projected(first, second):
        return module._write_readback_observation(write_step, {'memory':{
            'write_acknowledged':True,'readback_matched':True,'requested':write_step['devices'],
            'write_acknowledged_at':99.9,'readback':first,'observed_readback':second}}, 'a'*64)[2]
    assert 'acquisition' not in projected(sample, extra)
    timing = {key:acquisition[key] for key in ('started_at','completed_at','duration_ms')}
    assert projected({**sample,'acquisition':timing},{**extra,'acquisition':timing})['acquisition'] == timing
    with pytest.raises(ValueError):
        projected({**sample,'acquisition':timing},extra)
    timer_write = {**timer_step, 'action':'write_devices', 'devices':[{'area':'T','bit':0,'value':True}],
                   'expected':[{'area':'T','bit':0,'value':True}]}
    with pytest.raises(module.MCPToolError):
        module._validated_simulation_scenario({**timer_scenario,'steps':[timer_write]})
    mcp_words = mcp_server._native_simulator_tool['inputSchema']['properties']['devices']['items']['oneOf']
    assert 'T' in mcp_words[2]['properties']['area']['enum']
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / 'controlstudio-xg5000-cli/src'))
    from controlstudio_xg5000_cli import simulator_entry as entry, simulator_write_sequence as sequence
    from controlstudio_xg5000_cli.vendor.app import uia_read_session
    sha='a'*64
    arguments={'loaded_sha256':sha,'stop_on_mismatch':True,
        'run_before':True,'stop_after':True,'end_after':True,
        'steps':[{'case_label':label,'action':'write_devices',
            'devices':[{'area':'M','bit':0,'value':value}],
            'expected':[{'area':'M','bit':0,'value':value}]} for label,value in [('on',True),('off',False)]]}
    clock, calls, sessions, profiles, actual, cancelled = [100.0], [], [], [], {}, [False]
    mode, failure, admissions, bits = ['STOP'], [None], [0], {}
    saved={'sha256':sha,'stable_during_read':True,'size':200}
    monkeypatch.setattr(psutil,'Process',lambda pid:SimpleNamespace(create_time=lambda:42.0))
    monkeypatch.setitem(sys.modules,'win32gui',SimpleNamespace(IsWindow=lambda hwnd:True))
    monkeypatch.setitem(sys.modules,'win32process',SimpleNamespace(GetWindowThreadProcessId=lambda hwnd:(1,7)))
    monkeypatch.setattr(time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(module,'saved_project_observation',lambda *a,**k:copy.deepcopy(saved))
    monkeypatch.setattr(entry,'_saved',lambda *a,**k:copy.deepcopy(saved))
    monkeypatch.setattr(entry,'_read_journal',lambda request:{'state':'loaded','loaded_sha256':saved['sha256']})
    monkeypatch.setattr(entry,'XGSimFEnetBridge',lambda:SimpleNamespace(probe=lambda **k:None))
    monkeypatch.setattr(entry.ui,'observe_simulator_idle',lambda *a:{'verified':True})
    monkeypatch.setattr(entry.ui,'simulator_status_observation_budget',lambda deadline,phase:(4.0,deadline))
    class Session:
        def __init__(self,**kwargs):
            assert kwargs['deadline_monotonic']==190.0
            self.reusable=True;sessions.append(self)
        def __enter__(self):return self
        def __exit__(self,*args):self.reusable=False
        def close(self):self.reusable=False
        def inspect_msaa_status_panes(self,**kwargs):
            profiles.append((self,'status'));return [{'mode':mode[0]}]
        def inspect_msaa_tools_menu(self,**kwargs):
            profiles.append((self,'tools'));return {'actual_end_menu':True}
    monkeypatch.setattr(uia_read_session,'MsaaReadSession',Session)
    def mode_status(pid,hwnd,*,msaa_reader,**kwargs):
        nodes=msaa_reader(expected_pid=pid,hwnd=hwnd,timeout=4)
        return {'verified':True,'classification':nodes[0]['mode']}
    monkeypatch.setattr(entry.ui,'verify_simulator_mode_status',mode_status)
    def target(pid,hwnd,deadline,*,msaa_reader):
        return {'mode_observation':mode_status(pid,hwnd,msaa_reader=msaa_reader)}
    monkeypatch.setattr(entry,'_target',target)
    def write(bridge,devices,timeout,memory,**kwargs):
        calls.append(('write',devices[0]['value']))
        bits.update({(item['area'],item['bit']):item['value'] for item in devices})
        memory.update(write_acknowledged=True,readback_matched=True,
            readback={'devices':copy.deepcopy(devices),'observed_at':clock[0],
                'transport':'XGT_FENET_SIMULATOR','atomic_snapshot':False})
    monkeypatch.setattr(entry,'_write_bits',write)
    monkeypatch.setattr(entry,'_read_bits',lambda bridge,devices,timeout:{
        'observed_at':clock[0],'transport':'XGT_FENET_SIMULATOR','atomic_snapshot':False,
        'devices':[{**item,'value':bits.get((item['area'],item['bit']),False)} for item in devices]})
    monkeypatch.setattr(time,'sleep',lambda seconds:None)
    def lifecycle(request,deadline,state,*,msaa_status_reader,msaa_tools_reader):
        action=request['action'];calls.append(action)
        assert deadline==190.0 and request['project_hash']==saved['sha256']
        msaa_status_reader(expected_pid=7,hwnd=8,timeout=4)
        if action=='end':assert msaa_tools_reader(expected_pid=7,hwnd=8,timeout=4)['actual_end_menu']
        receipt={'ok':action!=failure[0],'action':action,'saved_project':copy.deepcopy(saved),
            'loaded_sha256':None if action=='end' else saved['sha256'],'input_state_unknown':action==failure[0],
            'action_attempted':True,'automatic_replay':False}
        state.update(receipt=receipt,input_may_have_occurred=True,input_state_unknown=action==failure[0])
        if action=='run':mode[0]='RUN'
        if action=='stop':mode[0]='STOP'
        if action=='end':
            receipt.update(ended=action!=failure[0],current_session_inactive_verified=action!=failure[0],
                navigation_input={'attempted':True,'outcome':'opened','close_outcome':'closed'})
            state['simulator_control_action_attempted']=True
        actual[action]=receipt
        return receipt
    monkeypatch.setattr(entry,'_execute_prepared_end',lifecycle)
    # Keep complete-plan validation and the real terminal MAC checks. Only the
    # private cancellation-event/process/file boundary stays in memory.
    holder={}
    def control_init(self,binding,steps,**kwargs):
        self.scope='1'*32;self.host={'pid':7,'process_create_time':42.0};self.key='2'*64
        self.binding_digest=control.digest(binding);self.plan_digest=control.digest(steps)
        self.native_deadline=190.0;self.return_deadline=205.0;self.terminal_verified=False
        root=Path.cwd()/('controlstudio-sim-sequence-'+self.scope+'-memory')
        self.control={'schema':control.CONTROL_SCHEMA,'scope':self.scope,'host':self.host,
            'binding_digest':self.binding_digest,'plan_digest':self.plan_digest,'key':self.key,
            'event_name':'Local\\ControlStudioSimSequence.'+self.scope,
            'owner_path':str(root/'owner.json'),'progress_path':str(root/'progress.json'),
            'native_deadline':190.0,'return_deadline':205.0}
        holder['control']=self
    monkeypatch.setattr(control.SimulatorSequenceControl,'__init__',control_init)
    monkeypatch.setattr(control.SimulatorSequenceControl,'revoke',lambda self:True)
    monkeypatch.setattr(control.SimulatorSequenceControl,'close',lambda self:None)
    monkeypatch.setattr(control.SimulatorSequenceControl,'terminal_owner',lambda self:holder['owner'])
    def owner_init(self,capability):self.control=capability;self.owner=None;self.sequence=0
    def admit(self):
        admissions[0]+=1
        if failure[0]=='cancel' and admissions[0]==4:cancelled[0]=True
        if failure[0]=='cancel_second' and admissions[0]==8:cancelled[0]=True
        return not cancelled[0]
    def publish_owner(self,pid):self.owner={'actual_worker_birth':42.0};holder['owner']=self.owner
    def publish(self,result,**kwargs):
        self.sequence+=1
        assert len(self._envelope(self._payload(owner_digest=sequence._digest(self.owner),
            sequence=self.sequence,terminal=kwargs.get('terminal',False),result=result)))<=sequence.MAX_PROGRESS_BYTES
    monkeypatch.setattr(sequence.SequenceOwner,'__init__',owner_init)
    monkeypatch.setattr(sequence.SequenceOwner,'admit',admit)
    monkeypatch.setattr(sequence.SequenceOwner,'publish_owner',publish_owner)
    monkeypatch.setattr(sequence.SequenceOwner,'publish',publish)
    monkeypatch.setattr(sequence,'_reparse',lambda path:False)
    monkeypatch.setattr(sequence.Path,'is_dir',lambda path:True)
    async def trial(fail=None, request=None, expected_plans=None, accepted_sha=None, native_chain=None,
                    postprocess_failure=False):
        request=copy.deepcopy(arguments if request is None else request)
        failure[0]=fail;mode[0]='STOP';admissions[0]=0;cancelled[0]=False
        saved.update(sha256=sha,size=200)
        if native_chain is not None:
            saved['size']=native_chain['before']['size']
            def observed(*args,**kwargs):
                receipt=copy.deepcopy(saved)
                if kwargs.get('include_source_snapshot'):
                    receipt['_source_snapshot']=copy.deepcopy(native_chain['before'] if saved['sha256']==sha else native_chain['after'])
                return receipt
            monkeypatch.setattr(module,'saved_project_observation',observed)
            monkeypatch.setattr(module,'_native_creation_parameter_origin',lambda *args:native_chain['origin'])
        else:
            monkeypatch.setattr(module,'saved_project_observation',lambda *a,**k:copy.deepcopy(saved))
        calls.clear();sessions.clear();profiles.clear();actual.clear();bits.clear();raw=[];gateway_calls=[]
        scenario_task=asyncio.current_task()
        async def execute(name,args):
            assert name=='workspace.simulator'
            action=args['action']
            if action in {'start','load'}:
                calls.append(action)
                assert args['project_hash']==sha
                failed=fail in {action,action+'_unknown'}
                receipt={'ok':not failed,'action':action,'saved_project':copy.deepcopy(saved),
                    'input_state_unknown':fail==action+'_unknown','action_attempted':True}
                if failed:receipt['error']={'kind':'actual_'+action+'_failure'}
                if action=='load' and not failed:
                    saved['sha256']=accepted_sha or sha
                    receipt.update(load_state='loaded',requested_sha256=sha,loaded_sha256=saved['sha256'],
                        saved_project=copy.deepcopy(saved),gui={'ok':True,'verified':True})
                    if saved['sha256']!=sha:
                        receipt['saved_version_transition']={'verified':True,'metadata_only':True,
                            'before':{'sha256':sha,'source_sha256':'c'*64},
                            'after':{'sha256':saved['sha256'],'source_sha256':'c'*64}}
                    if native_chain is not None:
                        receipt=copy.deepcopy(native_chain['receipt'])
                        saved.update(receipt['saved_project'])
                actual[action]=copy.deepcopy(receipt)
                if fail=='cancel_'+action:
                    asyncio.get_running_loop().call_soon(scenario_task.cancel)
                    await asyncio.sleep(0)
                return receipt
            assert action=='write_sequence'
            gateway_calls.append(copy.deepcopy(args))
            request={key:value for key,value in args.items() if key not in {'workspace_id','revision_id'}}
            return sequence.execute_write_sequence(request,220.0,{})
        target={'workspace_id':'w','revision_id':'r','session_id':'s','pid':7,'hwnd':8,
            'project_path':'C:/scenario.xgwx'}
        def refresh(receipt):
            if postprocess_failure:raise RuntimeError('private callback diagnostic is not public')
        owner=module.NativeWorkspaceExecution(gateway=SimpleNamespace(call_workspace_tool=execute),
            target=target,current_target=lambda:target,record_raw=lambda *args:raw.append(copy.deepcopy(args)),
            refresh_saved=refresh,allow_simulator=True,expected_process_create_time=42.0)
        schema=next(tool.parameters_json_schema for tool in owner.tool_catalog()
            if tool.tool_name=='execute_simulation_scenario')
        assert schema['oneOf']==[{'required':['loaded_sha256']},{'required':['initialize']}]
        assert 'loaded_sha256' not in schema['required'] and schema['properties']['initialize']['required']==['saved_sha256']
        result=await owner._execute_simulation_scenario(request)
        if expected_plans is None:expected_plans=[['run','write_devices','write_devices','stop','end']]
        assert [[step['action'] for step in call['sequence_steps']] for call in gateway_calls]==expected_plans
        assert len(sessions)==len(expected_plans) and all(not session.reusable for session in sessions)
        assert all(reader in sessions for reader,profile in profiles)
        assert all(step.get('samples',1)<=60 for call in gateway_calls for step in call['sequence_steps'])
        for action,receipt in actual.items():
            returned=result['lifecycle'][action]['result']
            assert (returned['native_receipt'] if postprocess_failure and action=='load' else returned)==module.scrub_native_tool_result(receipt)
            assert any(name=='simulator' and args['action']==action and value==receipt for name,args,value in raw)
        return result,list(calls)
    result,calls=asyncio.run(trial())
    assert calls==['run',('write',True),('write',False),'stop','end'] and result['ok'] is True
    assert result['batch']['completed_prefix_count']==2
    assert all(item['status']=='completed' for item in result['lifecycle'].values())
    assert any(profile=='tools' for reader,profile in profiles)
    def completion_sources(request, result, *, raw_result=None):
        records = {}
        store = SimpleNamespace(get_host_state=lambda key:copy.deepcopy(records.get(key)),
            set_host_state=lambda key,value:records.__setitem__(key,copy.deepcopy(value)))
        snapshot = {'workspace_id':'w','revision_id':'r','pid':7,'hwnd':8,
            'process_create_time':42.0,'project_path':'c:/scenario.xgwx','saved_sha256':sha}
        sequence = [0]
        ledger = NativeCompletionLedger(store=store,conversation_id='c',run_id='run',
            request_message_id='request',workspace_id='w',revision_id='r',request_content='actual test',
            user_messages=[{'message_id':'request','content':'actual test'}],project_brief={'project_name':'scenario'},
            snapshot=lambda full:copy.deepcopy(snapshot),receipt_sequence=lambda:sequence[0],
            allowed_runs={'run':'request'})
        store.set_host_state('native_ld_receipts:run',{'binding':{'workspace_id':'w','revision_id':'r'}})
        ledger.before('execute_simulation_scenario',request,'scenario')
        raw={'sequence':0,'tool_name':'execute_simulation_scenario','arguments':copy.deepcopy(request),
            'result':copy.deepcopy(result if raw_result is None else raw_result)}
        store.set_host_state('native_ld_receipt:run:00000000',raw);sequence[0]=1
        returned=ledger.record('execute_simulation_scenario',request,result,'scenario')
        row=ledger.read_sources({'run_id':'run'})['rows'][0]
        return ledger,store,sequence,returned,row,raw

    def assessment(ledger, refs):
        return {'schema_version':'1','scope':'request','requirement_ref':ledger.requirement_refs['request'],
            'criteria':[{'criterion':'model judges these actual observations','verdict':'met',
                'evidence_refs':refs,'reuse_basis':'current_execution'}],
            'diagnostic_refs':[],'remaining_work':[]}

    # Old unknown input remains historical; only a bound quiescence inspection
    # can retire its current-input obligation, even after later successful work.
    records = {}
    state_store = SimpleNamespace(get_host_state=lambda key:copy.deepcopy(records.get(key)),
        set_host_state=lambda key,value:records.__setitem__(key,copy.deepcopy(value)))
    snap = {'workspace_id':'w','revision_id':'r','pid':7,'hwnd':8,
        'process_create_time':42.0,'project_path':r'c:\scenario.xgwx','saved_sha256':sha}
    seq = [0]
    ledger = NativeCompletionLedger(store=state_store,conversation_id='c',run_id='run',
        request_message_id='request',workspace_id='w',revision_id='r',request_content='actual test',
        user_messages=[{'message_id':'request','content':'actual test'}],project_brief=None,
        snapshot=lambda fresh:copy.deepcopy(snap),receipt_sequence=lambda:seq[0],allowed_runs={'run':'request'})
    def ledger_call(action, result, *, raw_result=None):
        args = {'action':action}
        ledger.before('native_editor',args,action)
        raw_args = {'action':action,'workspace_id':'w','revision_id':'r','pid':7,'hwnd':8}
        raw = {'sequence':seq[0], 'tool_name':'native_editor', 'arguments':raw_args,
               'result':copy.deepcopy(raw_result if raw_result is not None else result)}
        state_store.set_host_state(f'native_ld_receipt:run:{seq[0]:08d}',raw)
        seq[0] += 1
        return ledger.record('native_editor',args,result,action)
    unknown = ledger_call('apply_ld_edits', {'ok':False,'input_state_unknown':True})
    saved_unknown = copy.deepcopy(ledger._evidence(unknown['completion_evidence_ref']))
    success = ledger_call('apply_ld_edits', {'ok':True,'input_state_unknown':False})
    assert state_store.get_host_state(ledger.key)['pending_other_input'] is True
    inspect_raw = {'ok':True,'action':'inspect_abandoned_operation','read_only':True,
        'action_attempted':False,'input_state_unknown':False,
        'operation_recovery':{'state':'not_quarantined','quarantined':False,'settled':False,
            'target_verified':True,'saved_sha256':sha,
            'process_evidence':{'inventory_complete':True,'live_candidate_count':0,'old_owner_live':False},
            'lifetime_marker':{'schema_version':'1',**snap,'project_path':'C:/scenario.xgwx','snapshot_stable':True}},
        'observation':{'pid':7,'hwnd':8,'main_hwnd':8,
            'operation_recovery_binding':{'verified':True,'unsaved_changes_preserved':False}}}
    public = copy.deepcopy(inspect_raw)
    public['operation_recovery'].pop('lifetime_marker')
    ledger_call('inspect_abandoned_operation',public,raw_result=inspect_raw)
    state = state_store.get_host_state(ledger.key)
    assert not state['pending_current_input'] and not state['pending_other_input']
    assert state['editor_dirty_since_rebound'] is True and state['saved_observed_epoch'] == 0
    assert ledger._evidence(unknown['completion_evidence_ref']) == saved_unknown
    assert saved_unknown['eligible_support'] is False
    assert ledger.evaluate(assessment(ledger,[success['completion_evidence_ref']]),'completed')['accepted'] is True
    ledger_call('apply_ld_edits', {'ok':False,'input_state_unknown':True})
    bad = copy.deepcopy(inspect_raw); bad['operation_recovery']['lifetime_marker']['pid'] = 9
    ledger_call('inspect_abandoned_operation',public,raw_result=bad)
    assert state_store.get_host_state(ledger.key)['pending_other_input'] is True

    for fail,expected in [('run',['run']),('stop',['run',('write',True),('write',False),'stop']),
                          ('end',['run',('write',True),('write',False),'stop','end'])]:
        result,calls=asyncio.run(trial(fail))
        assert calls==expected and result['ok'] is False and result['input_state_unknown'] is True
        assert result['lifecycle'][fail]['status']=='unknown'
        if fail!='end':assert result['lifecycle']['end']['status']=='not_started'
        if fail=='end':
            ledger,store,receipt_count,returned,row,raw=completion_sources(arguments,result)
            parent=ledger._evidence(returned['completion_evidence_ref'])
            assert parent['eligible_support'] is False and parent['outcome']=='unknown'
            parts=row['completion_evidence_parts']
            assert [part['source_selector'] for part in parts]==[['batch','children',0],['batch','children',1]]
            assert [part['source_selector'] for part in returned['completion_evidence_parts']] == [part['source_selector'] for part in parts]
            assert all(part['scope']=='finalization_only' and part['native_input_authority'] is False for part in parts)
            refs=[part['completion_evidence_ref'] for part in returned['completion_evidence_parts']]
            for ref in refs:ledger._verify_raw_refs(ledger._evidence(ref))
            import subprocess, os, json
            from pathlib import Path
            root=Path(__file__).resolve().parents[3]
            agent_python=root/'gidong-agent/.venv-dev/Scripts/python.exe'
            env={**os.environ, 'PYTHONPATH':os.pathsep.join((str(root/'gidong-agent/src'),
                str(root/'gidong-platform/packages/python/controlstudio-contracts/src')))}
            script='''import sys,json,copy
from controlstudio_agent.native_workspace import NativeLDWorkspaceOwner
from controlstudio_agent.working_history import SavedToolResults
data=json.load(sys.stdin); events=[]
def owner(emit): return NativeLDWorkspaceOwner(binding={'program_name':'Main'},store=SavedToolResults(),dispatch_native=None,observe_saved_project=None,save_project=None,emit_record=emit)
live=owner(events.append)
live.record_completed({'name':'execute_simulation_scenario','arguments':data['arguments'],'call_id':'scenario','native_step':1},data['result'])
def refs(value):
 page=value._current_state.view(historical_through_native_step=0)['domains']['simulation_test']['scenario_results']
 return [x.get('completion_evidence_ref') for x in page['scenarios'][0]['case_results']['items']]
assert refs(live)[:2]==data['refs']
cold=owner(lambda event:None); cold.restore_records(copy.deepcopy(events)); assert refs(cold)[:2]==data['refs']
'''
            process=subprocess.run([str(agent_python),'-c',script],input=json.dumps(
                {'arguments':arguments,'result':returned,'refs':refs}),text=True,env=env,capture_output=True,timeout=60)
            assert process.returncode == 0, process.stderr
            assert ledger.evaluate(assessment(ledger,refs),'completed')['reason_code']=='current_generation_not_settled'
            # The separate cleanup obligation must settle before finalization;
            # successful children do not rewrite the original aggregate.
            ended={'ok':True,'action':'end','ended':True,'current_session_inactive_verified':True,
                'input_state_unknown':False,'saved_project':{'sha256':sha,'stable_during_read':True}}
            ledger.before('simulator',{'action':'end'},'cleanup')
            store.set_host_state('native_ld_receipt:run:00000001',{'sequence':1,'tool_name':'simulator',
                'arguments':{'action':'end'},'result':ended});receipt_count[0]=2
            ledger.record('simulator',{'action':'end'},ended,'cleanup')
            assert ledger.evaluate(assessment(ledger,refs),'completed')['accepted'] is True
            reuse = assessment(ledger, [refs[0]])
            reuse['criteria'][0].update(reuse_basis='same_saved_source', change_impact='No source change')
            assert ledger.evaluate(reuse, 'completed')['accepted'] is True
            # The same original request may resume in another run without rewriting its evidence.
            def resumed(request_id='request'):
                return NativeCompletionLedger(store=store, conversation_id='c', run_id='resume',
                    request_message_id=request_id, workspace_id='w', revision_id='r',
                    request_content='actual test', user_messages=[{'message_id':request_id,'content':'actual test'}],
                    project_brief={'project_name':'scenario'}, snapshot=ledger.snapshot,
                    receipt_sequence=lambda:0, allowed_runs={'run':'request', 'resume':request_id})
            resume = resumed()
            same_request = copy.deepcopy(reuse)
            same_request['requirement_ref'] = resume.requirement_refs['request']
            assert resume.evaluate(same_request, 'completed')['accepted'] is True
            assert resume._evidence(refs[0])['run_id'] == 'run'
            current_only = copy.deepcopy(same_request)
            current_only['criteria'][0]['reuse_basis'] = 'current_execution'
            assert resume.evaluate(current_only, 'completed')['reason_code'] == 'support_scope_or_generation_mismatch'
            other = resumed('another-request')
            different = copy.deepcopy(same_request)
            different['requirement_ref'] = other.requirement_refs['request']
            assert other.evaluate(different, 'completed')['field_error']['type'] == 'request_mismatch'
            clean = store.get_host_state(ledger.key)
            original_clean = copy.deepcopy(clean)
            store.set_host_state(ledger.key, {**clean, 'editor_dirty_since_rebound': True})
            assert resume.evaluate(same_request, 'completed')['field_error']['type'] == 'editor_dirty_since_rebound_mismatch'
            store.set_host_state(ledger.key, clean)
            old_snapshot = resume.snapshot
            resume.snapshot = lambda fresh: {**old_snapshot(fresh), 'saved_sha256': 'f'*64}
            store.set_host_state(ledger.key, resume._state(resume.snapshot(True)))
            assert resume.evaluate(same_request, 'completed')['field_error']['type'] == 'saved_sha256_mismatch'
            store.set_host_state(ledger.key, clean)
            reuse['scope'] = 'project_goal'
            reuse['requirement_ref'] = ledger.requirement_refs['project_goal']
            assert ledger.evaluate(reuse, 'completed')['accepted'] is True
            original_sha = ledger.snapshot
            ledger.snapshot = lambda fresh: {**original_sha(fresh), 'saved_sha256':'f'*64}
            # Bind the new state so only the saved source comparison differs.
            changed_state = ledger._state(ledger.snapshot(True))
            store.set_host_state(ledger.key, changed_state)
            sha_rejection = ledger.evaluate(reuse, 'completed')
            assert sha_rejection['field_error']['type'] == 'saved_sha256_mismatch'
            ledger.snapshot = original_sha
            store.set_host_state(ledger.key, ledger._state(ledger.snapshot(True)))
            def exercise_save(save_result):
                probe, saved_store, counter, initial, _, _ = completion_sources(arguments, result)
                def record(tool, args, body, cid):
                    probe.before(tool, args, cid)
                    saved_store.set_host_state(f'native_ld_receipt:run:{counter[0]:08d}',
                        {'sequence':counter[0], 'tool_name':tool, 'arguments':args, 'result':body})
                    counter[0] += 1
                    return probe.record(tool, args, body, cid)
                record('simulator', {'action':'end'}, {'ok':True,'action':'end','ended':True,
                    'current_session_inactive_verified':True,'input_state_unknown':False}, 'end')
                record('native_editor', {'action':'apply_ld_edits'}, {'ok':True,'input_state_unknown':False}, 'edit')
                assert saved_store.get_host_state(probe.key)['editor_dirty_since_rebound'] is True
                record('read_saved_project', {}, {'ok':True,'sha256':sha,'stable_during_read':True}, 'read-before')
                assert saved_store.get_host_state(probe.key)['editor_dirty_since_rebound'] is True
                record('save_project', {}, save_result, 'save')
                return probe, saved_store, record, initial
            saved = {'workspace_id':'w','revision_id':'r','sha256':sha,'stable_during_read':True,'exists':True}
            save_ok = {'ok':True,'action_attempted':True,'input_state_unknown':False,'saved_project':saved}
            probe, saved_store, record, initial = exercise_save(save_ok)
            clean = saved_store.get_host_state(probe.key)
            assert clean['editor_dirty_since_rebound'] is False and clean['saved_observed_epoch'] == clean['epoch']
            read_after = record('read_saved_project', {}, {'ok':True,'exists':True,'sha256':sha,'stable_during_read':True}, 'read-after')
            goal = assessment(probe, [read_after['completion_evidence_ref']])
            goal['scope'] = 'project_goal'; goal['requirement_ref'] = probe.requirement_refs['project_goal']
            goal['criteria'][0].update(reuse_basis='same_saved_source',change_impact='No source change')
            review = probe.evaluate(goal,'completed')
            assert review['accepted'] is True, review.get('reason_code')
            for bad in ({**save_ok,'ok':False}, {**save_ok,'input_state_unknown':True},
                        {**save_ok,'saved_project':{**saved,'sha256':'f'*64}},
                        {**save_ok,'saved_project':{**saved,'workspace_id':'other'}}):
                failed_probe, failed_store, _, _ = exercise_save(bad)
                assert failed_store.get_host_state(failed_probe.key)['editor_dirty_since_rebound'] is True
            uncertain_probe, uncertain_store, uncertain_record, _ = exercise_save({**save_ok,'input_state_unknown':True})
            uncertain_record('save_project', {}, save_ok, 'later-save')
            assert uncertain_store.get_host_state(uncertain_probe.key)['pending_current_input'] is True
            batch_args = {'action':'apply_ld_edits','entries':[{'action':'write_ld_program','check_and_save':True}]}
            batch_child = {'ok':True,'declaration_matches':True,'check':{'status':'passed','has_errors':False},
                           'save_input':{'completed':True,'input_attempted':True,'input_state_unknown':False}}
            batch_result = {'ok':True,'action':'apply_ld_edits','input_state_unknown':False,'saved_project':saved,
                            'batch':{'all_completed':True,'completed_prefix_count':1,'entries':[{'index':0,'action':'write_ld_program','status':'completed','result':batch_child}]}}
            batch_probe, batch_store, batch_record, _ = exercise_save({**save_ok,'ok':False})
            batch_record('native_editor',batch_args,batch_result,'batch-save')
            assert batch_store.get_host_state(batch_probe.key)['editor_dirty_since_rebound'] is False
            for altered in ({**batch_result,'saved_project':{**saved,'stable_during_read':False}},
                            {**batch_result,'batch':{'all_completed':True,'entries':[{'action':'write_ld_program','status':'completed',
                                'result':{**batch_child,'save_input':{**batch_child['save_input'],'input_state_unknown':True}}}]}}):
                bad_probe, bad_store, bad_record, _ = exercise_save({**save_ok,'ok':False})
                bad_record('native_editor',batch_args,altered,'bad-batch-save')
                assert bad_store.get_host_state(bad_probe.key)['editor_dirty_since_rebound'] is True
            def metadata_transition(change=None, *, physical_change=False, edit_after=False, scenario=False):
                probe, saved_store, counter, _, _, _ = completion_sources(arguments, result)
                def recorded(tool, args, body, cid):
                    probe.before(tool, args, cid)
                    saved_store.set_host_state(f'native_ld_receipt:run:{counter[0]:08d}',
                        {'sequence':counter[0], 'tool_name':tool, 'arguments':args, 'result':body})
                    counter[0] += 1
                    return probe.record(tool, args, body, cid)
                recorded('simulator', {'action':'end'}, {'ok':True,'action':'end','ended':True,
                    'current_session_inactive_verified':True,'input_state_unknown':False}, 'end')
                original = recorded('read_saved_project', {}, {'ok':True,'exists':True,'sha256':sha,
                    'stable_during_read':True}, 'initial-read')
                goal = assessment(probe, [original['completion_evidence_ref']])
                goal.update(scope='project_goal',requirement_ref=probe.requirement_refs['project_goal'])
                goal['criteria'][0].update(reuse_basis='same_saved_source',change_impact='No source change')
                assert probe.evaluate(goal,'completed')['accepted'] is True
                prior_snapshot=probe.snapshot
                next_sha='b'*64; source_sha='c'*64
                load={'ok':True,'action':'load','input_state_unknown':False,'load_state':'loaded',
                    'requested_sha256':sha,'loaded_sha256':next_sha,'loaded_source_sha256':source_sha,
                    'gui':{'ok':True,'verified':True,'status':'passed',
                        'write_dialog':{'confirm':{'dispatch_state':'posted_once','control_id':1}},
                        'success_dialog':{'success_marker':True,'confirm':{'dispatch_state':'posted_once','control_id':2,'closed':True}},
                        'xgsim_probe':{'pass':True,'host':'127.0.0.1','port':2004,'transport':'XGT_FENET_SIMULATOR'}},
                    'saved_project':{'sha256':next_sha,'stable_during_read':True},
                    'saved_version_transition':{'verified':True,'metadata_only':True,
                        'before':{'sha256':sha,'source_sha256':source_sha},
                        'after':{'sha256':next_sha,'source_sha256':source_sha}}}
                if change: change(load)
                tool = 'execute_simulation_scenario' if scenario else 'simulator'
                args = {'initialize':{'saved_sha256':sha}} if scenario else {'action':'load'}
                body = {'ok':True,'input_state_unknown':False,'lifecycle':{'load':{'status':'completed','result':load}}} if scenario else load
                probe.before(tool,args,'load')
                next_snapshot={**prior_snapshot(False),'saved_sha256':next_sha}
                if physical_change: next_snapshot['pid'] += 1
                probe.snapshot=lambda fresh: copy.deepcopy(next_snapshot)
                if scenario:
                    saved_store.set_host_state(f'native_ld_receipt:run:{counter[0]:08d}',
                        {'sequence':counter[0],'tool_name':'simulator','arguments':{'action':'load'},'result':load})
                    counter[0]+=1
                saved_store.set_host_state(f'native_ld_receipt:run:{counter[0]:08d}',
                    {'sequence':counter[0],'tool_name':tool,'arguments':args,'result':body})
                counter[0]+=1; probe.record(tool,args,body,'load')
                if edit_after:
                    recorded('native_editor',{'action':'apply_ld_edits'}, {'ok':True,'input_state_unknown':False},'edit-after')
                return probe.evaluate(goal,'completed'), saved_store.get_host_state(probe.key)
            for scenario in (False,True):
                review, cohort = metadata_transition(scenario=scenario)
                assert review['accepted'] is True and set(cohort['saved_source_cohort']) == {sha,'b'*64}
            for change in (lambda x:x.update(input_state_unknown=True),
                           lambda x:x['saved_version_transition'].update(metadata_only=False),
                           lambda x:x['saved_version_transition']['after'].update(source_sha256='d'*64),
                           lambda x:x['saved_version_transition']['before'].update(sha256='e'*64),
                           lambda x:x.update(load_state='loading'),
                           lambda x:x['gui']['success_dialog']['confirm'].update(closed=False)):
                assert metadata_transition(change)[0]['accepted'] is False
            assert metadata_transition(physical_change=True)[0]['accepted'] is False
            assert metadata_transition(edit_after=True)[0]['accepted'] is False
            rejected_review=ledger.evaluate(assessment(ledger,[returned['completion_evidence_ref']]),'completed')
            assert rejected_review['reason_code']=='failed_or_unknown_support'
            assert rejected_review['field_error'] == {'loc':['completion_assessment','criteria',0,'evidence_refs',0],
                'type':'failed_or_unknown_support', 'reference':returned['completion_evidence_ref'],
                'eligible_support':False, 'outcome':'unknown'}
            # Exercise the real broker projection, not only the ledger: its
            # rejected aggregate must retain the Host-issued read-only lookup.
            from controlstudio_contracts import ExternalToolRequestV1
            from controlstudio_host.execution_broker import MCPExecutionBroker
            store.set_host_state(ledger.key, original_clean)
            broker=MCPExecutionBroker(None)
            broker.bind_native_run('c','run',SimpleNamespace(completion_ledger=ledger))
            broker_events=[]
            async def completion_lookup():
                async def event(kind,payload):broker_events.append((kind,payload))
                async def dispatch(name,args,call_id):
                    return await broker.execute_external_tool_request(ExternalToolRequestV1(
                        request_id=call_id,conversation_id='c',run_id='run',tool_name=name,
                        arguments=args,tool_call_id=call_id),sink=event)
                invalid_scope = copy.deepcopy(reuse)
                invalid_scope['scope'] = 'request'
                invalid_scope['requirement_ref'] = ledger.requirement_refs['request']
                scope_result = await dispatch('finish_editor_request', {'status':'completed',
                    'completion_assessment':invalid_scope}, 'scope-review')
                assert scope_result['completion_review']['accepted'] is True
                rejected=await dispatch('finish_editor_request',{'status':'completed',
                    'completion_assessment':assessment(ledger,[returned['completion_evidence_ref']])},'aggregate-review')
                review=rejected['completion_review']
                assert review['accepted'] is False and review['reason_code']=='failed_or_unknown_support'
                assert review['field_error'] == rejected_review['field_error']
                lookup=review['evidence_lookup']
                assert lookup==rejected_review['evidence_lookup']
                assert lookup['tool_name']=='read_completion_sources' and lookup['run_id']=='run'
                assert lookup['arguments']=={'evidence_ref':returned['completion_evidence_ref']}
                sources=await dispatch(lookup['tool_name'],lookup['arguments'],'read-parts')
                assert sources['read_only'] is True and sources['action_attempted'] is False
                assert sources['input_state_unknown'] is False
                assert len(sources['rows']) == 1 and sources['next_offset'] is None
                assert sources['rows'][0]['support_available'] is False and sources['rows'][0]['outcome']=='unknown'
                selected=sources['rows'][0]['completion_evidence_parts']
                assert [part['source_selector'] for part in selected]==[['batch','children',0],['batch','children',1]]
                assert [part['completion_evidence_ref'] for part in selected] == refs
                assert all(part['scope']=='finalization_only' and part['native_input_authority'] is False for part in selected)
                accepted=await dispatch('finish_editor_request',{'status':'completed',
                    'completion_assessment':assessment(ledger,[part['completion_evidence_ref'] for part in selected])},'parts-review')
                assert accepted['completion_review']['accepted'] is True
            asyncio.run(completion_lookup())
            paged=ledger.read_sources({'run_id':'run','offset':0,'limit':1})
            assert len(paged['rows']) == 1 and paged['next_offset']==1
            import pytest
            with pytest.raises(ValueError): ledger.read_sources({'evidence_ref':'invalid'})
            from controlstudio_host.native_completion import canonical_digest
            foreign={**parent,'workspace_id':'other'}
            foreign_ref='completion-evidence:'+canonical_digest(foreign)
            store.set_host_state('native_'+foreign_ref,foreign)
            with pytest.raises(ValueError,match='completion_source_scope_mismatch'):
                ledger.read_sources({'evidence_ref':foreign_ref})
            assert receipt_count[0]==2 and all(payload['tool_name'] in
                {'finish_editor_request','read_completion_sources'} for kind,payload in broker_events)
            assert ledger._evidence(returned['completion_evidence_ref'])==parent
            import pytest
            source=ledger._evidence(refs[0]);parent_ref=source['parent_evidence_ref']
            altered={**ledger._evidence(parent_ref),'result_sha256':'f'*64}
            store.set_host_state('native_'+parent_ref,altered)
            with pytest.raises(ValueError,match='unissued_evidence_ref'):
                ledger._verify_raw_refs(source)
            # A parent raw receipt digest is also checked, even if only one
            # selected child would otherwise support the model's criterion.
            original={**altered,'result_sha256':canonical_digest(module.scrub_native_tool_result(result))}
            store.set_host_state('native_'+parent_ref,original)
            raw['result']['lifecycle']['end']['status']='completed'
            store.set_host_state('native_ld_receipt:run:00000000',raw)
            with pytest.raises(ValueError,match='completion_receipt_changed'):
                ledger._verify_raw_refs(source)
    result,calls=asyncio.run(trial('cancel'))
    assert calls==['run',('write',True)] and result['batch']['cancelled'] is True
    assert result['batch']['completed_prefix_count']==1 and result['batch']['children'][1]['status']=='not_started'
    assert result['lifecycle']['stop']['status']==result['lifecycle']['end']['status']=='not_started'
    assert result['input_state_unknown'] is False
    ledger,store,receipt_count,returned,row,raw=completion_sources(arguments,result)
    assert [part['source_selector'] for part in row['completion_evidence_parts']]==[['batch','children',0]]
    # raw59's 96 planned reads and lifecycle share one CLI owner.
    bounded=copy.deepcopy(arguments)
    bounded['steps']=[]
    for i,(action,value,samples) in enumerate([
        ('write_devices',False,1),('wait_devices',False,20),
        ('write_devices',True,1),('wait_devices',True,20),
        ('write_devices',False,1),('wait_devices',False,20),
        ('write_devices',False,1),('write_devices',False,1),
        ('wait_devices',False,10),('write_devices',False,1),('wait_devices',False,20)]):
        step={'case_label':str(i),'action':action,'devices':[{'area':'M','bit':0,'value':value}],
            'expected':[{'area':'M','bit':0,'value':value}]}
        if action=='wait_devices':
            step['devices']=[{'area':'M','bit':0}];step.update(samples=samples,interval_ms=100)
        bounded['steps'].append(step)
    plans=[['run']+[step['action'] for step in bounded['steps']]+['stop','end']]
    result,executed=asyncio.run(trial(request=bounded,expected_plans=plans))
    assert result['ok'] is True and result['batch']['completed_prefix_count']==11
    assert [child['index'] for child in result['batch']['children']]==list(range(11))
    assert executed.count('run')==executed.count('stop')==executed.count('end')==1
    words=copy.deepcopy(bounded)
    words['steps'].append({'case_label':'counter','action':'read_words',
        'devices':[{'area':'D','word':0}], 'expected':[{'area':'D','word':0,'value':321}],
        'samples':2,'interval_ms':0})
    monkeypatch.setattr(entry,'_read_words',lambda bridge,devices,timeout:{
        'observed_at':clock[0], 'transport':'XGT_FENET_SIMULATOR', 'atomic_snapshot':False,
        'devices':[{**item,'value':321} for item in devices]})
    word_plan=[['run']+[step['action'] for step in words['steps']]+['stop','end']]
    word_result,_=asyncio.run(trial(request=words,expected_plans=word_plan))
    assert word_result['ok'] is True and word_result['batch']['children'][-1]['expectation']['matched'] is True
    for fail in ('stop','end'):
        result,executed=asyncio.run(trial(fail,request=bounded,expected_plans=plans))
        assert result['batch']['completed_prefix_count']==11 and result['input_state_unknown'] is True
        assert result['lifecycle'][fail]['status']=='unknown'
        if fail=='stop':assert result['lifecycle']['end']['status']=='not_started' and 'end' not in executed
    result,executed=asyncio.run(trial('cancel_second',request=bounded,expected_plans=plans))
    assert result['batch']['cancelled'] is True and result['batch']['completed_prefix_count']==5
    assert result['input_state_unknown'] is False
    assert all(child['status']=='not_started' for child in result['batch']['children'][5:])
    assert result['lifecycle']['stop']['status']==result['lifecycle']['end']['status']=='not_started'
    mismatch=copy.deepcopy(bounded)
    mismatch['steps'][5]['expected'][0]['value']=True
    result,executed=asyncio.run(trial(request=mismatch,expected_plans=plans))
    assert result['batch']['completed_prefix_count']==6 and result['ok'] is False
    assert result['batch']['stop_index']==5 and result['batch']['children'][5]['expectation']['matched'] is False
    assert all(child['status']=='not_started' for child in result['batch']['children'][6:])
    assert 'stop' not in executed and 'end' not in executed
    ledger,store,receipt_count,returned,row,raw=completion_sources(mismatch,result)
    assert [part['source_selector'][-1] for part in row['completion_evidence_parts']]==list(range(5))
    assert ledger._evidence(returned['completion_evidence_ref'])['eligible_support'] is False
    # Host admission is appended after the native receipt was stored.  It must
    # not hide completed cases or change the aggregate's failed outcome.
    for failed_index in (8, 4):
        partial = copy.deepcopy(bounded)
        partial['steps'][failed_index]['expected'][0]['value'] = not partial['steps'][failed_index]['expected'][0]['value']
        native, _ = asyncio.run(trial(request=partial, expected_plans=plans))
        public = copy.deepcopy(native)
        public['host_binding_admission'] = {'generation': 'host-issued'}
        ledger, store, _, issued, page, raw = completion_sources(partial, public, raw_result=native)
        assert [part['source_selector'][-1] for part in page['completion_evidence_parts']] == list(range(failed_index))
        assert ledger._evidence(issued['completion_evidence_ref'])['outcome'] == 'failed'
        for part in issued['completion_evidence_parts']:
            assert ledger._issued_source(part['completion_evidence_ref'])[2] == 0
            assert ledger.read_sources({'evidence_ref': part['completion_evidence_ref']})['rows'][0]['support_available'] is True
        parent = ledger._evidence(issued['completion_evidence_ref'])
        assert parent['result_sha256'] == canonical_digest(public)
        altered = copy.deepcopy(public)
        altered['batch']['children'][0]['case_label'] = 'foreign'
        assert ledger._issue_scenario_parts(issued['completion_evidence_ref'], parent, partial, altered) == []
        changed_raw = copy.deepcopy(raw)
        changed_raw['result']['batch']['children'][0]['case_label'] = 'foreign'
        store.set_host_state('native_ld_receipt:run:00000000', changed_raw)
        assert ledger._issue_scenario_parts(issued['completion_evidence_ref'], parent, partial, public) == []
    initialized=copy.deepcopy(bounded)
    initialized.pop('loaded_sha256');initialized['initialize']={'saved_sha256':sha}
    result,executed=asyncio.run(trial(request=initialized,expected_plans=plans,accepted_sha='b'*64))
    assert result['ok'] is True and executed[:3]==['start','load','run']
    assert result['batch']['requested_saved_sha256']==sha and result['batch']['loaded_sha256']=='b'*64
    assert result['lifecycle']['load']['result']['requested_sha256']==sha
    assert result['lifecycle']['load']['result']['loaded_sha256']=='b'*64
    assert all(child['result']['loaded_sha256']=='b'*64 for child in result['batch']['children'])
    for fail in ('start','start_unknown','load','load_unknown','cancel_start','cancel_load'):
        result,executed=asyncio.run(trial(fail,request=initialized,expected_plans=[]))
        assert result['ok'] is False and result['batch']['completed_prefix_count']==0
        assert all(child['status']=='not_started' for child in result['batch']['children'])
        assert 'run' not in executed and 'stop' not in executed and 'end' not in executed
        if fail.startswith('start') or fail=='cancel_start':assert 'load' not in executed
        assert result['input_state_unknown'] is fail.endswith('_unknown')
        assert result['batch']['cancelled'] is fail.startswith('cancel_')
    changed=copy.deepcopy(initialized);changed['initialize']['saved_sha256']='d'*64
    result,executed=asyncio.run(trial(request=changed,expected_plans=[]))
    assert executed==[] and result['input_state_unknown'] is False
    assert result['batch']['error']['kind']=='simulator_scenario_initialization_rejected'
    import pytest
    with pytest.raises(module.MCPToolError):
        module._validated_simulation_scenario({**initialized,'loaded_sha256':sha})
    with pytest.raises(module.MCPToolError):
        module._validated_simulation_scenario({**initialized,'initialize':{'saved_sha256':sha,'extra':True}})
    # cdab6972 raw06: initial native-default change, verified STOP, then a
    # second successful Write with a distinct requested/accepted container SHA.
    from xml.etree import ElementTree
    from controlstudio_contracts import native_simulator_saved_source as shared
    runtime=''.join(f'<{table}><{field}/></{table}>' for table,field in [
        ('ForAllocationTable','PulseTableData'),('LabelAllocationTable','StringTableData'),
        ('ProgramTable','TableData'),('PulseAllocationTable','PulseTableData'),
        ('SubroutineAllocationTable','StringTableData'),('TaskTable','TableData')])
    root=ElementTree.fromstring('<Project><Configurations><Configuration WriteSignature="a">'
        '<Program>LD P00000; OUT P00020; END</Program><Parameters><Parameter>'
        '<XGTBasicParam D_AREA_LATCH2_START_W="110" D_AREA_LATCH2_END_W="99"/>'
        '</Parameter></Parameters><OnlineElements>'+runtime+
        '<XGCommSettings Connnections="a"><RWItem_StateMemento/></XGCommSettings>'
        '</OnlineElements></Configuration></Configurations></Project>')
    def snapshot(root,digest,size):
        return shared.saved_project_xml_snapshot(root,sha256=digest,size=size,xml_sha256='f'*64)
    before=snapshot(root,sha,5728);baseline=snapshot(root,'d'*64,5728)
    native=copy.deepcopy(root)
    native.find('./Configurations/Configuration/Parameters/Parameter/XGTBasicParam').attrib.update(
        D_AREA_LATCH2_START_W='0',D_AREA_LATCH2_END_W='0')
    middle=snapshot(native,'c'*64,6264)
    native.find('./Configurations/Configuration').set('WriteSignature','b')
    after=snapshot(native,'b'*64,6268)
    workspace='00000000-0000-0000-0000-000000000001'
    provenance={'schema':'controlstudio-native-creation-provenance/v1','workspace_id':workspace,
        'creation_receipt_digest':'e'*64,'saved_project_sha256':baseline['sha256']}
    provenance['receipt_digest']=shared.canonical_digest(provenance)
    manifest={'source_kind':'new_build','workspace_id':workspace,'revision_id':'r','provenance':provenance,
        'source':{'sha256':baseline['sha256']},'working_copy':{'baseline_sha256':baseline['sha256']}}
    origin=shared.native_creation_parameter_origin_from_snapshots(before,baseline,manifest)
    gui={'ok':True,'verified':True,'status':'passed','write_dialog':{'confirm':{
        'dispatch_state':'posted_once','control_id':1}},'success_dialog':{'success_marker':True,
        'confirm':{'dispatch_state':'posted_once','control_id':2,'closed':True}},
        'xgsim_probe':{'pass':True,'host':'127.0.0.1','port':2004,'transport':'XGT_FENET_SIMULATOR'}}
    binding={'pid':7,'hwnd':8,'project_path':'C:/scenario.xgwx','expected_process_create_time':42.0}
    first={'schema':'controlstudio-simulator-completed-write/v1','binding':binding,'attempt_id':'1'*32,
        'requested_sha256':sha,'written_saved_project':{'sha256':'c'*64,'size':6264,'stable_during_read':True},
        'gui':gui,'saved_version_transition':shared.compare_simulator_save(before,middle)}
    receipt={'ok':True,'action':'load','requested_sha256':sha,'loaded_sha256':'b'*64,
        'loaded_source_sha256':after['source_sha256'],'final_load_requested_sha256':'c'*64,
        'load_attempt_id':'2'*32,'load_state':'loaded','initial_load_pending':False,'input_state_unknown':False,
        'saved_project':{'sha256':'b'*64,'size':6268,'stable_during_read':True},'gui':gui,
        'saved_version_transition':shared.compare_simulator_save(middle,after),
        'native_saved_version_load':{'requested_sha256':sha,'first_completed_write':first,
            'native_default_parameters':shared.native_default_parameter_change(origin,before,after),
            'stopped_before_second_write':{'operation':'stop','ok':True,'verified':True,
                'input_state_unknown':False,'postcondition':{'main_enabled':True,'ui_state':{
                    'verified':True,'classification':'STOP','pid':7,'hwnd':8,'process_create_time':42.0}}}}}
    chain={'before':before,'after':after,'origin':origin,'receipt':receipt}
    assert module.native_default_load_chain_verified(receipt,origin,before,after,binding)
    result,executed=asyncio.run(trial(request=initialized,expected_plans=plans,native_chain=chain))
    assert result['ok'] is True and result['batch']['loaded_sha256']=='b'*64
    result,executed=asyncio.run(trial(request=initialized,expected_plans=[],native_chain=chain,postprocess_failure=True))
    load=result['lifecycle']['load']
    assert load['status']=='failed' and result['input_state_unknown'] is False
    assert load['result']['native_receipt']==module.scrub_native_tool_result(receipt) and load['result']['error']=={
        'kind':'simulator_result_processing_failed','exception_kind':'RuntimeError'}
    assert result['batch']['completed_prefix_count']==0 and 'run' not in executed
    # The pre-dispatch plan enforces the existing wire/proof bounds, never splitting.
    assert len(module._simulation_lifecycle_plan(arguments['steps']*30,{'run':{}})) == 1
    with pytest.raises(module.MCPToolError):
        module._simulation_lifecycle_plan([{**arguments['steps'][0], 'case_label':'x'*120001}], {'run':{}})
    heavy={'case_label':'proof','action':'read_devices','devices':[{'area':'M','bit':i} for i in range(16)],
        'expected':[],'samples':60,'interval_ms':0}
    with pytest.raises(module.MCPToolError):
        module._simulation_lifecycle_plan([heavy]*10, {'run':{}})


def test_default_program_check_joins_only_its_new_options_and_issues_fresh_result_refs(monkeypatch):
    import asyncio
    import copy
    import json
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    import psutil
    from controlstudio_host.native_workspace_host import NativeWorkspaceExecution
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / 'controlstudio-xg5000-cli/src'))
    monkeypatch.syspath_prepend(str(root / 'gidong-plc-mcp/src'))
    from controlstudio_xg5000_cli.vendor.live import program_check as native
    from controlstudio_xg5000_cli.vendor.app import win32_messages
    from controlstudio_xg5000_cli.vendor.errors import XG5000Error
    from xg5000_control.project import operations

    clock, mode = [100.0], ['options']
    state = {'command': 0, 'ok': 0, 'modal': False, 'reads': 0}
    frame, dialog, button = 1, 2, 3
    target = {'workspace_id': 'w', 'revision_id': 'r', 'session_id': 's',
        'pid': 7, 'hwnd': frame, 'project_path': 'C:/unchanged.xgwx'}
    monkeypatch.setattr(native.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(native.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(psutil, 'Process', lambda pid: SimpleNamespace(create_time=lambda: 42.0))
    monkeypatch.setattr(native, 'get_window_pid', lambda hwnd: 7)
    monkeypatch.setitem(sys.modules, 'win32gui', SimpleNamespace(
        IsWindow=lambda hwnd: hwnd == frame or state['modal'],
        IsWindowVisible=lambda hwnd: hwnd == frame or state['modal'],
        IsWindowEnabled=lambda hwnd: hwnd != frame or not state['modal'],
        GetWindowRect=lambda hwnd: (0, 0, 100, 100), GetDlgCtrlID=lambda hwnd: 1,
        GetWindow=lambda hwnd, kind: 999 if mode[0] == 'foreign' else frame,
        GetWindowLong=lambda hwnd, index: 9 if hwnd in {10, 11} else 0,
        IsChild=lambda parent, child: parent == dialog and child in {button, 10, 11, 12}))
    options = [{'control_id': 100, 'text': '현재 프로그램', 'check_state': 1, 'enabled': True, 'visible': True},
               {'control_id': 101, 'text': '모든 프로그램', 'check_state': 0, 'enabled': True, 'visible': True}]
    # The real options inventory had lossy captions and no optional check_state.
    # The operation must read Unicode text and actual native default selections.
    def controls(*args):
        return [{'handle': handle, 'control_id': control_id, 'class_name': kind,
                 'text': '\ufffd', 'visible': True, 'enabled': True}
                for handle, control_id, kind in ((10, 100, 'Button'), (11, 101, 'Button'),
                    (12, 3676, 'ComboBox'), (button, 1, 'Button'))]
    captions = {dialog: '프로그램 검사 - VentilationPLC', button: '확인',
                10: '현재 프로그램', 11: '모든 프로그램', 12: '모두'}
    monkeypatch.setattr(win32_messages, 'read_window_text_with_timeout', lambda handle, **kwargs: captions[handle])
    def selection(handle, message, **kwargs):
        assert message == (0x0147 if handle == 12 else 0x00F0)
        return int(handle == 10)
    monkeypatch.setattr(win32_messages, 'send_message_timeout', selection)
    def owned(*args):
        return [{'hwnd': dialog, 'pid': 7, 'owner_hwnd': frame, 'class_name': '#32770',
            'title': '\ufffd'}] if state['modal'] else []
    def dispatch():
        state['command'] += 1
        state['modal'] = mode[0] != 'immediate'
        return {'command_id': 876}
    def post(hwnd, message, wparam, lparam):
        assert (hwnd, message, wparam, lparam) == (button, 0x00F5, 0, 0)
        state['ok'] += 1
        state['modal'] = False
    monkeypatch.setattr(win32_messages, 'post_message_checked', post)
    monkeypatch.setattr(operations, 'snapshot_program_check_dock_state', lambda *args:
        {'candidate_count': 1, 'candidates': [{'root_hwnd': 20, 'root_class_name': 'BCGPControlBar'}]}
        if state['command'] else {'candidate_count': 0, 'candidates': []})
    def capture(*args, **kwargs):
        state['reads'] += 1
        complete = mode[0] != 'partial' and state['reads'] > 1
        return {'read_complete': complete, 'messages_complete': True, 'read_errors': [], 'messages': ['오류 1개'],
            'tables': [{'read_complete': complete, 'identity_verified': True,
                'identity': {'pid': 7, 'main_hwnd': frame, 'process_create_time': 42.0, 'table_hwnd': 21},
                'row_count': 1, 'column_count': 1, 'columns': ['오류'],
                'rows': [{'index': 0, 'read_complete': complete, 'cells': ['실제 native 검사 오류']}]}]}
    monkeypatch.setattr(native, 'capture_program_check', capture)
    async def call(name, arguments):
        assert name == 'xg5000.editor' and arguments['action'] == 'program_check'
        try:
            job, check = native.run_default_program_check(7, frame, {'name': 'Main'},
                deadline_monotonic=100.25, observe_owned=owned, read_controls=controls, dispatch_command=dispatch)
            return {'ok': True, 'action': 'program_check', 'program_check': job, 'check': check}
        except XG5000Error as error:
            return {'ok': False, 'action': 'program_check', 'error': {'code': error.code}, **error.details}
    owner = NativeWorkspaceExecution(gateway=SimpleNamespace(call_workspace_tool=call), target=target,
        current_target=lambda: copy.deepcopy(target), record_raw=lambda *args: None, refresh_saved=lambda *args: None,
        program_name='Main', expected_process_create_time=42.0, current_process_create_time=lambda: 42.0)
    async def scenario():
        for kind in ('options', 'immediate'):
            mode[0] = kind; clock[0] = 100.0
            state.update(command=0, ok=0, modal=False, reads=0)
            result = await owner.execute('native_editor', {'action': 'program_check', 'program_name': 'Main'})
            assert result['ok'] is True and result['program_check']['completed'] is True
            assert state['command'] == 1 and state['ok'] == (kind == 'options') and state['reads'] == 2
            assert result['check']['status'] == 'failed'  # A completed check may report actual errors.
            reference = result['check']['tables'][0]['result_ref']
            assert owner.check_results[reference]['table']['rows'][0]['cells'] == ['실제 native 검사 오류']
            if kind == 'options':
                assert result['program_check']['options']['title'] == captions[dialog]
                assert result['program_check']['options']['controls'] == [*options,
                    {'control_id': 3676, 'text': '모두', 'selected_index': 0, 'enabled': True, 'visible': True}]
                assert result['program_check']['options']['changed'] is False
        for kind, preexisting in (('options', True), ('foreign', False), ('partial', False)):
            mode[0] = kind; clock[0] = 100.0
            state.update(command=0, ok=0, modal=preexisting, reads=0)
            failed = await owner.execute('native_editor', {'action': 'program_check', 'program_name': 'Main'})
            assert failed['ok'] is False and failed['program_check']['completed'] is False
            assert owner.check_results == {} and 'result_ref' not in json.dumps(failed)
            assert state['command'] == (not preexisting) and state['ok'] == (kind == 'partial')
            assert failed['input_state_unknown'] is (not preexisting)
    asyncio.run(scenario())


def test_partial_batch_keeps_completed_prefix_and_unknown_suffix(monkeypatch):
    result = {"ok": False, "batch": {"completed_prefix_count": 2, "all_completed": False,
        "entries": [{"index": 0, "status": "completed"}, {"index": 1, "status": "completed"},
                    {"index": 2, "status": "unknown"}, {"index": 3, "status": "not_started"}]},
        "post_batch_capture": {"status": "failed", "private_path": "secret"}, "pid": 123}
    event = native_result_event("native_editor", {"action": "apply_ld_edits"}, result)
    assert event["native_result"] == {"schemaVersion": "1", "action": "apply_ld_edits", "state": "unknown",
        "completedPrefixKnown": True, "completedPrefixCount": 2, "totalEdits": 4, "failedIndices": [],
        "unknownIndices": [2], "notStartedCount": 1, "postCaptureState": "failed"}
    assert "2건 입력 완료" in event["activity_result"]
    assert "secret" not in repr(event) and "pid" not in event["native_result"]
    import ast, asyncio, json, os, subprocess
    from pathlib import Path
    from types import SimpleNamespace
    from jsonschema import validate
    from controlstudio_contracts import ExternalToolRequestV1
    from controlstudio_contracts.native_ld_commands import native_ld_entry_seconds
    from controlstudio_host.mcp_client import MCPGateway, MCPToolPreflightError, _editor_batch_sdk_timeout
    from controlstudio_host.execution_broker import MCPExecutionBroker
    root = Path(__file__).resolve().parents[3]
    # Read the actual server schema without importing/starting its runtime.
    specs = []
    for node in ast.walk(ast.parse((root/'gidong-plc-mcp/src/xg5000_control/server.py').read_text(encoding='utf-8'))):
        if isinstance(node, ast.Dict) and any(isinstance(key, ast.Constant) and key.value == 'name'
                and isinstance(value, ast.Constant) and value.value == 'entries' for key, value in zip(node.keys, node.values)):
            spec = ast.literal_eval(node)
            if 'write_ld_program' in spec.get('items', {}).get('properties', {}).get('action', {}).get('enum', []):
                specs.append(spec)
    assert len(specs) == 1
    # The Agent has a different Python ABI; exercise its real exposed schema,
    # resolver and normalizer in its existing environment with a no-input owner.
    script = '''
import asyncio,json
from controlstudio_agent import native_editor as native
from controlstudio_agent.native_workspace import reference_tools,resolve_batch
from controlstudio_agent.working_history import SavedToolResults
entries=[{'action':'insert_ld_row_before','args':{'visual_row':4,'column':94}},
 {'action':'write_ld_element','args':{'visual_row':4,'cell':0,'element_kind':'NC','operand':'M00000','expect_empty':True}},
 {'action':'insert_ld_instruction','args':{'visual_row':4,'cell':'end','text':'TON T0000 10'}},
 {'action':'insert_ld_row_after','args':{'visual_row':4,'column':88,
  'expected_element':{'opcode':'33','coordinate':4184,'value':'TON,T0000,10'}}}]
schema=next(t['function']['parameters'] for t in reference_tools(service=True,compact_references=True)
 if t['function']['name']=='editor_apply_edits')
allowed={o['properties']['action']['enum'][0] for o in schema['properties']['entries']['oneOf'][1]['items']['anyOf']}
assert {e['action'] for e in entries}<=allowed
values,_=resolve_batch(SavedToolResults(),{'entries':entries},'Main')
class Owner:
 async def editor(self,name,args):
  assert name=='editor_apply_edits'
  return dict(action='apply_ld_edits',pid=1,hwnd=2,program_name='Main',**args)
print(json.dumps(asyncio.run(native._apply_edits(Owner(),values,step=1,secrets=[]))))
'''
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(str(root/path) for path in (
        'gidong-agent/src', 'gidong-platform/packages/python/controlstudio-contracts/src'))}
    produced = subprocess.run([str(root/'gidong-agent/.venv/Scripts/python.exe'), '-B', '-c', script],
        env=env, capture_output=True, text=True, timeout=60)
    assert produced.returncode == 0, produced.stdout + produced.stderr
    request = json.loads(produced.stdout)
    validate(request['entries'], {key:value for key,value in specs[0].items() if key != 'name'})
    monkeypatch.syspath_prepend(str(root/'controlstudio-xg5000-cli/src'))
    monkeypatch.syspath_prepend(str(root/'gidong-plc-mcp/src'))
    from controlstudio_xg5000_cli import editor_entry
    from xg5000_control.cli_adapter import editor_batch_timeout_seconds, _canonical_json
    assert editor_entry._request(['--request-json', _canonical_json(request)])['entries'] == request['entries']
    budget = 90.0 + sum(native_ld_entry_seconds(entry['action']) for entry in request['entries'])
    assert _editor_batch_sdk_timeout('xg5000.editor', request) == budget
    assert editor_batch_timeout_seconds(request) <= budget
    async def transport():
        calls, events = [], []
        async def wire(name, args, **kwargs):
            calls.append((name, args, kwargs))
            return SimpleNamespace(is_error=False, structured_content={'ok':True})
        gateway = MCPGateway.__new__(MCPGateway)
        gateway._client=SimpleNamespace(call_tool=wire)
        gateway._status=SimpleNamespace(status='ready');gateway._call_lock=asyncio.Lock()
        assert await gateway.call_workspace_tool('xg5000.editor', request) == {'ok':True}
        assert len(calls) == 1 and calls[0][2]['timeout'] == budget
        async def execute(name, args):return await gateway.call_workspace_tool('xg5000.editor', args)
        async def sink(kind, payload):events.append((kind,payload))
        broker=MCPExecutionBroker(gateway);broker.bind_native_run('c','run',SimpleNamespace(execute=execute))
        for bad in ({**request,'entries':[{'action':'not_a_native_edit'}]}, {**request,'capture_after':'yes'}):
            try:_editor_batch_sdk_timeout('xg5000.editor',bad)
            except MCPToolPreflightError as error:
                assert error.as_result()['status'] == 'not_started'
            else:raise AssertionError('Invalid batch reached timeout admission')
            known=await broker.execute_external_tool_request(ExternalToolRequestV1(request_id='invalid',
                conversation_id='c',run_id='run',tool_name='native_editor',arguments=bad,tool_call_id='invalid'),sink=sink)
            assert known['status']=='not_started' and known['action_attempted'] is False
            assert known['input_state_unknown'] is False and len(calls)==1
        assert all(payload.get('failure_kind')!='host_policy' for _,payload in events)
    asyncio.run(transport())


def test_missing_batch_reply_never_claims_zero_inputs():
    event = native_result_event("native_editor", {"action": "apply_ld_edits"}, {
        "ok": False, "batch_result": "unknown", "completed_prefix_known": False})
    assert event["native_result"]["state"] == "unknown"
    assert event["native_result"]["completedPrefixKnown"] is False
    assert "completedPrefixCount" not in event["native_result"]
    assert native_interrupted_event("save_project", {}, "transport")["native_result"]["state"] == "unknown"


def test_save_input_is_separate_from_hashed_saved_observation(monkeypatch):
    dispatched = native_result_event("save_project", {}, {"ok": True, "input_events_inserted": 4})
    assert "savedFile" not in dispatched["native_result"]
    observed = native_result_event("read_saved_project", {}, {
        "exists": True, "size": 37, "stable_during_read": True, "sha256": "a" * 64,
        "path": "C:/private/project.xgwx", "credentials": "secret"})
    assert observed["native_result"]["savedFile"]["sha256"] == "a" * 64
    assert "private" not in repr(observed) and "secret" not in repr(observed)
    malformed = native_result_event("read_saved_project", {}, {"exists": True, "size": {"secret": "bad"}})
    assert "size" not in malformed["native_result"]["savedFile"]
    import asyncio
    from copy import deepcopy
    from controlstudio_host.native_workspace_host import NativeWorkspaceExecution
    async def save_and_observe():
        target = {'workspace_id': 'w', 'revision_id': 'r', 'session_id': 's',
            'pid': 1, 'hwnd': 2, 'project_path': 'C:/bound/project.xgwx'}
        calls, observations = [], []
        native = {'ok': True, 'action': 'key', 'action_attempted': True,
            'input_events_inserted': 4, 'input_state_unknown': False}
        saved = {'ok': True, 'exists': True, 'stable_during_read': True, 'sha256': 'a' * 64,
            'saved_sections': {'status': 'complete'},
            'simulator_source_comparison': {'current_saved': {'sha256': 'a' * 64, 'source_sha256': 'b' * 64}}}
        class Gateway:
            async def call_workspace_tool(self, name, arguments):
                assert name == 'xg5000.editor' and arguments['action'] in ('key', 'apply_ld_edits')
                calls.append(arguments)
                return deepcopy(native)
        owner = NativeWorkspaceExecution(gateway=Gateway(), target=target, current_target=lambda: target,
            record_raw=lambda *args: None, refresh_saved=lambda *args: None)
        async def observe(arguments):
            assert arguments == {}
            observations.append('disk')
            if isinstance(saved, Exception): raise saved
            return deepcopy(saved)
        monkeypatch.setattr(owner, '_read_saved_project', observe)
        result = await owner._execute('save_project', {})
        assert result['input_events_inserted'] == 4 and result['saved_project'] == saved
        assert len(calls) == len(observations) == 1
        saved = {'ok': False, 'stable_during_read': False, 'error': {'kind': 'saved_file_unstable'}}
        result = await owner._execute('save_project', {})
        assert result['ok'] is True and result['input_state_unknown'] is False
        assert result['saved_project'] == saved and 'sha256' not in result['saved_project']
        saved = OSError('read failed')
        result = await owner._execute('save_project', {})
        assert result['ok'] is True and result['saved_project']['ok'] is False
        assert result['saved_project']['error']['kind'] == 'saved_project_read_failed'
        native.update(ok=False, error={'kind': 'native_save_failed'}, input_state_unknown=True)
        result = await owner._execute('save_project', {})
        assert result['ok'] is False and result['input_state_unknown'] is True and 'saved_project' not in result
        assert len(calls) == 4 and len(observations) == 3  # No automatic save replay.
        rungs = [{'branches': [[{'kind': 'NO', 'operand': 'P00000'}]], 'terminal': {'kind': 'APP', 'text': 'TON T0 10'}},
                 {'branches': [[{'kind': 'NO', 'operand': 'T0'}]], 'terminal': {'kind': 'OUT', 'operand': 'P00020'}}]
        entry = {'action': 'write_ld_program', 'rungs': rungs, 'check_and_save': True,
                 'expected_source_sha256': 'a' * 64}
        child = {'ok': True, 'declaration_matches': True, 'check': {'status': 'passed', 'has_errors': False},
                 'save_input': {'completed': True, 'input_attempted': True, 'input_state_unknown': False}}
        native = {'ok': True, 'action': 'apply_ld_edits', 'input_state_unknown': False,
                  'batch': {'all_completed': True, 'entries': [{'action': 'write_ld_program', 'status': 'completed', 'result': child}]}}
        saved = {'ok': True, 'exists': True, 'stable_during_read': True, 'sha256': 'b' * 64}
        before = len(observations)
        result = await owner._execute('native_editor', {'action': 'apply_ld_edits', 'program_name': 'MAIN', 'capture_after': True, 'entries': [entry]})
        assert result['saved_project'] == saved and len(observations) == before + 1
        child['save_input']['input_state_unknown'] = True
        result = await owner._execute('native_editor', {'action': 'apply_ld_edits', 'program_name': 'MAIN', 'capture_after': True, 'entries': [entry]})
        assert 'saved_project' not in result and len(observations) == before + 1
    asyncio.run(save_and_observe())
    pending = native_result_event("simulator", {"action": "start"}, {"ok": True,
        "gui": {"status": "write_dialog_ready", "verified": True, "write_dialog": {"hwnd": 123}},
        "initial_load_pending": True, "loaded_sha256": None, "runtime_behavior_verified": False})
    assert pending["activity_label"] == "XG-SIM 시작"
    assert pending["native_result"]["initialLoadPending"] is True
    assert pending["native_result"]["runtimeBehaviorVerified"] is False
    assert "loadedSha256" not in pending["native_result"] and "hwnd" not in repr(pending)
    assert "아직 완료되지" in pending["activity_result"]
    unverified = native_result_event("simulator", {"action": "load"}, {"ok": True,
        "saved_project": {"sha256": "a" * 64}, "loaded_sha256": "b" * 64})
    assert unverified["native_result"]["loadedVersionMatchesSaved"] is False
    assert "미확인" in unverified["activity_result"]


def test_preparation_partial_field_receipts_do_not_expose_destination_text():
    result = public_preparation_failure({"ok": False, "error": {"kind": "NATIVE_PREPARATION_CHOICE_UNAVAILABLE",
        "details": {"requested": "wrong CPU", "choices": ["observed CPU"], "project_path": "C:/private/destination"}},
        "preparation_receipts": [{"action": "set_field", "receipt": {"action_attempted": True,
        "input_events_inserted": 8, "requested": "C:/private/destination", "control_hwnd": 123}}]})
    assert result["native_choices"] == ["observed CPU"]
    assert result["preparation_receipts"] == [{"action": "set_field", "action_attempted": True, "input_events_inserted": 8}]
    assert "private" not in repr(result) and "hwnd" not in repr(result)


def test_bound_editor_activate_rejects_model_selected_targets(monkeypatch):
    import asyncio
    import copy
    from types import SimpleNamespace
    from controlstudio_host.native_workspace_host import NativeWorkspaceExecution

    target = {'workspace_id': 'w', 'revision_id': 'r', 'session_id': 's', 'pid': 7,
              'hwnd': 11, 'project_path': 'C:/fixture/project.xgwx', 'project_hash': 'a' * 64}
    calls = []
    async def gateway(name, request):
        calls.append((name, copy.deepcopy(request)))
        return {'ok': True, 'action': 'activate', 'action_attempted': True}
    owner = NativeWorkspaceExecution(gateway=SimpleNamespace(call_workspace_tool=gateway),
        target=target, current_target=lambda: copy.deepcopy(target), record_raw=lambda *args: None,
        refresh_saved=lambda *args: None, expected_process_create_time=3.0,
        current_process_create_time=lambda: 3.0)
    async def run():
        assert (await owner.execute('native_editor', {'action': 'activate'}))['ok'] is True
        assert calls == [('xg5000.editor', {'action': 'activate', 'pid': 7, 'hwnd': 11})]
        for field in ('pid', 'hwnd', 'control_hwnd'):
            rejected = await owner.execute('native_editor', {'action': 'activate', field: 99})
            assert rejected['error']['kind'] == 'invalid_arguments'
            assert rejected['input_state_unknown'] is False
        assert len(calls) == 1
    asyncio.run(run())

    # A current native menu is not issued a modeless close capability.
    async def observe_gateway(name, request):
        return {'ok': True, 'observation': {'pid': 7, 'main_hwnd': 11,
            'owned_modals': [], 'owned_windows': [
                {'hwnd': 12, 'pid': 7, 'root_owner_hwnd': 11, 'owner_hwnd': 11,
                 'visible': True, 'enabled': True, 'title': '', 'class_name': 'Afx:menu',
                 'surface_kind': 'native_menu'},
                {'hwnd': 13, 'pid': 7, 'root_owner_hwnd': 11, 'owner_hwnd': 11,
                 'visible': True, 'enabled': True, 'title': 'panel', 'class_name': 'panel'}]}}
    owner.gateway = SimpleNamespace(call_workspace_tool=observe_gateway)
    asyncio.run(owner.execute('native_editor', {'action': 'observe'}))
    assert [item['hwnd'] for item in owner.owned_ui.values()] == [13]

    # A stale generic reference cannot dispatch WM_CLOSE to a live menu either.
    import sys, time, pytest
    from controlstudio_xg5000_cli import editor_entry as editor
    from controlstudio_xg5000_cli.vendor.live import simulator_commands
    from controlstudio_xg5000_cli.vendor.errors import XG5000Error
    gui = SimpleNamespace(GetWindow=lambda *a: 11, GetWindowLong=lambda *a: 0,
                          GetClassName=lambda h: 'Afx:menu')
    con = SimpleNamespace(GW_OWNER=1, GWL_EXSTYLE=2, WS_EX_MDICHILD=4, WM_CLOSE=16)
    monkeypatch.setitem(sys.modules, 'win32gui', gui)
    monkeypatch.setitem(sys.modules, 'win32con', con)
    monkeypatch.setattr(editor, 'list_main_instances', lambda **kw: [
        SimpleNamespace(hwnd=11, process_create_time=3.0)])
    monkeypatch.setattr(editor, '_assert_target', lambda *a: None)
    monkeypatch.setattr(editor, '_assert_action_surface', lambda *a: None)
    monkeypatch.setattr(editor, '_owned_modals', lambda *a: [])
    monkeypatch.setattr(editor, '_owned_windows', lambda *a: [{'hwnd': 12}])
    monkeypatch.setattr(simulator_commands, '_owned_menu_popups', lambda *a: [12])
    dispatched = []
    monkeypatch.setattr(editor, 'post_message_checked', lambda *a: dispatched.append(a))
    receipt = {'action_attempted': False, 'input_events_inserted': 0}
    with pytest.raises(XG5000Error, match='not a modeless close target'):
        editor._execute_action({'pid': 7, 'hwnd': 12, 'action': 'close_owned_window'},
                               receipt, time.monotonic() + 10, None)
    assert receipt['action_attempted'] is False and not dispatched


def test_stale_saved_hash_only_blocks_created_empty_initialization(monkeypatch):
    import asyncio
    import copy
    from types import SimpleNamespace
    from controlstudio_host import native_workspace_host as host
    target = {'workspace_id': 'w', 'revision_id': 'r', 'session_id': 's', 'pid': 1,
              'hwnd': 2, 'project_path': 'C:/fixture/project.xgwx', 'project_hash': 'a' * 64}
    monkeypatch.setattr(host, 'saved_project_observation', lambda *_: {
        'stable_during_read': True, 'sha256': 'b' * 64})
    requests, lifetime = [], [3.0]
    async def gateway(name, request):
        requests.append(copy.deepcopy(request))
        return {'ok': True, 'action': request['action'], 'input_state_unknown': False}
    owner = host.NativeWorkspaceExecution(gateway=SimpleNamespace(call_workspace_tool=gateway),
        target=target, current_target=lambda: copy.deepcopy(target), record_raw=lambda *args: None,
        refresh_saved=lambda *args: None, program_name='Main', expected_process_create_time=3.0,
        current_process_create_time=lambda: lifetime[0])
    async def run():
        actions = [
            {'action': 'read_ld', 'program_name': 'Main'},
            {'action': 'read_ld_rows', 'program_name': 'Main', 'first_row': 0, 'last_row': 0},
            {'action': 'read_selection', 'program_name': 'Main'},
            {'action': 'write_ld_element', 'program_name': 'Main', 'visual_row': 1,
             'cell': 0, 'element_kind': 'NO', 'operand': 'M00000'},
        ]
        for action in actions:
            result = await owner.execute('native_editor', action)
            assert result['ok'] is True
            assert 'created_empty_authority' not in requests[-1]
        assert target['project_hash'] == 'a' * 64
        before = len(requests)
        blocked = await owner.execute('initialize_ld_program', {'program_name': 'Main',
            'element_kind': 'NO', 'operand': 'M00000'})
        assert blocked['error']['kind'] == 'native_saved_binding_stale'
        assert blocked['action_attempted'] is False and len(requests) == before
        lifetime[0] = 4.0
        blocked = await owner.execute('native_editor', actions[0])
        assert blocked['error']['kind'] == 'native_created_empty_binding_changed'
        assert blocked['action_attempted'] is False and len(requests) == before
        lifetime[0] = 3.0
        monkeypatch.setattr(host, 'saved_project_observation', lambda *_: {
            'stable_during_read': True, 'sha256': 'a' * 64})
        writer = {'action': 'apply_ld_edits', 'program_name': 'Main', 'entries': [{
            'action': 'write_ld_program', 'expected_source_sha256': 'c' * 64,
            'check_and_save': True, 'rungs': [{'branches': [[{'kind': 'NO', 'operand': 'M00000'}]],
                'terminal': {'kind': 'OUT', 'operand': 'P00020'}}]}]}
        assert (await owner.execute('native_editor', writer))['ok'] is True
        authority = requests[-1]['created_empty_authority']
        assert authority['project_hash'] == target['project_hash']
        assert authority['current_binding'] == {
            **{key: target[key] for key in ('workspace_id', 'revision_id', 'session_id', 'pid', 'hwnd')},
            'process_create_time': 3.0}
        writer['entries'] = [{'action': 'set_ld_horizontal_wire', 'visual_row': 0, 'present': True}]
        assert (await owner.execute('native_editor', writer))['ok'] is True
        assert 'created_empty_authority' not in requests[-1]
    asyncio.run(run())


def test_standalone_timer_contact_read_is_read_only(monkeypatch):
    import asyncio
    import copy
    import pytest
    from types import SimpleNamespace
    from controlstudio_host import native_workspace_host as host
    from controlstudio_host.mcp_client import MCPToolError
    from controlstudio_contracts.native_simulator_addresses import simulator_bit_key
    from controlstudio_xg5000_cli import simulator_entry as cli
    from controlstudio_xg5000_cli.vendor.live import simulator_protocol
    sha = 'a' * 64
    target = {'workspace_id': 'w', 'revision_id': 'r', 'session_id': 's', 'pid': 1,
              'hwnd': 2, 'project_path': 'C:/fixture/project.xgwx', 'project_hash': sha}
    monkeypatch.setattr(host, 'saved_project_observation', lambda *_args, **_kwargs:
        {'stable_during_read': True, 'sha256': sha})
    requests = []
    async def gateway(name, request):
        requests.append(copy.deepcopy(request))
        devices, _ = cli._devices(request, write=False, allow_timer=request['action'] == 'read_devices')
        assert simulator_protocol._normalize_variables(tuple(f"%{d['area']}X{d['bit']}" for d in devices),
            simulator_protocol.ReadDataType.BIT) == ('%TX1',)
        return {'ok': True, 'action': 'read_devices', 'read_only': True, 'input_state_unknown': False,
            'loaded_sha256': sha, 'trace': [{'observed_at': 123.0, 'transport': 'XGT_FENET_SIMULATOR',
                'atomic_snapshot': False, 'devices': [{'area': 'T', 'bit': 1, 'value': True}]}]}
    owner = host.NativeWorkspaceExecution(gateway=SimpleNamespace(call_workspace_tool=gateway),
        target=target, current_target=lambda: copy.deepcopy(target), record_raw=lambda *args: None,
        refresh_saved=lambda *args: None, program_name='Main', allow_simulator=True,
        expected_process_create_time=3.0, current_process_create_time=lambda: 3.0)
    async def run():
        result = await owner.execute('simulator', {'action': 'read_devices', 'devices': [{'area': 'T', 'bit': 1}]})
        assert result['trace'][0]['devices'][0] == {'area': 'T', 'bit': 1, 'value': True}
        assert requests[0]['devices'] == [{'area': 'T', 'bit': 1}]
        with pytest.raises(MCPToolError):
            await owner.execute('simulator', {'action': 'write_devices', 'devices': [{'area': 'T', 'bit': 1, 'value': True}]})
        with pytest.raises(MCPToolError):
            await owner.execute('simulator', {'action': 'write_devices', 'devices': [{'area': 'M', 'bit': 1, 'value': True}],
                'readback_devices': [{'area': 'T', 'bit': 1}]})
        assert len(requests) == 1
    asyncio.run(run())
    with pytest.raises(ValueError): simulator_bit_key({'area': 'T', 'bit': 1})
    with pytest.raises(ValueError): cli._devices({'devices': [{'area': 'T', 'bit': 1}]}, write=False)


def test_flag_word_scenario_validation_and_expectation():
    import pytest
    from controlstudio_host import native_workspace_host as module
    step = {'case_label':'scan', 'action':'read_words', 'devices':[{'area':'F','word':50}], 'expected':[{'area':'F','word':50,'value':5}]}
    scenario = {'loaded_sha256':'a'*64, 'stop_on_mismatch':True, 'steps':[step]}
    assert module._validated_simulation_scenario(scenario) == [step]
    assert module._simulation_expectations(step, {'trace':[{'devices':[{'area':'F','word':50,'value':5}]}]})['matched'] is True
    import asyncio
    from types import SimpleNamespace
    from controlstudio_contracts import ExternalToolRequestV1
    from controlstudio_host.execution_broker import MCPExecutionBroker
    from controlstudio_host.mcp_client import MCPToolError

    async def preflight():
        calls, events = [], []
        class Ledger:
            pending = False
            def before(self, *args): self.pending = True
            def interrupted(self): self.pending = True
        ledger = Ledger()
        async def execute(name, args):
            calls.append(name)
            raise MCPToolError('actual callback outcome unknown')
        async def sink(kind, payload): events.append((kind, payload))
        broker = MCPExecutionBroker(SimpleNamespace())
        broker.bind_native_run('conversation', 'run', SimpleNamespace(execute=execute, completion_ledger=ledger))
        wait = {'case_label': 'wait', 'action': 'wait_devices', 'devices': [{'area': 'P', 'bit': 16}],
                'expected': [{'area': 'P', 'bit': 16, 'value': True}], 'samples': 100, 'interval_ms': 50}
        args = {'loaded_sha256': 'a' * 64, 'stop_on_mismatch': True, 'steps': [wait]}
        request = lambda value: ExternalToolRequestV1(request_id='request', conversation_id='conversation',
            run_id='run', tool_name='execute_simulation_scenario', arguments=value, tool_call_id='call')
        result = await broker.execute_external_tool_request(request(args), sink=sink)
        assert result['status'] == 'not_started' and result['action_attempted'] is False
        assert result['input_state_unknown'] is False and not ledger.pending and not calls
        assert result['error']['message'] == ('Scenario step 0: samples must be 1..60, interval_ms 0..5000, '
            'samples * interval_ms <= 60000; write_devices must omit samples and interval_ms')
        assert events[-1][1]['failure_kind'] == 'native_result'
        wait['samples'] = 60
        with pytest.raises(MCPToolError, match='actual callback outcome unknown'):
            await broker.execute_external_tool_request(request(args), sink=sink)
        assert calls == ['execute_simulation_scenario'] and ledger.pending
        assert events[-1][1]['failure_kind'] == 'host_policy'
    asyncio.run(preflight())
