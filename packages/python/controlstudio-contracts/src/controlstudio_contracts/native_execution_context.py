"""One strict, pure native execution descriptor boundary for Host and Agent.

These source identities are context, never an input capability or authorization.
"""
from copy import deepcopy

from .native_completion import validate_completion_context

_REQUIRED = frozenset({'schema_version', 'workspace_id', 'revision_id', 'program_name', 'transport'})
_ALLOWED = _REQUIRED | frozenset({'preparation', 'simulation', 'workspace_recovery',
    'workspace_recovery_generation', 'current_workspace_recovery', 'restore_scope',
    'engineering_sources', 'completion_context'})


def _valid_current_workspace_recovery(value):
    return (isinstance(value, dict)
        and set(value) == {'schema_version', 'provenance', 'operation_id', 'phase',
                          'recovery_only', 'native_binding_available'}
        and value.get('schema_version') == '1'
        and value.get('provenance') == 'host_current_recovery_state/v1'
        and isinstance(value.get('operation_id'), str) and bool(value['operation_id'].strip())
        and isinstance(value.get('phase'), str) and value['phase'] in {
            'save_pending', 'save_terminal', 'saved', 'close_pending', 'closed',
            'launch_pending', 'launch_terminal', 'rebound'}
        and type(value.get('recovery_only')) is bool
        and type(value.get('native_binding_available')) is bool
        and (value['phase'] != 'rebound' or (value['recovery_only'] is False
                                          and value['native_binding_available'] is True)))


def validate_native_execution_descriptor(value, *, workflow, change_request_scope, verification_scope):
    """Return a deep copy of the only registered descriptor; reject all unknown keys."""
    if (not isinstance(workflow, str) or workflow not in {'engineering', 'new_project_planning'}
            or change_request_scope != 'permanent_offline_revision'
            or verification_scope != 'offline_native_check_only'
            or not isinstance(value, dict) or not _REQUIRED <= set(value) or not set(value) <= _ALLOWED
            or value['schema_version'] != '1'
            or not isinstance(value['transport'], str) or value['transport'] not in {'chat', 'responses'}
            or any(not isinstance(value[k], str) or not value[k] for k in ('workspace_id', 'revision_id'))
            or value['program_name'] is not None and not isinstance(value['program_name'], str)
            or any(k in value and type(value[k]) is not bool
                   for k in ('preparation', 'simulation', 'workspace_recovery'))
            or workflow == 'new_project_planning' and value.get('preparation') is not True
            or 'workspace_recovery_generation' in value and (
                value.get('workspace_recovery') is not True
                or not isinstance(value['workspace_recovery_generation'], str)
                or not value['workspace_recovery_generation'].strip())
            or 'current_workspace_recovery' in value and (
                value.get('workspace_recovery') is not True
                or not _valid_current_workspace_recovery(value['current_workspace_recovery']))
            or 'restore_scope' in value and (type(value['restore_scope']) is not str
                or len(value['restore_scope']) != 64
                or any(char not in '0123456789abcdef' for char in value['restore_scope']))):
        raise ValueError('invalid_native_execution_descriptor')
    if 'engineering_sources' in value:
        sources = value['engineering_sources']
        if (not isinstance(sources, list)
                or any(not isinstance(item, dict) or set(item) != {'source_id', 'sha256', 'revision'}
                    or any(type(item.get(k)) is not str or not item[k] for k in ('source_id', 'sha256', 'revision'))
                    or len(item['sha256']) != 64 or any(char not in '0123456789abcdef' for char in item['sha256'])
                    for item in sources)
                or len({item['source_id'] for item in sources}) != len(sources)):
            raise ValueError('invalid_native_execution_descriptor')
    result = deepcopy(value)
    if 'completion_context' in value:
        result['completion_context'] = validate_completion_context(value['completion_context'])
    return result
