"""Bind model assessments to Host-owned requests and post-validation receipts.

No semantic solver or input authority. Existing native admission still owns all
input. Stored review is about its request/source version, not live equipment.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy

from controlstudio_contracts.native_completion import (
    completion_settlement_result_digest,
    validate_completion_assessment,
    validate_finalization_settlement,
)
from controlstudio_contracts.native_ld_commands import NATIVE_LD_COMMANDS

from .agent_protocol import _without_host_binding_admission, scrub_native_tool_result
from .native_execution_results import _activity_presentation, native_result_event


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _native_result_digest(value):
    return canonical_digest(scrub_native_tool_result(_without_host_binding_admission(value)))


def _matches_parent_native_result(parent, value):
    if 'native_result_sha256' in parent:
        return _native_result_digest(value) == parent['native_result_sha256']
    return canonical_digest(scrub_native_tool_result(value)) == parent.get('result_sha256')


def completion_state_key(workspace_id, revision_id):
    return 'native_completion_generation:' + canonical_digest([workspace_id, revision_id])


def _current_unknown(value):
    # Historical/source/readback archives are not the current operation outcome.
    if not isinstance(value, dict):
        return False
    return (any(key in value and value[key] is not False for key in
                ('input_state_unknown', 'input_queue_state_unknown'))
            or any(_current_unknown(value.get(key)) for key in ('error', 'receipt', 'batch'))
            or any(_current_unknown(item.get('result', item)) for key in ('entries', 'children')
                   for item in (value.get(key) if isinstance(value.get(key), list) else []) if isinstance(item, dict)))


def _editor_navigation_only(tool_name, arguments):
    # These paths may issue navigation input, but cannot change LD or comments.
    # Unknown/undeclared operations retain the conservative write treatment.
    if tool_name != 'native_editor':
        return False
    action = arguments.get('action')
    command = NATIVE_LD_COMMANDS.get(action)
    return (command is not None and not command.mutates_program
            or action in {'observe', 'inspect_rung_comment', 'inspect_variable_comment'})


def _completed_scenario_parts(arguments, result):
    """Typed observed children only; aggregate/cleanup outcomes stay independent."""
    from .native_workspace_host import (
        MCPToolError,
        _rung_unknown,
        _simulation_expectations,
        _validated_simulation_scenario,
    )
    try:
        steps = _validated_simulation_scenario(arguments)
        batch = result.get('batch')
        children = batch.get('children') if isinstance(batch, dict) else None
        prefix = batch.get('completed_prefix_count') if isinstance(batch, dict) else None
        saved = batch.get('loaded_sha256') if isinstance(batch, dict) else None
        if (not isinstance(children, list) or len(children) != len(steps)
                or type(prefix) is not int or not 0 <= prefix <= len(steps)
                or not isinstance(saved, str) or re.fullmatch('[0-9a-f]{64}', saved) is None
                or ('loaded_sha256' in arguments and saved != arguments['loaded_sha256'])):
            return []
        if 'initialize' in arguments:
            lifecycle = result.get('lifecycle')
            load = lifecycle.get('load') if isinstance(lifecycle, dict) else None
            native_load = load.get('result') if isinstance(load, dict) else None
            observed = native_load.get('saved_project') if isinstance(native_load, dict) else None
            if (not isinstance(load, dict) or load.get('status') != 'completed'
                    or not isinstance(native_load, dict) or native_load.get('ok') is not True
                    or native_load.get('error') or native_load.get('failure') or _rung_unknown(native_load)
                    or native_load.get('action') != 'load' or native_load.get('load_state') != 'loaded'
                    or native_load.get('requested_sha256') != arguments['initialize']['saved_sha256']
                    or native_load.get('loaded_sha256') != saved or not isinstance(observed, dict)
                    or observed.get('sha256') != saved or observed.get('stable_during_read') is not True):
                return []
        parts = []
        for index, (step, child) in enumerate(zip(steps[:prefix], children[:prefix])):
            native = child.get('result') if isinstance(child, dict) else None
            observed = native.get('saved_project') if isinstance(native, dict) else None
            if (not isinstance(child, dict) or child.get('index') != index
                    or child.get('status') != 'completed' or child.get('native_execution_completed') is not True
                    or not isinstance(native, dict) or native.get('ok') is not True
                    or native.get('error') or native.get('failure') or _rung_unknown(native)
                    or native.get('loaded_sha256') != saved or not isinstance(observed, dict)
                    or observed.get('sha256') != saved or observed.get('stable_during_read') is not True):
                break
            verdict = _simulation_expectations(step, native)
            if verdict != child.get('expectation') or verdict.get('native_samples_complete') is not True:
                break
            if verdict.get('matched') is False:
                continue
            parts.append({'source_selector': ['batch', 'children', index],
                'source_sha256': canonical_digest(child), 'saved_sha256': saved,
                'case_label': step['case_label']})
        return parts
    except (MCPToolError, ValueError, KeyError, TypeError, AttributeError):
        return []


class NativeCompletionLedger:
    def __init__(self, *, store, conversation_id, run_id, request_message_id,
                 workspace_id, revision_id, request_content, user_messages, project_brief, snapshot, receipt_sequence, allowed_runs):
        self.store, self.snapshot = store, snapshot
        self.allowed_runs = dict(allowed_runs)
        self._checkpoint_indexed_runs = set()
        self.receipt_sequence = receipt_sequence
        self.receipt_start = 0
        self.scope = dict(conversation_id=conversation_id, run_id=run_id,
            request_message_id=request_message_id, workspace_id=workspace_id, revision_id=revision_id)
        self.key = completion_state_key(workspace_id, revision_id)
        originals = [item['content'] for item in user_messages if item.get('message_id') == request_message_id
                     and isinstance(item.get('content'), str)]
        self.requirement_available = len(originals) == 1
        self.requirements = ({'request': {'request_message_id': request_message_id, 'content': originals[0]}}
                             if self.requirement_available else {})
        if self.requirement_available and project_brief is not None:
            self.requirements['project_goal'] = {'project_brief': deepcopy(project_brief),
                                               'ordered_user_messages': deepcopy(user_messages)}
        self.requirement_refs = {kind: 'completion-requirement:' + canonical_digest({
            **self.scope, 'kind': kind, 'requirements': body}) for kind, body in self.requirements.items()}
        self.user_digest = canonical_digest(user_messages)
        self.project_basis_digest = canonical_digest(project_brief)

    def context(self):
        if not self.requirement_available:
            return None
        return {'schema_version': '1', 'requirement_refs': deepcopy(self.requirement_refs),
            'available_prior_runs': [{'run_id': run_id, 'request_message_id': request_id}
                for run_id, request_id in self.allowed_runs.items() if run_id != self.scope['run_id']],
            'sources': deepcopy(self.requirements),
            'meaning': 'Source identifiers only, never editing/input authority. You judge meaning and coverage. '
                'Project goal includes the original brief and ordered user amendments.'}

    def _state(self, snapshot):
        state = self.store.get_host_state(self.key)
        physical = {key: snapshot[key] for key in ('workspace_id', 'revision_id', 'pid', 'hwnd',
                                                  'process_create_time', 'project_path')}
        if not isinstance(state, dict) or state.get('physical') != physical:
            state = {'physical': physical, 'saved_sha256': snapshot['saved_sha256'],
                'saved_source_cohort': [snapshot['saved_sha256']],
                'epoch': (state.get('epoch', 0) + 1 if isinstance(state, dict) else 0),
                'pending_current_input': False, 'pending_simulator_input': False, 'pending_other_input': False,
                'in_flight': None, 'in_flight_simulator': False, 'in_flight_editor_navigation': False,
                'saved_observed_epoch': 0, 'editor_dirty_since_rebound': False, 'quarantine_baseline': None}
        elif state.get('saved_sha256') != snapshot['saved_sha256']:
            state = {**state, 'saved_sha256': snapshot['saved_sha256'], 'epoch': state['epoch'] + 1,
                     'saved_source_cohort': [snapshot['saved_sha256']], 'quarantine_baseline': None}
        return state

    def before(self, tool_name, arguments, call_id):
        # No privilege from a completion reference. Normal native.execute follows.
        self.receipt_start = self.receipt_sequence()
        snapshot = self.snapshot(False)
        if snapshot is None:
            return
        state = self._state(snapshot)
        action = arguments.get('action') if tool_name in ('native_editor', 'simulator') else tool_name
        role = _activity_presentation(tool_name, action)[1]
        # Conservatively invalidate at existing generic edit/comment categories.
        if role in ('LD 편집', '설명 수정') or tool_name in ('prepare_project', 'cancel_project_preparation'):
            state['epoch'] += 1
            state['editor_dirty_since_rebound'] = True
            state['saved_source_cohort'] = [state['saved_sha256']]
        if tool_name == 'native_editor' and action in ('inspect_abandoned_operation', 'settle_abandoned_operation'):
            state['quarantine_baseline'] = None
        state['in_flight'] = call_id
        state['in_flight_simulator'] = tool_name in ('simulator', 'execute_simulation_scenario')
        state['in_flight_editor_navigation'] = _editor_navigation_only(tool_name, arguments)
        self.store.set_host_state(self.key, state)

    def interrupted(self):
        state = self.store.get_host_state(self.key)
        if isinstance(state, dict):
            domain = 'pending_simulator_input' if state.get('in_flight_simulator') else 'pending_other_input'
            if state.get('in_flight_simulator'):
                state.pop('pending_simulator_source', None)
            if state.get('in_flight_editor_navigation') is not True:
                state[domain] = True
            self.store.set_host_state(self.key, {**state,
                'pending_current_input': state['pending_simulator_input'] or state['pending_other_input'],
                'in_flight': None, 'in_flight_simulator': False, 'in_flight_editor_navigation': False,
                'quarantine_baseline': None})

    def record(self, tool_name, arguments, result, call_id, *, admitted_generation=None):
        # Only this post-validation Host ledger can issue a settlement.
        if isinstance(result, dict):
            result = {key: value for key, value in result.items()
                      if key not in {'finalization_settlement', 'completion_evidence_ref', 'completion_evidence_parts'}}
        # Proof digests bind the result the Agent actually receives. Private
        # diagnostics remain in raw receipts, outside the native wire envelope.
        result = scrub_native_tool_result(result)
        previous = deepcopy(self.store.get_host_state(self.key))
        snapshot = self.snapshot(False)
        if snapshot is None:
            return result
        state = self._state(snapshot)
        projected = native_result_event(tool_name, arguments, result)['native_result']['state']
        current_unknown = _current_unknown(result) or projected == 'unknown'
        load = result if tool_name == 'simulator' and arguments.get('action') == 'load' else None
        if tool_name == 'execute_simulation_scenario' and isinstance(result, dict):
            lifecycle = result.get('lifecycle')
            entry = lifecycle.get('load') if isinstance(lifecycle, dict) else None
            if isinstance(entry, dict) and entry.get('status') == 'completed':
                load = entry.get('result')
        transition = load.get('saved_version_transition') if isinstance(load, dict) else None
        before = transition.get('before') if isinstance(transition, dict) else None
        after = transition.get('after') if isinstance(transition, dict) else None
        saved_load = load.get('saved_project') if isinstance(load, dict) else None
        cohort_candidate = (isinstance(previous, dict) and previous.get('in_flight') == call_id
                and previous.get('physical') == state['physical']
                and state['physical']['workspace_id'] == self.scope['workspace_id']
                and state['physical']['revision_id'] == self.scope['revision_id']
                and previous.get('editor_dirty_since_rebound') is False
                and previous.get('saved_observed_epoch') == previous.get('epoch')
                and not current_unknown and isinstance(load, dict) and load.get('ok') is True
                and load.get('action') == 'load' and load.get('input_state_unknown') is False
                and not load.get('error') and not load.get('failure')
                and load.get('load_state') == 'loaded'
                and isinstance(transition, dict) and transition.get('verified') is True and transition.get('metadata_only') is True
                and isinstance(before, dict) and isinstance(after, dict) and isinstance(saved_load, dict)
                and before.get('sha256') == previous.get('saved_sha256') == load.get('requested_sha256')
                and after.get('sha256') == saved_load.get('sha256') == load.get('loaded_sha256') == snapshot['saved_sha256']
                and saved_load.get('stable_during_read') is True
                and isinstance(before.get('source_sha256'), str)
                and re.fullmatch(r'[0-9a-f]{64}', before['source_sha256']) is not None
                and before['source_sha256'] == after.get('source_sha256') == load.get('loaded_source_sha256'))
        end_result = result if tool_name == 'simulator' and arguments.get('action') == 'end' else None
        if tool_name == 'execute_simulation_scenario' and arguments.get('end_after') is True:
            lifecycle = result.get('lifecycle', {}) if isinstance(result, dict) else {}
            entry = lifecycle.get('end') if isinstance(lifecycle, dict) else None
            ended = entry.get('result') if isinstance(entry, dict) else None
            if (isinstance(entry, dict) and entry.get('status') == 'completed' and isinstance(ended, dict)
                    and ended.get('ok') is True and not ended.get('error') and not ended.get('failure')
                    and not _current_unknown(ended)):
                end_result = ended
        if current_unknown and not _editor_navigation_only(tool_name, arguments):
            state['quarantine_baseline'] = None
            domain = 'pending_simulator_input' if tool_name in ('simulator', 'execute_simulation_scenario') else 'pending_other_input'
            state[domain] = True
        if (tool_name == 'read_saved_project' and result.get('stable_during_read') is True
                and result.get('sha256') == snapshot['saved_sha256'] and not state['editor_dirty_since_rebound']):
            state['saved_observed_epoch'] = state['epoch']
        saved = result.get('saved_project') if isinstance(result, dict) else None
        batch = result.get('batch') if isinstance(result, dict) else None
        requested = arguments.get('entries') if isinstance(arguments, dict) else None
        completed = batch.get('entries') if isinstance(batch, dict) else None
        child = completed[0].get('result') if isinstance(completed, list) and len(completed) == 1 and isinstance(completed[0], dict) else None
        check = child.get('check') if isinstance(child, dict) else None
        save_input = child.get('save_input') if isinstance(child, dict) else None
        batch_save = (tool_name == 'native_editor' and arguments.get('action') == 'apply_ld_edits'
            and isinstance(requested, list) and len(requested) == 1 and isinstance(requested[0], dict)
            and requested[0].get('action') == 'write_ld_program' and requested[0].get('check_and_save') is True
            and isinstance(batch, dict) and batch.get('all_completed') is True
            and isinstance(completed, list) and len(completed) == 1 and isinstance(completed[0], dict) and completed[0].get('status') == 'completed'
            and isinstance(child, dict) and child.get('ok') is True and child.get('declaration_matches') is True
            and isinstance(check, dict) and check.get('status') == 'passed' and check.get('has_errors') is False
            and isinstance(save_input, dict) and save_input.get('completed') is True
            and save_input.get('input_attempted') is True and save_input.get('input_state_unknown') is False
            and isinstance(saved, dict) and saved.get('exists') is True
            and isinstance(saved.get('sha256'), str) and re.fullmatch(r'[0-9a-f]{64}', saved['sha256']) is not None)
        if ((tool_name == 'save_project' or batch_save) and isinstance(previous, dict)
                and previous.get('in_flight') == call_id and previous.get('physical') == state['physical']
                and state['physical']['workspace_id'] == self.scope['workspace_id']
                and state['physical']['revision_id'] == self.scope['revision_id']
                and isinstance(result, dict) and result.get('ok') is True
                and not result.get('error') and not result.get('failure') and not current_unknown
                and projected == 'completed' and isinstance(saved, dict)
                and saved.get('workspace_id') == self.scope['workspace_id']
                and saved.get('revision_id') == self.scope['revision_id']
                and saved.get('stable_during_read') is True and saved.get('sha256') == snapshot['saved_sha256']):
            state['editor_dirty_since_rebound'] = False
            state['saved_observed_epoch'] = state['epoch']
        if (isinstance(end_result, dict) and end_result.get('ended') is True
                and end_result.get('current_session_inactive_verified') is True and not current_unknown):
            # Inactive SIM verification does not settle any LD/editor unknown.
            state['pending_simulator_input'] = False
        state['pending_current_input'] = state['pending_simulator_input'] or state['pending_other_input']
        state['in_flight'] = None
        state['in_flight_simulator'] = False
        state['in_flight_editor_navigation'] = False
        raw_refs = []
        for sequence in range(self.receipt_start, self.receipt_sequence()):
            key = f"native_ld_receipt:{self.scope['run_id']}:{sequence:08d}"
            raw = self.store.get_host_state(key)
            if not isinstance(raw, dict) or raw.get('sequence') != sequence:
                raise ValueError('completion_receipt_not_durable')
            raw_refs.append({'sequence': sequence, 'sha256': canonical_digest(raw)})
        if cohort_candidate:
            from controlstudio_contracts.native_simulator_saved_source import (
                native_write_completion_verified,
            )
            fields = ('action', 'requested_sha256', 'loaded_sha256', 'loaded_source_sha256',
                      'saved_version_transition', 'saved_project', 'gui')
            expected_load = {key: load.get(key) for key in fields}
            matching = []
            for item in raw_refs:
                raw = self.store.get_host_state(f"native_ld_receipt:{self.scope['run_id']}:{item['sequence']:08d}")
                native = raw.get('result') if isinstance(raw, dict) and raw.get('tool_name') == 'simulator' else None
                if (isinstance(native, dict) and native.get('action') == 'load'
                        and native_write_completion_verified(native.get('gui'))
                        and scrub_native_tool_result({key: native.get(key) for key in fields}) == expected_load):
                    matching.append(item['sequence'])
            prior_cohort = previous.get('saved_source_cohort', [previous['saved_sha256']])
            if (len(matching) == 1 and isinstance(prior_cohort, list) and previous['saved_sha256'] in prior_cohort
                    and all(isinstance(item, str) and re.fullmatch(r'[0-9a-f]{64}', item) for item in prior_cohort)):
                state['saved_source_cohort'] = list(dict.fromkeys([*prior_cohort, snapshot['saved_sha256']]))
                state['saved_observed_epoch'] = state['epoch']
        if (tool_name == 'native_editor' and arguments.get('action') in
                ('inspect_abandoned_operation', 'settle_abandoned_operation')):
            baseline = self._inspection_baseline(arguments, result, raw_refs, snapshot)
            state['quarantine_baseline'] = baseline
            if (baseline is not None and not current_unknown and isinstance(previous, dict)
                    and previous.get('in_flight') == call_id
                    and all(previous.get(key) == state[key] for key in ('physical', 'saved_sha256', 'epoch'))):
                # This proves present owner quiescence, not the old edit's outcome.
                state['pending_other_input'] = False
                state['pending_current_input'] = state['pending_simulator_input']
            # A known lock snapshot is not settlement of every later editor unknown.
            if baseline is not None and baseline['unsaved_changes_observed']:
                state['editor_dirty_since_rebound'] = True
        if not self.requirement_available:
            self.store.set_host_state(self.key, state)
            return result
        eligible = (isinstance(result, dict) and result.get('ok') is not False and not result.get('error')
                    and projected in ('completed', 'observed') and not current_unknown and bool(raw_refs))
        body = {**self.scope, 'call_id': call_id, 'tool_name': tool_name,
            'arguments_sha256': canonical_digest(arguments), 'result_sha256': canonical_digest(result),
            'native_result_sha256': _native_result_digest(result),
            'evidence_kind': 'post_validated_native_result', 'raw_receipts': raw_refs, 'generation': {key: deepcopy(state[key]) for key in ('physical', 'saved_sha256', 'epoch', 'editor_dirty_since_rebound')}, 'eligible_support': eligible, 'outcome': projected}
        reference = 'completion-evidence:' + canonical_digest(body)
        generation = {key: deepcopy(state[key]) for key in ('physical', 'saved_sha256', 'epoch')}
        if current_unknown and tool_name in ('simulator', 'execute_simulation_scenario'):
            state['pending_simulator_source'] = {'call_id': call_id,
                'result_sha256': completion_settlement_result_digest(result), 'completion_evidence_ref': reference}
            state['pending_simulator_generation'] = canonical_digest(generation)
            state['pending_simulator_admission'] = admitted_generation
        self.store.set_host_state('native_' + reference, body)
        parts = (self._issue_scenario_parts(reference, body, arguments, result)
                 if tool_name == 'execute_simulation_scenario' else [])
        self.store.set_host_state(self.key, state)
        try:
            self.index_issued_references([reference])
        except Exception:
            pass  # This derived catalog must not change the completed native outcome.
        returned = {**result, 'completion_evidence_ref': reference} if isinstance(result, dict) else result
        if parts:
            returned['completion_evidence_parts'] = parts
        saved = end_result.get('saved_project') if isinstance(end_result, dict) else None
        settled_source = previous.get('pending_simulator_source') if isinstance(previous, dict) else None
        if eligible and isinstance(end_result, dict) and isinstance(settled_source, dict):
            # Older ledgers hashed a scenario before the native wire scrub.
            # Re-anchor only an exact Host-owned evidence/raw-receipt pair;
            # leave that historical source and its unknown outcome untouched.
            source_ref = settled_source.get('completion_evidence_ref')
            source = self.store.get_host_state('native_' + source_ref) if isinstance(source_ref, str) else None
            if (isinstance(source, dict) and source_ref == 'completion-evidence:' + canonical_digest(source)
                    and all(source.get(key) == self.scope[key] for key in
                        ('conversation_id', 'request_message_id', 'workspace_id', 'revision_id'))
                    and source.get('run_id') in self.allowed_runs
                    and self.allowed_runs[source['run_id']] == self.scope['request_message_id']
                    and source.get('call_id') == settled_source.get('call_id')
                    and source.get('tool_name') in ('simulator', 'execute_simulation_scenario')
                    and source.get('evidence_kind') == 'post_validated_native_result' and source.get('outcome') == 'unknown'
                    and source.get('result_sha256') == settled_source.get('result_sha256')
                    and isinstance(source.get('generation'), dict)
                    and all(source.get('generation', {}).get(key) == generation[key] for key in generation)
                    and isinstance(source.get('raw_receipts'), list) and source['raw_receipts']):
                raw_ref = source['raw_receipts'][-1]
                raw = (self.store.get_host_state(f"native_ld_receipt:{source['run_id']}:{raw_ref['sequence']:08d}")
                    if isinstance(raw_ref, dict) and type(raw_ref.get('sequence')) is int and raw_ref['sequence'] >= 0 else None)
                if (isinstance(raw, dict) and raw.get('sequence') == raw_ref['sequence']
                        and canonical_digest(raw) == raw_ref.get('sha256')
                        and raw.get('tool_name') == source['tool_name']
                        and isinstance(raw.get('result'), dict)
                        and completion_settlement_result_digest(raw['result']) == source['result_sha256']):
                    settled_source = {**settled_source, 'result_sha256':
                        completion_settlement_result_digest(scrub_native_tool_result(raw['result']))}
        # Fresh inactive verification may reissue this finalization-only proof
        # after an earlier delivery failed; it never replays simulator control.
        if (eligible and isinstance(end_result, dict)
                and end_result.get('ended') is True and end_result.get('current_session_inactive_verified') is True
                and isinstance(previous, dict) and previous.get('in_flight') == call_id
                and state['pending_current_input'] is False and state['pending_other_input'] is False
                and all(previous.get(key) == state[key] for key in ('physical', 'saved_sha256', 'epoch'))
                and previous.get('pending_simulator_generation') == canonical_digest(generation)
                and previous.get('pending_simulator_admission') == admitted_generation
                and isinstance(settled_source, dict)
                and isinstance(saved, dict) and saved.get('stable_during_read') is True
                and saved.get('sha256') == state['saved_sha256'] and isinstance(admitted_generation, str)):
            returned['finalization_settlement'] = validate_finalization_settlement({
                'schema_version': '1', 'provenance': 'host_completion_settlement/v1',
                'scope': 'finalization_only', 'settled_domain': 'simulator',
                'requirement_ref': self.requirement_refs['request'], 'admitted_generation': admitted_generation,
                'source_call_id': call_id, 'completion_evidence_ref': reference,
                'result_sha256': completion_settlement_result_digest(result),
                'saved_sha256': state['saved_sha256'], 'generation_sha256': canonical_digest(generation),
                'epoch': state['epoch'], 'settled_source': settled_source,
                'native_input_authority': False, 'original_outcomes_preserved': True})
        return returned

    def _issue_scenario_parts(self, parent_ref, parent, arguments, result, *, existing_only=False):
        # The same content-addressed reference syntax and original raw receipt
        # bind every selection. These sources grant finalization authority only.
        sources = []
        if not _matches_parent_native_result(parent, result):
            return sources
        matching_raw = False
        for item in parent['raw_receipts']:
            raw = self.store.get_host_state(f"native_ld_receipt:{parent['run_id']}:{item['sequence']:08d}")
            if (isinstance(raw, dict) and canonical_digest(raw) == item['sha256']
                    and raw.get('tool_name') == parent['tool_name']
                    and canonical_digest(raw.get('arguments')) == parent['arguments_sha256']
                    and _matches_parent_native_result(parent, raw.get('result'))):
                matching_raw = True
        if not matching_raw:
            return sources
        for part in _completed_scenario_parts(arguments, result):
            body = {**deepcopy(parent), 'evidence_kind': 'post_validated_native_result_part',
                'eligible_support': True, 'outcome': 'completed', 'parent_evidence_ref': parent_ref,
                'source_selector': part['source_selector'], 'source_sha256': part['source_sha256']}
            reference = 'completion-evidence:' + canonical_digest(body)
            if existing_only:
                if self.store.get_host_state('native_' + reference) != body:
                    continue
            else:
                self.store.set_host_state('native_' + reference, body)
            sources.append({key: deepcopy(value) for key, value in {
                **part, 'completion_evidence_ref': reference, 'scope': 'finalization_only',
                'native_input_authority': False, 'outcome': 'completed'}.items() if key != 'source_sha256'})
        return sources

    def _inspection_baseline(self, arguments, returned, raw_refs, snapshot):
        # These identifiers stay Host-only: read the one exact pre-scrub receipt,
        # never rename fields to bypass the existing public PID/path scrubber.
        if len(raw_refs) != 1 or not isinstance(returned, dict):
            return None
        item = raw_refs[0]
        raw = self.store.get_host_state(f"native_ld_receipt:{self.scope['run_id']}:{item['sequence']:08d}")
        if not isinstance(raw, dict) or canonical_digest(raw) != item['sha256'] or raw.get('tool_name') != 'native_editor':
            return None
        request = raw.get('arguments'); original = raw.get('result')
        action = arguments['action']
        if (not isinstance(request, dict) or not isinstance(original, dict)
                or request.get('action') != action
                or any(request.get(k) != snapshot[k] for k in ('workspace_id', 'revision_id', 'pid', 'hwnd'))
                or set(request) != {'action', 'workspace_id', 'revision_id', 'pid', 'hwnd'} |
                    ({'recovery_token'} if action == 'settle_abandoned_operation' else set())):
            return None
        body = original.get('operation_recovery'); public = returned.get('operation_recovery')
        if not isinstance(body, dict) or not isinstance(public, dict):
            return None
        if any(value.get('ok') is not True or value.get('action') != action
               or value.get('read_only') is not True or value.get('action_attempted') is not False
               or value.get('input_state_unknown') is not False or value.get('error') or value.get('failure')
               or _current_unknown(value) for value in (original, returned)):
            return None
        flags = ('state', 'quarantined', 'settled', 'target_verified', 'saved_sha256')
        if (any(body.get(k) != public.get(k) for k in flags)
                or body.get('quarantined') is not False or body.get('target_verified') is not True
                or body.get('saved_sha256') != snapshot['saved_sha256']
                or not (body.get('state') == 'not_quarantined' and body.get('settled') is False
                        or body.get('state') == 'settled' and body.get('settled') is True)):
            return None
        processes = body.get('process_evidence')
        release = body.get('owner_release_evidence')
        if (not isinstance(processes, dict) or processes.get('inventory_complete') is not True
                or type(processes.get('live_candidate_count')) is not int
                or processes['live_candidate_count'] != 0
                or type(processes.get('old_owner_live')) is not bool
                or (processes['old_owner_live'] and not (body['state'] == 'settled'
                    and isinstance(release, dict) and isinstance(release.get('before'), dict)
                    and release['before'] == release.get('after')))):
            return None
        observed = body.get('lifetime_marker')
        expected = {k: snapshot[k] for k in ('workspace_id', 'revision_id', 'pid', 'hwnd',
                                             'process_create_time', 'project_path', 'saved_sha256')}
        if (not isinstance(observed, dict) or observed.get('schema_version') != '1'
                or observed.get('snapshot_stable') is not True
                or any(observed.get(k) != value for k, value in expected.items() if k != 'project_path')
                or not isinstance(observed.get('project_path'), str)
                or observed['project_path'].replace('\\', '/').casefold()
                    != expected['project_path'].replace('\\', '/').casefold()):
            return None
        ui = original.get('observation')
        if (not isinstance(ui, dict) or ui.get('pid') != snapshot['pid'] or ui.get('hwnd') != snapshot['hwnd']
                or ui.get('main_hwnd') != snapshot['hwnd']
                or not isinstance(ui.get('operation_recovery_binding'), dict)
                or ui['operation_recovery_binding'].get('verified') is not True):
            return None
        return {'conversation_id': self.scope['conversation_id'], 'run_id': self.scope['run_id'],
            'raw_receipts': deepcopy(raw_refs), 'binding': expected, 'state': body['state'],
            'unsaved_changes_observed': ui['operation_recovery_binding'].get('unsaved_changes_preserved') is True}

    def evaluate(self, proposal, finish_status):
        if proposal is None:
            return None
        try:
            if not self.requirement_available:
                raise ValueError('original_user_requirement_unavailable')
            assessment = validate_completion_assessment(proposal)
            snapshot = self.snapshot(True)
            if snapshot is None:
                raise ValueError('completion_binding_unavailable')
            state = self._state(snapshot)
            recorded = self.store.get_host_state(self.key)
            if state != recorded or state['pending_current_input'] or state['in_flight'] is not None:
                raise ValueError('current_generation_not_settled')
            scope = assessment['scope']
            if assessment['requirement_ref'] != self.requirement_refs.get(scope):
                raise ValueError('requirement_scope_mismatch')
            partial = (finish_status != 'completed' or any(item['verdict'] != 'met' or not item['evidence_refs']
                       for item in assessment['criteria']) or any(item['scope'] == scope or scope == 'project_goal'
                       for item in assessment['remaining_work']))
            for criterion_index, criterion in enumerate(assessment['criteria']):
                for evidence_index, ref in enumerate(criterion['evidence_refs']):
                    evidence = self._evidence(ref)
                    self._verify_raw_refs(evidence)
                    if evidence['eligible_support'] is not True:
                        field_error = {'loc': ['completion_assessment', 'criteria', criterion_index,
                                               'evidence_refs', evidence_index],
                            'type': 'failed_or_unknown_support', 'reference': ref,
                            'eligible_support': evidence['eligible_support'], 'outcome': evidence.get('outcome')}
                        raise ValueError('failed_or_unknown_support')
                    if criterion['reuse_basis'] == 'current_execution':
                        # An execution includes successive edits and saves. Its
                        # receipts retain their observed versions, not the final
                        # version; the model judges what each receipt supports.
                        if any(evidence.get(key) != value for key, value in self.scope.items()):
                            raise ValueError('support_scope_or_generation_mismatch')
                    else:
                        problem = (('request', 'same original request') if scope == 'request'
                            and evidence.get('request_message_id') != self.scope['request_message_id'] else
                            ('binding', 'same conversation, workspace and revision') if any(
                                evidence.get(key) != self.scope[key] for key in ('conversation_id', 'workspace_id', 'revision_id')) else
                            ('run', 'allowed prior run and request') if evidence['run_id'] not in self.allowed_runs
                                or evidence['request_message_id'] != self.allowed_runs[evidence['run_id']] else
                            ('saved_sha256', 'current saved source') if evidence['generation']['saved_sha256'] != state['saved_sha256']
                                and (evidence['generation']['saved_sha256'] not in state.get('saved_source_cohort', [state['saved_sha256']])
                                     or evidence['generation'].get('physical') != state['physical']) else
                            ('saved_observed_epoch', 'current epoch') if state['saved_observed_epoch'] != state['epoch'] else
                            ('editor_dirty_since_rebound', 'false') if state['editor_dirty_since_rebound'] else None)
                        if problem:
                            field_error = {'loc': ['completion_assessment', 'scope'] if problem[0] == 'scope' else
                                ['completion_assessment', 'criteria', criterion_index, 'reuse_basis'],
                                'type': problem[0] + '_mismatch', 'expected': problem[1]}
                            raise ValueError('historical_saved_source_or_change_scope_mismatch')
            for ref in assessment['diagnostic_refs']:
                evidence = self._evidence(ref)
                if any(evidence.get(key) != self.scope[key] for key in
                       ('conversation_id', 'workspace_id', 'revision_id')):
                    raise ValueError('diagnostic_scope_mismatch')
            return {**self.scope, 'schema_version': '1', 'accepted': not partial,
                'status': 'partial' if partial else 'accepted', 'scope': scope,
                'assessment': assessment, 'assessment_sha256': canonical_digest(assessment),
                'user_messages_sha256': self.user_digest, 'project_basis_sha256': self.project_basis_digest, 'generation': state,
                'basis': 'model_assessment_host_bound_sources', 'natural_language_independently_verified': False}
        except ValueError as error:
            rejected = {**self.scope, 'schema_version': '1', 'accepted': False, 'status': 'rejected',
                'basis': 'completion_sources_or_scope_unverified', 'reason_code': str(error),
                'natural_language_independently_verified': False}
            if str(error) == 'historical_saved_source_or_change_scope_mismatch':
                rejected['field_error'] = field_error
            if str(error) == 'failed_or_unknown_support':
                rejected['field_error'] = field_error
            if str(error) == 'failed_or_unknown_support' and evidence.get('tool_name') == 'execute_simulation_scenario':
                rejected['evidence_lookup'] = {'tool_name': 'read_completion_sources',
                    'run_id': evidence['run_id'], 'arguments': {'evidence_ref': ref}}
            return rejected

    def _issued_source(self, reference):
        evidence = self._evidence(reference)
        if (any(evidence.get(key) != self.scope[key] for key in ('conversation_id', 'workspace_id', 'revision_id'))
                or evidence.get('run_id') not in self.allowed_runs
                or evidence.get('request_message_id') != self.allowed_runs[evidence['run_id']]):
            raise ValueError('completion_source_scope_mismatch')
        self._verify_raw_refs(evidence)
        parent = self._evidence(evidence.get('parent_evidence_ref', reference))
        matches = []
        for item in parent['raw_receipts']:
            raw = self.store.get_host_state(f"native_ld_receipt:{parent['run_id']}:{item['sequence']:08d}")
            arguments = raw.get('arguments') if isinstance(raw, dict) else None
            logical_arguments = ({key: value for key, value in arguments.items() if key not in {
                'pid', 'hwnd', 'expected_process_create_time', 'project_path', 'project_hash',
                'workspace_id', 'revision_id'}} if isinstance(arguments, dict) else None)
            if (isinstance(raw, dict) and raw.get('tool_name') == parent.get('tool_name')
                    and (canonical_digest(arguments) == parent.get('arguments_sha256')
                         or (logical_arguments is not None and canonical_digest(logical_arguments) == parent.get('arguments_sha256')))
                    and _matches_parent_native_result(parent, raw.get('result'))):
                matches.append(item['sequence'])
        if len(matches) != 1:
            raise ValueError('completion_source_receipt_unresolved')
        return evidence, parent['run_id'], matches[0]

    def index_issued_references(self, references):
        """Best-effort derived links to already issued and verified evidence."""
        headers = {}
        for reference in references:
            try:
                evidence, run_id, sequence = self._issued_source(reference)
                # Catalog only issued aggregate records; direct part lookup is unchanged.
                if evidence.get('parent_evidence_ref') is not None:
                    continue
                key = 'native_ld_receipts:' + run_id
                if key not in headers:
                    header = self.store.get_host_state(key)
                    if (not isinstance(header, dict) or not isinstance(header.get('binding'), dict)
                            or any(header['binding'].get(k) != self.scope[k] for k in ('workspace_id', 'revision_id'))):
                        continue
                    headers[key] = [deepcopy(header), False]
                header, _ = headers[key]
                mapping = header.setdefault('issued_evidence_by_sequence', {})
                if not isinstance(mapping, dict):
                    continue
                values = mapping.setdefault(str(sequence), [])
                if not isinstance(values, list):
                    continue
                if reference not in values:
                    values.append(reference)
                    headers[key][1] = True
            except (ValueError, KeyError, TypeError):
                continue
        for key, (header, changed) in headers.items():
            if changed:
                self.store.set_host_state(key, header)

    def index_checkpoint_references(self, checkpoint):
        if not isinstance(checkpoint, Mapping):
            return
        public = checkpoint.get('public_state')
        records = public.get('records') if isinstance(public, Mapping) else None
        refs = []
        for record in records if isinstance(records, (list, tuple)) else ():
            if not isinstance(record, Mapping) or record.get('event') != 'child_result':
                continue
            result = record.get('result')
            if isinstance(result, Mapping) and isinstance(result.get('completion_evidence_ref'), str):
                refs.append(result['completion_evidence_ref'])
        # Validated checkpoints also retain exact public tool exchanges. Older
        # END results can exist only there, as JSON tool-message content.
        pairs = public.get('completed_pairs') if isinstance(public, Mapping) else None
        for pair in pairs if isinstance(pairs, (list, tuple)) else ():
            if not isinstance(pair, Mapping):
                continue
            results = pair.get('results')
            for message in results if isinstance(results, (list, tuple)) else ():
                if not isinstance(message, Mapping) or message.get('role') != 'tool':
                    continue
                content = message.get('content')
                if not isinstance(content, str):
                    continue
                try:
                    result = json.loads(content)
                except (ValueError, TypeError):
                    continue
                if isinstance(result, dict) and isinstance(result.get('completion_evidence_ref'), str):
                    refs.append(result['completion_evidence_ref'])
        self.index_issued_references(dict.fromkeys(refs))

    def read_sources(self, arguments):
        if not self.requirement_available:
            return {'ok': False, 'read_only': True, 'action_attempted': False, 'input_state_unknown': False,
                    'error': {'kind': 'completion_requirement_unavailable'}}
        if not isinstance(arguments, dict) or set(arguments) - {'run_id', 'offset', 'limit', 'evidence_ref'}:
            raise ValueError('completion_source_run_unavailable')
        direct = 'evidence_ref' in arguments
        if direct:
            if set(arguments) != {'evidence_ref'}:
                raise ValueError('completion_source_arguments_invalid')
            evidence, run_id, offset = self._issued_source(arguments['evidence_ref'])
            limit = 1
        else:
            run_id = arguments.get('run_id', self.scope['run_id'])
            if not isinstance(run_id, str) or run_id not in self.allowed_runs:
                raise ValueError('completion_source_run_unavailable')
            offset = arguments.get('offset', 0); limit = arguments.get('limit', 10)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('completion_source_page_invalid')
        index = self.store.get_host_state('native_ld_receipts:' + run_id)
        binding = index.get('binding') if isinstance(index, dict) else None
        no_receipts = index is None and not direct
        if not no_receipts and (not isinstance(binding, dict) or any(binding.get(k) != self.scope[k] for k in ('workspace_id', 'revision_id'))):
            raise ValueError('completion_source_workspace_mismatch')
        if not direct and not no_receipts and run_id not in self._checkpoint_indexed_runs:
            mapping = index.get('issued_evidence_by_sequence', {})
            if not isinstance(mapping, dict) or any(not mapping.get(str(sequence)) for sequence in range(offset, offset + limit)):
                self._checkpoint_indexed_runs.add(run_id)
                try:
                    state = self.store.get_native_checkpoint_state('native_ld_checkpoint:' + run_id)
                    checkpoint_binding = state.get('binding') if isinstance(state, dict) else None
                    if isinstance(checkpoint_binding, dict) and all(
                            checkpoint_binding.get(k) == self.scope[k] for k in ('workspace_id', 'revision_id')):
                        self.index_checkpoint_references(state.get('checkpoint'))
                        index = self.store.get_host_state('native_ld_receipts:' + run_id)
                except Exception:
                    pass  # Derived catalog recovery must not change the underlying receipt page.
        rows = []; exhausted = no_receipts
        for sequence in range(offset, offset + limit) if not no_receipts else ():
            raw = self.store.get_host_state(f'native_ld_receipt:{run_id}:{sequence:08d}')
            if raw is None:
                exhausted = True; break
            if not isinstance(raw, dict) or raw.get('sequence') != sequence:
                raise ValueError('completion_source_record_invalid')
            result = raw.get('result'); args = raw.get('arguments'); name = raw.get('tool_name')
            row = {'sequence': sequence, 'tool_name': name, 'support_available': False}
            if direct:
                row.update(completion_evidence_ref=arguments['evidence_ref'],
                           support_available=evidence.get('eligible_support') is True,
                           outcome=evidence.get('outcome'))
            else:
                mapping = index.get('issued_evidence_by_sequence', {}) if isinstance(index, dict) else {}
                candidates = mapping.get(str(sequence), []) if isinstance(mapping, dict) else []
                valid = []
                for candidate in candidates if isinstance(candidates, list) else []:
                    try:
                        issued, issued_run, issued_sequence = self._issued_source(candidate)
                        if issued_run == run_id and issued_sequence == sequence:
                            valid.append((candidate, issued))
                    except (ValueError, KeyError, TypeError):
                        continue
                if len(valid) == 1:
                    candidate, issued = valid[0]
                    row.update(completion_evidence_ref=candidate,
                               support_available=issued.get('eligible_support') is True,
                               outcome=issued.get('outcome'))
            # This exact aggregate is recorded only after Host step validation and assertion comparison.
            if name == 'execute_simulation_scenario' and isinstance(result, dict) and isinstance(args, dict):
                from .native_workspace_host import (
                    MCPToolError,
                    _rung_unknown,
                    _simulation_expectations,
                    _validated_simulation_scenario,
                )
                batch = result.get('batch')
                children = batch.get('children') if isinstance(batch, dict) else None
                try:
                    steps = _validated_simulation_scenario(args)
                    saved_sha = args['loaded_sha256']
                    valid = (result.get('ok') is True and not result.get('error') and not result.get('failure')
                        and not _rung_unknown(result) and isinstance(batch, dict)
                        and batch.get('loaded_sha256') == saved_sha
                        and batch.get('all_native_steps_completed') is True and batch.get('all_completed') is True
                        and batch.get('expectations_matched') is True and batch.get('cancelled') is False
                        and batch.get('stop_index') is None and batch.get('stop_reason') is None
                        and not batch.get('error') and batch.get('completed_prefix_count') == len(steps)
                        and batch.get('not_started_count') == 0
                        and isinstance(children, list) and len(steps) == len(children))
                    if valid:
                        for child_index, (step, child) in enumerate(zip(steps, children)):
                            native = child.get('result') if isinstance(child, dict) else None
                            native_saved = native.get('saved_project') if isinstance(native, dict) else None
                            native_ok = (isinstance(child, dict) and child.get('index') == child_index
                                and child.get('status') == 'completed' and child.get('native_execution_completed') is True
                                and isinstance(native, dict) and native.get('ok') is True
                                and not native.get('error') and not native.get('failure') and not _rung_unknown(native)
                                and native.get('loaded_sha256') == saved_sha and isinstance(native_saved, dict)
                                and native_saved.get('sha256') == saved_sha
                                and native_saved.get('stable_during_read') is True)
                            verdict = _simulation_expectations(step, native) if native_ok else None
                            if (not native_ok or verdict != child.get('expectation')
                                    or verdict.get('matched') is False or verdict.get('native_samples_complete') is not True):
                                valid = False; break
                except (MCPToolError, ValueError, KeyError, TypeError):
                    valid = False
                saved = args.get('loaded_sha256')
                if not direct and 'completion_evidence_ref' not in row and valid and isinstance(saved, str) and re.fullmatch('[0-9a-f]{64}', saved):
                    body = {**self.scope, 'run_id': run_id, 'request_message_id': self.allowed_runs[run_id],
                        'call_id': None, 'tool_name': name, 'arguments_sha256': canonical_digest(args),
                        'result_sha256': canonical_digest(result), 'evidence_kind': 'historical_validated_scenario',
                        'generation': {'saved_sha256': saved}, 'eligible_support': True, 'outcome': 'completed',
                        'raw_receipts': [{'sequence': sequence, 'sha256': canonical_digest(raw)}]}
                    ref = 'completion-evidence:' + canonical_digest(body)
                    self.store.set_host_state('native_' + ref, body)
                    row.update(support_available=True, completion_evidence_ref=ref, saved_sha256=saved,
                               evidence_scope='recorded_model_assertions_only')
                # A failed/unknown aggregate can contain independently validated
                # completed observations. Never promote the aggregate itself.
                public = scrub_native_tool_result(result)
                observed_saved = batch.get('loaded_sha256') if isinstance(batch, dict) else None
                historical_parent = {**self.scope, 'run_id': run_id, 'request_message_id': self.allowed_runs[run_id],
                    'call_id': None, 'tool_name': name, 'arguments_sha256': canonical_digest(args),
                    'result_sha256': canonical_digest(public), 'evidence_kind': 'historical_native_result',
                    'generation': {'saved_sha256': observed_saved}, 'eligible_support': False,
                    'outcome': native_result_event(name, args, public)['native_result']['state'],
                    'raw_receipts': [{'sequence': sequence, 'sha256': canonical_digest(raw)}]}
                if direct:
                    parent_ref = evidence.get('parent_evidence_ref', arguments['evidence_ref'])
                    parent = self._evidence(parent_ref)
                else:
                    parent = historical_parent
                    parent_ref = 'completion-evidence:' + canonical_digest(parent)
                parts = self._issue_scenario_parts(parent_ref, parent, args, public, existing_only=direct)
                if parts:
                    if not direct:
                        self.store.set_host_state('native_' + parent_ref, parent)
                    row['completion_evidence_parts'] = parts
                    row['partial_support_available'] = True
                    row['evidence_scope'] = 'recorded_model_assertions_only'
            rows.append(row)
        return {'ok': True, 'read_only': True, 'action_attempted': False, 'input_state_unknown': False,
            'run_id': run_id, 'rows': rows, 'next_offset': None if direct or exhausted else offset + len(rows),
            'meaning': 'Bounded historical source lookup, not execution or input permission. Unsupported legacy receipts remain readable in their original ledger but are not promoted to completion support.'}

    def _verify_raw_refs(self, evidence):
        for item in evidence['raw_receipts']:
            key = f"native_ld_receipt:{evidence['run_id']}:{item['sequence']:08d}"
            raw = self.store.get_host_state(key)
            if not isinstance(raw, dict) or canonical_digest(raw) != item['sha256']:
                raise ValueError('completion_receipt_changed')
        if evidence.get('evidence_kind') == 'post_validated_native_result_part':
            parent = self._evidence(evidence.get('parent_evidence_ref'))
            if (any(evidence.get(key) != value for key, value in parent.items()
                    if key not in {'evidence_kind', 'eligible_support', 'outcome'})
                    or parent.get('tool_name') != 'execute_simulation_scenario'):
                raise ValueError('completion_partition_parent_mismatch')
            self._verify_raw_refs(parent)

    def _evidence(self, reference):
        if not isinstance(reference, str) or re.fullmatch(r'completion-evidence:[0-9a-f]{64}', reference) is None:
            raise ValueError('invalid_evidence_ref')
        value = self.store.get_host_state('native_' + reference)
        if not isinstance(value, dict) or 'completion-evidence:' + canonical_digest(value) != reference:
            raise ValueError('unissued_evidence_ref')
        return value


def completion_review_current(review, *, detail, current_state, current_saved_sha256):
    """Metadata-only display check; no UIA/input/profile scan or live equipment claim."""
    if not isinstance(review, dict) or review.get('accepted') is not True:
        return False
    assessment = review.get('assessment')
    if (review.get('scope') not in ('request', 'project_goal') or not isinstance(assessment, dict)
            or canonical_digest(assessment) != review.get('assessment_sha256')):
        return False
    project_basis = (detail.project_brief if detail.project_brief is not None else
        {'origin': 'host_preserved_native_project_start_request'}
        if any(getattr(item.role, 'value', item.role) == 'user'
               and isinstance(item.metadata.get('native_project_start'), dict) for item in detail.messages) else None)
    if review['scope'] == 'project_goal' and canonical_digest(project_basis) != review.get('project_basis_sha256'):
        return False
    users = [{'message_id': item.id, 'content': item.content} for item in detail.messages
             if getattr(item.role, 'value', item.role) == 'user']
    return (bool(users) and review.get('request_message_id') == users[-1]['message_id']
            and review.get('user_messages_sha256') == canonical_digest(users)
            and review.get('conversation_id') == detail.id
            and review.get('workspace_id') == detail.workspace_id
            and review.get('revision_id') == detail.revision_id
            and review.get('generation') == current_state
            and isinstance(current_state, dict) and current_state.get('saved_sha256') == current_saved_sha256
            and current_state.get('pending_current_input') is False and current_state.get('in_flight') is None)
