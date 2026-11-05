"""Versioned diagnostic transport, without native control or receipt authority."""
from __future__ import annotations

import json
import math

NATIVE_FAILURE_SCHEMA = 'controlstudio-native-failure/v1'
MAX_NATIVE_FAILURE_BYTES = 8 * 1024 * 1024
MAX_NATIVE_FAILURE_DEPTH = 64


class NativeFailureProtocolError(ValueError):
    """The source diagnostic cannot be transported under this contract."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__('Native failure protocol rejected: ' + reason)


def _copy_json(value, depth=0, active=None):
    if depth > MAX_NATIVE_FAILURE_DEPTH:
        raise NativeFailureProtocolError('depth_limit')
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is str:
        try:
            value.encode('utf-8', errors='strict')
        except UnicodeError as exc:
            raise NativeFailureProtocolError('json_encoding') from exc
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise NativeFailureProtocolError('non_finite_number')
        return value
    if type(value) not in (dict, list):
        raise NativeFailureProtocolError('non_json_value')
    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise NativeFailureProtocolError('cyclic_value')
    active.add(identity)
    try:
        if type(value) is list:
            return [_copy_json(item, depth + 1, active) for item in value]
        if any(type(key) is not str for key in value):
            raise NativeFailureProtocolError('non_string_key')
        for key in value:
            _copy_json(key, depth + 1, active)
        return {key: _copy_json(item, depth + 1, active) for key, item in value.items()}
    finally:
        active.remove(identity)


def validate_native_failure(value):
    """Copy one exact supported failure; validation confers no input authority."""
    if type(value) is not dict:
        raise NativeFailureProtocolError('envelope_type')
    if value.get('schema') != NATIVE_FAILURE_SCHEMA:
        raise NativeFailureProtocolError('unsupported_schema')
    if set(value) not in ({'schema', 'kind', 'message', 'details'},
                          {'schema', 'kind', 'message', 'details', 'code'}):
        raise NativeFailureProtocolError('envelope_fields')
    if any(type(value.get(key)) is not str or not value[key] for key in ('kind', 'message')):
        raise NativeFailureProtocolError('kind_message_type')
    if 'code' in value and (type(value['code']) is not str or not value['code']):
        raise NativeFailureProtocolError('code_type')
    if type(value['details']) is not dict:
        raise NativeFailureProtocolError('details_type')
    result = _copy_json(value)
    try:
        # Count the existing ASCII-escaped CLI wire as UTF-8, including Unicode
        # escape expansion. Values themselves have already passed strict UTF-8.
        encoded = json.dumps(result, ensure_ascii=True, allow_nan=False,
                             sort_keys=True, separators=(',', ':')).encode('utf-8', errors='strict')
    except (TypeError, ValueError, UnicodeError, OverflowError) as exc:
        raise NativeFailureProtocolError('json_encoding') from exc
    if len(encoded) > MAX_NATIVE_FAILURE_BYTES:
        raise NativeFailureProtocolError('byte_limit')
    return result


def build_native_failure(kind, message, details=None, code=None):
    value = {'schema': NATIVE_FAILURE_SCHEMA, 'kind': kind, 'message': message,
             'details': {} if details is None else details}
    if code is not None:
        value['code'] = code
    return validate_native_failure(value)


def validate_native_failure_slots(value) -> None:
    """Validate explicit operation error/failure slots; diagnostic details stay opaque.

    Legacy slots without schema remain the caller's explicit legacy contract.
    This check neither projects fields nor promotes observations to authority.
    """
    if type(value) is dict and value.get('schema') == NATIVE_FAILURE_SCHEMA:
        validate_native_failure(value)
        return
    active = set()

    def visit(item, depth):
        if depth > MAX_NATIVE_FAILURE_DEPTH:
            raise NativeFailureProtocolError('depth_limit')
        if type(item) not in (dict, list):
            return
        if id(item) in active:
            raise NativeFailureProtocolError('cyclic_value')
        active.add(id(item))
        try:
            if type(item) is list:
                for child in item:
                    visit(child, depth + 1)
            else:
                for key, child in item.items():
                    if key in ('error', 'failure') and type(child) is dict and 'schema' in child:
                        validate_native_failure(child)
                        # A native failure's arbitrary details are diagnostic data,
                        # not another operation whose error slots can be inferred.
                    else:
                        visit(child, depth + 1)
        finally:
            active.remove(id(item))

    visit(value, 0)
