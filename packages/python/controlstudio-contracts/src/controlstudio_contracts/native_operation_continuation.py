"""Host-issued current operation continuation; diagnostic only, no input authority."""
from __future__ import annotations

import copy
import hashlib
import json
import re

SCHEMA = 'controlstudio-current-operation-continuation/v1'
_FIELDS = frozenset({'schema','provenance','source_tool_name','source_call_id','host_request_id',
    'constraints_schema','constraints_sha256','admitted_generation','saved_sha256',
    'route_tool_name','native_input_authority','original_outcomes_preserved'})


def validate_recovery_continuation(value):
    if (not isinstance(value,dict) or set(value)!={'schema_version','provenance',
            'operation_id','phase','state','route_tool_name','native_input_authority'}
            or value.get('schema_version')!='1'
            or value.get('provenance')!='host_current_recovery_continuation/v1'
            or not isinstance(value.get('operation_id'),str) or not value['operation_id'].strip()
            or value.get('phase')!='close_pending'
            or value.get('native_input_authority') is not False
            or value.get('state') not in {'physical_wait','ready'}
            or value['state']=='physical_wait' and value.get('route_tool_name') is not None
            or value['state']=='ready' and value.get('route_tool_name') not in {'respond_owned_ui','recover_workspace'}):
        raise ValueError('Current Host recovery continuation is invalid')
    return copy.deepcopy(value)

def operation_constraints_digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()

def validate_current_operation_continuation(value):
    if (not isinstance(value,dict) or set(value)!=_FIELDS or value.get('schema')!=SCHEMA
            or value.get('provenance')!='host_current_operation_continuation/v1'
            or any(type(value.get(key)) is not str or not value[key].strip()
                   for key in ('source_tool_name','source_call_id','host_request_id','constraints_schema'))
            or type(value.get('admitted_generation')) is not str
            or re.fullmatch(r'host_verified_native_binding/v2:[0-9a-f]{64}',value['admitted_generation']) is None
            or any(type(value.get(key)) is not str or re.fullmatch(r'[0-9a-f]{64}',value[key]) is None
                   for key in ('constraints_sha256','saved_sha256'))
            or value.get('route_tool_name') not in {'recover_workspace','simulator'}
            or value.get('route_tool_name')=='simulator' and value.get('source_tool_name')!='simulator'
            or value.get('native_input_authority') is not False
            or value.get('original_outcomes_preserved') is not True):
        raise ValueError('Current operation continuation is invalid')
    return copy.deepcopy(value)
