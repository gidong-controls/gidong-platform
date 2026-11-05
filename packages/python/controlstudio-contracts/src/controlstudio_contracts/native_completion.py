"""Model-authored completion assessment; references identify sources, never input authority."""
from __future__ import annotations

import copy
import hashlib
import json
import re

_REF = r'^completion-(?:requirement|evidence):[0-9a-f]{64}$'
_TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 512}
_REF_LIST = {'type': 'array', 'maxItems': 16, 'uniqueItems': True,
             'items': {'type': 'string', 'pattern': _REF}}
COMPLETION_ASSESSMENT_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'schema_version': {'type': 'string', 'enum': ['1']},
        'scope': {'type': 'string', 'enum': ['request', 'project_goal']},
        'requirement_ref': {'type': 'string', 'pattern': _REF},
        'criteria': {'type': 'array', 'minItems': 1, 'maxItems': 16, 'items': {
            'type': 'object', 'additionalProperties': False, 'properties': {
                'criterion': _TEXT, 'verdict': {'type': 'string', 'enum': ['met', 'not_met', 'unverified']},
                'evidence_refs': _REF_LIST, 'reuse_basis': {'type': 'string', 'enum': ['current_execution', 'same_saved_source'],
                    'description': 'current_execution cites actions observed during this request/run at their recorded versions, not automatically the final state. Use final observations for final-state claims. same_saved_source reuses prior-run evidence for the unchanged saved source in project_goal scope or a resumed run of the same original request; use fresh observations for current final-state claims.'},
                'change_impact': {**_TEXT, 'description': 'Required when reusing same_saved_source evidence; optional for current_execution.'}}, 'required': ['criterion', 'verdict', 'evidence_refs', 'reuse_basis']}},
        'diagnostic_refs': _REF_LIST,
        'remaining_work': {'type': 'array', 'maxItems': 16, 'items': {
            'type': 'object', 'additionalProperties': False, 'properties': {
                'scope': {'type': 'string', 'enum': ['request', 'project_goal']}, 'text': _TEXT},
            'required': ['scope', 'text']}},
    }, 'required': ['schema_version', 'scope', 'requirement_ref', 'criteria', 'diagnostic_refs', 'remaining_work'],
}


class CompletionAssessmentFieldError(ValueError):
    def __init__(self, loc, kind, expected):
        super().__init__('invalid_completion_assessment')
        self.detail = {'loc': list(loc), 'type': kind, 'expected': expected}


def validate_completion_assessment(value):
    def require(condition):
        if not condition:
            raise ValueError('invalid_completion_assessment')
    def text(item):
        return isinstance(item, str) and 0 < len(item) <= 512 and bool(item.strip())
    def refs(items):
        return (isinstance(items, list) and len(items) <= 16
                and all(isinstance(item, str) and re.fullmatch(_REF, item) for item in items)
                and len(set(items)) == len(items))
    require(isinstance(value, dict) and set(value) == set(COMPLETION_ASSESSMENT_SCHEMA['required']))
    require(value['schema_version'] == '1' and value['scope'] in ('request', 'project_goal'))
    if not (isinstance(value['requirement_ref'], str) and re.fullmatch(_REF, value['requirement_ref'])):
        raise CompletionAssessmentFieldError(('requirement_ref',), 'string_pattern', _REF)
    criteria = value['criteria']
    require(isinstance(criteria, list) and 1 <= len(criteria) <= 16)
    for item in criteria:
        require(isinstance(item, dict) and {'criterion', 'verdict', 'evidence_refs', 'reuse_basis'} <= set(item)
                and set(item) <= {'criterion', 'verdict', 'evidence_refs', 'reuse_basis', 'change_impact'})
        require(text(item['criterion']) and item['verdict'] in ('met', 'not_met', 'unverified'))
        require(refs(item['evidence_refs']))
        require(item['reuse_basis'] in ('current_execution', 'same_saved_source'))
        if item['reuse_basis'] == 'same_saved_source' or 'change_impact' in item:
            require(text(item.get('change_impact')))
    require(refs(value['diagnostic_refs']))
    require(isinstance(value['remaining_work'], list) and len(value['remaining_work']) <= 16)
    for item in value['remaining_work']:
        require(isinstance(item, dict) and set(item) == {'scope', 'text'})
        require(item['scope'] in ('request', 'project_goal') and text(item['text']))
    return copy.deepcopy(value)

READ_COMPLETION_SOURCES_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'properties': {'run_id': {'type': 'string', 'minLength': 1, 'maxLength': 200},
                   'evidence_ref': {'type': 'string', 'pattern': r'^completion-evidence:[0-9a-f]{64}$'},
                   'offset': {'type': 'integer', 'minimum': 0},
                   'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}},
    'oneOf': [{'not': {'required': ['evidence_ref']}},
              {'required': ['evidence_ref'], 'not': {'anyOf': [{'required': ['run_id']},
                  {'required': ['offset']}, {'required': ['limit']}]}}]}


def completion_settlement_result_digest(result):
    """Bind the actual result before Host completion annotations are attached."""
    body = {key: value for key, value in result.items()
            if key not in {'completion_evidence_ref', 'completion_evidence_parts', 'finalization_settlement'}}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def validate_finalization_settlement(value):
    """A current SIM ledger settlement, never goal acceptance or input authority."""
    def require(condition):
        if not condition:
            raise ValueError('invalid_finalization_settlement')
    def sha(item):
        return isinstance(item, str) and re.fullmatch('[0-9a-f]{64}', item) is not None
    require(isinstance(value, dict) and set(value) == {
        'schema_version', 'provenance', 'scope', 'settled_domain', 'requirement_ref',
        'admitted_generation', 'source_call_id', 'completion_evidence_ref', 'result_sha256',
        'saved_sha256', 'generation_sha256', 'epoch', 'settled_source',
        'native_input_authority', 'original_outcomes_preserved'})
    require(value['schema_version'] == '1' and value['provenance'] == 'host_completion_settlement/v1'
            and value['scope'] == 'finalization_only' and value['settled_domain'] == 'simulator'
            and value['native_input_authority'] is False and value['original_outcomes_preserved'] is True)
    require(isinstance(value['requirement_ref'], str)
            and re.fullmatch('completion-requirement:[0-9a-f]{64}', value['requirement_ref']) is not None)
    require(isinstance(value['admitted_generation'], str)
            and re.fullmatch('host_verified_native_binding/v2:[0-9a-f]{64}', value['admitted_generation']) is not None)
    require(isinstance(value['source_call_id'], str) and 0 < len(value['source_call_id']) <= 200)
    require(isinstance(value['completion_evidence_ref'], str)
            and re.fullmatch('completion-evidence:[0-9a-f]{64}', value['completion_evidence_ref']) is not None)
    require(all(sha(value[key]) for key in ('result_sha256', 'saved_sha256', 'generation_sha256'))
            and type(value['epoch']) is int and value['epoch'] >= 0)
    source = value['settled_source']
    require(isinstance(source, dict) and set(source) == {'call_id', 'result_sha256', 'completion_evidence_ref'}
            and isinstance(source['call_id'], str) and 0 < len(source['call_id']) <= 200
            and sha(source['result_sha256']) and isinstance(source['completion_evidence_ref'], str)
            and re.fullmatch('completion-evidence:[0-9a-f]{64}', source['completion_evidence_ref']) is not None)
    return copy.deepcopy(value)


def validate_completion_context(value):
    """Strict Host-to-Agent source contract. It carries no native authority."""
    def require(condition):
        if not condition:
            raise ValueError('invalid_completion_context')
    def identifier(item):
        return isinstance(item, str) and 0 < len(item) <= 200
    require(isinstance(value, dict) and set(value) == {
        'schema_version', 'requirement_refs', 'sources', 'available_prior_runs', 'meaning'})
    require(value['schema_version'] == '1' and isinstance(value['meaning'], str) and len(value['meaning']) <= 1000)
    refs = value['requirement_refs']; sources = value['sources']
    require(isinstance(refs, dict) and 'request' in refs and set(refs) <= {'request', 'project_goal'})
    require(all(isinstance(ref, str) and re.fullmatch('completion-requirement:[0-9a-f]{64}', ref) for ref in refs.values()))
    require(isinstance(sources, dict) and set(sources) == set(refs))
    request = sources['request']
    require(isinstance(request, dict) and set(request) == {'request_message_id', 'content'}
            and identifier(request['request_message_id']) and isinstance(request['content'], str))
    if 'project_goal' in sources:
        goal = sources['project_goal']
        require(isinstance(goal, dict) and set(goal) == {'project_brief', 'ordered_user_messages'}
                and isinstance(goal['project_brief'], dict) and isinstance(goal['ordered_user_messages'], list))
        for item in goal['ordered_user_messages']:
            require(isinstance(item, dict) and set(item) == {'message_id', 'content'}
                    and identifier(item['message_id']) and isinstance(item['content'], str))
    runs = value['available_prior_runs']
    require(isinstance(runs, list))
    for item in runs:
        require(isinstance(item, dict) and set(item) == {'run_id', 'request_message_id'}
                and identifier(item['run_id']) and identifier(item['request_message_id']))
    require(len({item['run_id'] for item in runs}) == len(runs))
    return copy.deepcopy(value)
