"""Shared native LD operation shape and existing standalone budget classification.

Pure public contract data only: no native implementation, authority, project data,
process access or geometry inference. Layer-owned guards and margins remain local.
"""
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

NATIVE_EDITOR_DEFAULT_SECONDS = 30.0
NATIVE_EDITOR_COMPOSITE_SECONDS = 120.0

@dataclass(frozen=True)
class NativeLDCommand:
    action: str
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...]
    mutates_program: bool
    composite: bool
    agent_function: str

_COMMANDS = (
    NativeLDCommand('inspect_plc_properties', ('program_name',), ('cpu_series',), False, True, 'editor_inspect_plc_properties'),
    NativeLDCommand('configure_plc_cpu', ('program_name', 'cpu_series', 'cpu_type'), (), False, True, 'editor_configure_plc_cpu'),
    NativeLDCommand('open_program', ('program_name',), (), False, True, 'editor_open_program'),
    NativeLDCommand('read_ld', ('program_name',), (), False, False, 'editor_read_ld'),
    NativeLDCommand('read_ld_rows', ('program_name', 'first_row', 'last_row'), (), False, False, 'editor_read_ld_rows'),
    NativeLDCommand('read_selection', ('program_name',), (), False, False, 'editor_read_selection'),
    NativeLDCommand('select_ld', ('program_name', 'column', 'visual_row'), (), False, False, 'editor_select_ld'),
    NativeLDCommand('position_ld_cell', ('program_name', 'cell', 'visual_row'), (), False, False, 'editor_position_ld_cell'),
    NativeLDCommand('write_ld_element', ('program_name', 'cell', 'element_kind', 'operand', 'visual_row'), ('symbol', 'expect_empty'), True, False, 'editor_write_ld_element'),
    NativeLDCommand('insert_contact', ('program_name', 'column', 'contact_kind', 'operand', 'visual_row'), ('symbol',), True, False, 'editor_insert_contact'),
    NativeLDCommand('insert_parallel_contact', ('program_name', 'column', 'contact_kind', 'operand', 'visual_row'), ('symbol',), True, False, 'editor_insert_parallel_contact'),
    NativeLDCommand('insert_series_contact_before', ('program_name', 'column', 'contact_kind', 'expected_element', 'operand', 'visual_row'), ('symbol',), True, True, 'editor_insert_series_contact_before'),
    NativeLDCommand('edit_ld_instruction', ('program_name', 'column', 'text', 'visual_row'), ('expected_element',), True, True, 'editor_edit_ld_instruction'),
    NativeLDCommand('insert_ld_instruction', ('program_name', 'cell', 'text', 'visual_row'), (), True, True, 'editor_insert_ld_instruction'),
    NativeLDCommand('append_instruction_rung', ('program_name', 'text', 'expected_source_sha256'), (), True, True, 'editor_append_instruction_rung'),
    NativeLDCommand('insert_ld_row_before', ('program_name', 'column', 'visual_row'), ('expected_element',), True, True, 'editor_insert_ld_row_before'),
    NativeLDCommand('insert_ld_row_after', ('program_name', 'column', 'expected_element', 'visual_row'), (), True, True, 'editor_insert_ld_row_after'),
    NativeLDCommand('delete_ld_element', ('program_name', 'column', 'expected_element', 'visual_row'), (), True, True, 'editor_delete_ld_element'),
    NativeLDCommand('delete_ld_rows', ('program_name', 'expected_rows', 'visual_row_end', 'visual_row_start'), (), True, True, 'editor_delete_ld_rows'),
    NativeLDCommand('move_instruction', ('program_name', 'column', 'destination', 'expected_element', 'visual_row'), (), True, True, 'editor_move_instruction'),
    NativeLDCommand('connect_row_terminal', ('program_name', 'column', 'expected_element', 'expected_row', 'visual_row'), (), True, True, 'editor_connect_row_terminal'),
    NativeLDCommand('set_ld_vertical_wire', ('program_name', 'column', 'present', 'visual_row'), (), True, True, 'editor_set_ld_vertical_wire'),
    NativeLDCommand('set_ld_horizontal_wire', ('program_name', 'column', 'present', 'visual_row'), (), True, True, 'editor_set_ld_horizontal_wire'),
    NativeLDCommand('apply_ld_edits', ('program_name', 'entries'), ('capture_after',), True, False, 'editor_apply_edits'),
    NativeLDCommand('program_check', ('program_name',), (), False, False, 'editor_program_check'),
    NativeLDCommand('read_check', ('program_name',), (), False, False, 'editor_read_check'),
 )
NATIVE_LD_COMMANDS = MappingProxyType({command.action: command for command in _COMMANDS})

def native_ld_actions():
    return tuple(NATIVE_LD_COMMANDS)

def native_ld_required_fields():
    return {name: set(command.required_fields) for name, command in NATIVE_LD_COMMANDS.items()}

def native_ld_composite_actions():
    return frozenset(name for name, command in NATIVE_LD_COMMANDS.items() if command.composite)

def native_ld_agent_required(action):
    command = NATIVE_LD_COMMANDS[action]
    return [name for name in command.required_fields if action == 'open_program' or name != 'program_name']

def native_ld_numeric_edits():
    return frozenset(command.agent_function for command in _COMMANDS if command.mutates_program)

def native_ld_save_request():
    # Existing bound Save service; generic key input retains its separate guards.
    return {'action': 'key', 'keys': ['CTRL', 'S']}

def native_ld_optional_fields(action):
    command = NATIVE_LD_COMMANDS.get(action)
    return set(command.optional_fields) if command is not None else set()

def native_ld_entry_seconds(action):
    command = NATIVE_LD_COMMANDS[action]
    return NATIVE_EDITOR_COMPOSITE_SECONDS if command.composite else NATIVE_EDITOR_DEFAULT_SECONDS



_DECLARED_DEVICE_PATTERN = r'^[A-Z][A-Z0-9]*[0-9][A-Z0-9.]*$'
_AUTO_END_MESSAGE = 'END is generated automatically as the final independent rung; omit it from rungs.'


def native_ld_declared_rungs_schema():
    """Return the shared JSON shape for native ladder declarations."""
    device = {'type': 'string', 'pattern': _DECLARED_DEVICE_PATTERN}
    contact = {'type': 'object', 'additionalProperties': False, 'required': ['kind', 'operand'],
               'properties': {'kind': {'enum': ['NO', 'NC']}, 'operand': device}}
    token = {'type': 'string', 'minLength': 1, 'pattern': r'^[^\s,\u0000]+$'}
    app_text = {'type': 'string', 'minLength': 1, 'maxLength': 512,
                'pattern': r'^(?!\s*[Ee][Nn][Dd](?:\s|$))[^,\u0000\r\n\t]+$'}
    mnemonic = {'type': 'string', 'minLength': 1,
                'pattern': r'^(?![Ee][Nn][Dd]$)[^\s,\u0000]+$'}
    terminal = {'oneOf': [
        {'type': 'object', 'additionalProperties': False, 'required': ['kind', 'operand'],
         'properties': {'kind': {'enum': ['OUT', 'SET', 'RST']}, 'operand': device}},
        {'type': 'object', 'additionalProperties': False, 'required': ['kind', 'text'],
         'properties': {'kind': {'const': 'APP'}, 'text': app_text}},
        {'type': 'object', 'additionalProperties': False, 'required': ['kind', 'mnemonic', 'operands'],
         'properties': {'kind': {'const': 'APP'}, 'mnemonic': mnemonic,
                        'operands': {'type': 'array', 'items': {'oneOf': [token, {'type': 'integer'}]}}}},
    ]}
    return {'type': 'array', 'minItems': 1,
            'description': 'END is generated automatically as the final independent rung; omit it from rungs.',
            'items': {'type': 'object', 'additionalProperties': False, 'required': ['branches', 'terminal'],
                      'properties': {'branches': {'type': 'array', 'minItems': 1,
                          'items': {'type': 'array', 'minItems': 1, 'maxItems': 30, 'items': contact},
                          'description': 'Parallel paths for this declared writer; each path requires at least one series contact.'},
                                     'terminal': terminal}}}


def native_ld_declared_app_text(terminal):
    """Validate and normalize native APP fields without inferring geometry."""
    if not isinstance(terminal, dict) or terminal.get('kind') != 'APP':
        raise ValueError('APP terminal requires native instruction text')
    if set(terminal) == {'kind', 'text'} and isinstance(terminal['text'], str):
        text = terminal['text']
        if not text or len(text) > 512 or any(c in text for c in '\0\r\n\t,'):
            raise ValueError('APP text must be native space-separated fields')
        fields = text.split()
    elif set(terminal) == {'kind', 'mnemonic', 'operands'}:
        mnemonic, operands = terminal['mnemonic'], terminal['operands']
        if (not isinstance(mnemonic, str) or not mnemonic or not isinstance(operands, list)
                or any(not (type(x) is int or isinstance(x, str) and x) for x in operands)):
            raise ValueError('APP requires a mnemonic and native operands')
        fields = [mnemonic, *(str(x) for x in operands)]
    else:
        raise ValueError('APP requires text or mnemonic and operands')
    if fields and fields[0].upper() == 'END':
        raise ValueError(_AUTO_END_MESSAGE)
    if (not fields or any(not f or any(c.isspace() or c in ',\0' for c in f) for f in fields)
            or len(' '.join(fields)) > 512):
        raise ValueError('APP fields must be single native tokens')
    return ' '.join(fields)


def validate_native_ld_rungs(rungs):
    """Validate declaration structure and native field syntax, without geometry."""
    import re
    if not isinstance(rungs, list) or not rungs:
        raise ValueError('rungs must be a non-empty list')
    for rung in rungs:
        if not isinstance(rung, dict) or set(rung) != {'branches', 'terminal'}:
            raise ValueError('each rung requires branches and terminal')
        branches, terminal = rung['branches'], rung['terminal']
        if (not isinstance(branches, list) or not branches
                or any(not isinstance(path, list) or not path or len(path) > 30 for path in branches)
                or not isinstance(terminal, dict)):
            raise ValueError('declared writer requires at least one contact in every path and a terminal')
        if terminal.get('kind') == 'APP':
            native_ld_declared_app_text(terminal)
        elif (set(terminal) != {'kind', 'operand'} or terminal.get('kind') not in {'OUT', 'SET', 'RST'}
              or not isinstance(terminal.get('operand'), str)
              or not re.fullmatch(_DECLARED_DEVICE_PATTERN, terminal['operand'])):
            if terminal.get('kind') == 'END':
                raise ValueError(_AUTO_END_MESSAGE)
            raise ValueError('rung requires an OUT/SET/RST device or supported APP terminal')
        for path in branches:
            for contact in path:
                if (not isinstance(contact, dict) or set(contact) != {'kind', 'operand'}
                        or contact['kind'] not in {'NO', 'NC'} or not isinstance(contact['operand'], str)
                        or not re.fullmatch(_DECLARED_DEVICE_PATTERN, contact['operand'])):
                    raise ValueError('contacts require NO/NC and a native device operand')

def native_ld_declaration_seconds(entry):
    """Budget the declared native inputs and mandatory before/after observations."""
    rungs = entry.get('rungs')
    if not isinstance(rungs, list) or not rungs:
        raise ValueError('write_ld_program requires declared rungs')
    actions = ['set_ld_horizontal_wire', 'insert_ld_instruction']  # END row
    for rung in rungs:
        branches = rung.get('branches') if isinstance(rung, dict) else None
        if not isinstance(branches, list) or not branches or any(not isinstance(path, list) for path in branches):
            raise ValueError('write_ld_program requires declared branches')
        join = max(1, max(map(len, branches)))
        for index, path in enumerate(branches):
            actions.extend(['write_ld_element'] * len(path))
            if len(path) < (31 if index == 0 else join):
                actions.append('set_ld_horizontal_wire')
        actions.extend(['set_ld_vertical_wire'] * (len(branches) - 1))
        actions.append('insert_ld_instruction' if rung.get('terminal', {}).get('kind') == 'APP'
                       else 'write_ld_element')  # terminal
    if not rungs[0]['branches'][0]:
        actions.append('delete_ld_element')  # initial seed removal
    # Before and after full reads are mandatory even without capture_after.
    # Inputs share one verified native context. Their individual deadlines are
    # still enforced by the executor; they are not additive wall-clock costs.
    return (2 * native_ld_entry_seconds('read_ld')
            + 3.0 * (len(actions) - 1) + native_ld_entry_seconds('insert_ld_instruction')
            + (NATIVE_EDITOR_COMPOSITE_SECONDS if entry.get('check_and_save') is True else 0))


def native_ld_source_sha256(rows):
    """Digest ordered literal native content, shared by captures and the driver."""
    import hashlib
    import json
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValueError('An actual whole-program native row source is required')
    source = []
    for index, row in enumerate(rows):
        header = row.get('row_header')
        records = row.get('records', row.get('elements'))
        if (row.get('visual_row') != index or not isinstance(header, dict)
                or header.get('opcode') != '67' or header.get('coordinate') != index * 1024
                or not isinstance(records, (list, tuple))):
            raise ValueError('The source must retain every ordered native row and header')
        source.append({'visual_row': index,
            'row_header': {key: header.get(key) for key in ('opcode', 'coordinate', 'flag_raw', 'value', 'raw_tokens')},
            'records': [{key: record[key] for key in ('opcode', 'coordinate', 'value')} for record in records]})
    return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


class NativeInputEffect(str, Enum):
    MAY_INPUT = 'may_input'
    NO_NATIVE_INPUT = 'no_native_input'


NATIVE_INPUT_EFFECT_CONTRACT_VERSION = '1'
# These declarations describe complete audited operation paths, including
# navigation/selection/cleanup. Non-program-mutation and read_only are separate.
_NATIVE_INPUT_EFFECTS = MappingProxyType({
    ('native_editor', 'observe'): NativeInputEffect.NO_NATIVE_INPUT,
})
_NATIVE_INPUT_AGENT_ALIASES = MappingProxyType({'editor_observe': ('native_editor', 'observe')})


def native_operation_input_effect(tool_name, arguments):
    if not isinstance(tool_name, str) or not isinstance(arguments, dict):
        return NativeInputEffect.MAY_INPUT
    alias = _NATIVE_INPUT_AGENT_ALIASES.get(tool_name)
    if alias is not None:
        return _NATIVE_INPUT_EFFECTS[alias] if not arguments else NativeInputEffect.MAY_INPUT
    action = arguments.get('action')
    if set(arguments) != {'action'} or not isinstance(action, str):
        return NativeInputEffect.MAY_INPUT
    return _NATIVE_INPUT_EFFECTS.get((tool_name, action), NativeInputEffect.MAY_INPUT)


def native_operation_effect_contract(tool_name, arguments):
    return {'schema_version': NATIVE_INPUT_EFFECT_CONTRACT_VERSION,
            'native_input_effect': native_operation_input_effect(tool_name, arguments).value}


def native_input_excluded_by_contract(value):
    return (isinstance(value, dict) and set(value) == {'schema_version', 'native_input_effect'}
            and value.get('schema_version') == NATIVE_INPUT_EFFECT_CONTRACT_VERSION
            and value.get('native_input_effect') == NativeInputEffect.NO_NATIVE_INPUT.value)
