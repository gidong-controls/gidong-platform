"""Strict source comparison for the observed XG-SIM native save transition.

Snapshots contain a private parsed tree for the owner, never model-visible XML.
Only the exact mutable values observed in the single-configuration native file
are excluded from source identity. Native tags have no namespace; the observed
Microsoft datatypes dt attribute namespace is retained and compared exactly.
Whole-container digests remain distinct from this logical source comparison.
"""
from __future__ import annotations

import base64
import bz2
import copy
import hashlib
import json
import re
import uuid
from typing import Any


def canonical_digest(value: Any) -> str:
    """Return a deterministic SHA-256 for a JSON-compatible value."""

    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


_CONFIG = '/Project[1]/Configurations[1]/Configuration[1]'
_ONLINE = _CONFIG + '/OnlineElements[1]'
_ALLOWED = {
    (_CONFIG, 'attribute:WriteSignature'),
    (_ONLINE + '/ForAllocationTable[1]/PulseTableData[1]', 'text'),
    (_ONLINE + '/LabelAllocationTable[1]/StringTableData[1]', 'text'),
    (_ONLINE + '/ProgramTable[1]/TableData[1]', 'text'),
    (_ONLINE + '/PulseAllocationTable[1]/PulseTableData[1]', 'text'),
    (_ONLINE + '/SubroutineAllocationTable[1]/StringTableData[1]', 'text'),
    (_ONLINE + '/TaskTable[1]/TableData[1]', 'text'),
    (_ONLINE + '/XGCommSettings[1]', 'attribute:Connnections'),
    (_ONLINE + '/XGCommSettings[1]/RWItem_StateMemento[1]', 'text'),
}
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_DATATYPE_ATTRIBUTE = '{urn:schemas-microsoft-com:datatypes}dt'
_VARIABLE_COMMENT = _CONFIG + '/GlobalVariables[1]/VariableComment[1]'
_VARIABLE_COMMENT_CHILDREN = ['Symbols', 'EIPTags', 'HMIFlags']
_XML_INDENT = re.compile(r'[ \t\r\n]*\Z')
_NETWORK_MODULE_PATH = re.compile(
    r'/Project\[1\]/NetworkConfiguration\[1\]/NetworkList\[1\]/'
    r'Network\[[1-9][0-9]*\]/NetworkModule\[[1-9][0-9]*\]\Z')
_NETWORK_MODULE_IDENTITY = frozenset({
    'ConfigName', 'ConfigType', 'Base', 'Slot', 'Id', 'ChannelType', 'OptionType'})
_RUNTIME_NETWORK_NAME = re.compile(r'(?P<label>.+)@0x[0-9a-f]{8}\Z')
_OPCUA_SERVER = (_CONFIG + '/XGPD[1]/XGPD_CONFIG_INFO_GROUP[1]/'
    'XGPD_CONFIG_INFO_FENET_XBCUXECU[1]/XGPD_CONFIG_INFO_FENET[1]/'
    'OPCUA_FEnet[1]/OPCUA_Security[1]/OPCUAServerData[1]')
_SERVER_GUID = re.compile(r'[0-9a-f]{8}(?P<s>[-_])[0-9a-f]{4}(?P=s)'
    r'[0-9a-f]{4}(?P=s)[0-9a-f]{4}(?P=s)[0-9a-f]{12}\Z', re.IGNORECASE)
_USED_LIBRARY = _ONLINE + '/UsedLibraryForPlatV2[1]'
_EMPTY_LIBRARY_ARCHIVE = b'\0' * 14
_EMPTY_LIBRARY_METADATA = (b'\xff\xfe\xff\x1a'
    + '##OBJECT_ID##FUNCTION_META'.encode('utf-16le') + b'\0' * 4)


class SimulatorSavedSourceError(ValueError):
    """The exact saved source or supported comparison shape is unavailable."""


def _tree(element):
    return {'tag': element.tag, 'attributes': dict(element.attrib),
        'text': element.text, 'tail': element.tail, 'children': [_tree(child) for child in element]}


def _nodes(tree):
    nodes = {}
    def visit(node, path):
        if not isinstance(node, dict) or set(node) != {'tag', 'attributes', 'text', 'tail', 'children'}:
            raise SimulatorSavedSourceError('Invalid private XML tree')
        if (not isinstance(node['tag'], str) or '{' in node['tag'] or ':' in node['tag']
                or not isinstance(node['attributes'], dict)
                or any(not isinstance(key, str) or (('{' in key or ':' in key) and key != _DATATYPE_ATTRIBUTE) or not isinstance(value, str)
                    for key, value in node['attributes'].items())
                or any(node[field] is not None and not isinstance(node[field], str) for field in ('text', 'tail'))
                or not isinstance(node['children'], list)):
            raise SimulatorSavedSourceError('Only native tags and the observed Microsoft datatypes dt namespace are supported')
        nodes[path] = node
        counts = {}
        for child in node['children']:
            if not isinstance(child, dict) or not isinstance(child.get('tag'), str):
                raise SimulatorSavedSourceError('Invalid private XML child')
            tag = child['tag']
            counts[tag] = counts.get(tag, 0) + 1
            visit(child, path + '/' + tag + '[' + str(counts[tag]) + ']')
    if not isinstance(tree, dict) or tree.get('tag') != 'Project':
        raise SimulatorSavedSourceError('Only the native Project root is supported')
    visit(tree, '/Project[1]')
    configurations = [path for path, node in nodes.items() if node['tag'] == 'Configuration']
    containers = [path for path, node in nodes.items() if node['tag'] == 'Configurations']
    if configurations != [_CONFIG] or containers != ['/Project[1]/Configurations[1]']:
        raise SimulatorSavedSourceError('Only one Configuration at the exact native root path is supported')
    for path, field in _ALLOWED:
        if path not in nodes or field.startswith('attribute:') and field[10:] not in nodes[path]['attributes']:
            raise SimulatorSavedSourceError('An observed mutable field is absent from this native XML shape')
    return nodes


def _variable_comment_indent(node):
    """Only XML indentation in the exact structural variable/comment container."""
    return (node is not None and node['tag'] == 'VariableComment'
        and [child['tag'] for child in node['children']] == _VARIABLE_COMMENT_CHILDREN
        and (node['text'] is None or _XML_INDENT.fullmatch(node['text']) is not None))


def _runtime_network_name_projection(path, node):
    """Only the vendor-generated object-address suffix at the exact native path.

    Keep the label verbatim. A structured marker cannot collide with a raw XML
    attribute string; all other attributes, references and children stay exact.
    """
    attributes = node['attributes']
    if (not _NETWORK_MODULE_PATH.fullmatch(path) or node['tag'] != 'NetworkModule'
            or attributes.get('Type') != 'NETWORK MODULE'
            or not _NETWORK_MODULE_IDENTITY.issubset(attributes)):
        return None
    match = _RUNTIME_NETWORK_NAME.fullmatch(attributes.get('Name', ''))
    return {'vendor_runtime_name_label': match.group('label')} if match else None


def _empty_library_projection(path, node):
    """Recognize only the native empty used-library archive and its trailer.

    Native Write serializes the empty leaf and may append the archive's
    FUNCTION_META object marker. Any library content or unknown bytes remain
    source-bearing; decompression is bounded to the two observed empty forms.
    """
    if path != _USED_LIBRARY or node['tag'] != 'UsedLibraryForPlatV2' or node['children']:
        return None
    if not node['attributes'] and node['text'] in (None, ''):
        return {'vendor_empty_used_library': True}
    if (node['attributes'] != {'Compressed': '1', _DATATYPE_ATTRIBUTE: 'bin.base64'}
            or not isinstance(node['text'], str) or len(node['text']) > 1024):
        return None
    try:
        compressed = base64.b64decode(re.sub(r'[ \t\r\n]', '', node['text']), validate=True)
        decoder = bz2.BZ2Decompressor()
        archive = decoder.decompress(compressed,
            max_length=len(_EMPTY_LIBRARY_ARCHIVE) + len(_EMPTY_LIBRARY_METADATA) + 1)
    except (ValueError, OSError, EOFError):
        return None
    if (not decoder.eof or decoder.unused_data or archive not in (
            _EMPTY_LIBRARY_ARCHIVE, _EMPTY_LIBRARY_ARCHIVE + _EMPTY_LIBRARY_METADATA)):
        return None
    return {'vendor_empty_used_library': True}


def _source_digest(tree):
    masked = copy.deepcopy(tree)
    nodes = _nodes(masked)
    generated_ids = {path: _unsigned_server_identity(path, node, nodes)
        for path, node in nodes.items() if path == _OPCUA_SERVER}
    for path, field in _ALLOWED:
        if field == 'text':
            nodes[path]['text'] = {'observed_mutable_field': True}
        else:
            nodes[path]['attributes'][field[10:]] = {'observed_mutable_field': True}
    for path, node in nodes.items():
        projection = _runtime_network_name_projection(path, node)
        if projection is not None:
            node['attributes']['Name'] = projection
        if generated_ids.get(path) is not None:
            node['attributes']['GUID'] = generated_ids[path]
        empty_library = _empty_library_projection(path, node)
        if empty_library is not None:
            node['attributes'] = {}
            node['text'] = empty_library
    container = nodes.get(_VARIABLE_COMMENT)
    if _variable_comment_indent(container):
        container['text'] = None
    return canonical_digest(masked)


def _unsigned_server_identity(path, node, nodes):
    """Native Write regenerates this unreferenced unsigned server object ID.

    Certificate settings, server configuration and any referenced identity
    remain source-bearing. The container and changed-value proofs stay exact.
    """
    attributes = node['attributes']
    value = attributes.get('GUID')
    if (path != _OPCUA_SERVER or node['tag'] != 'OPCUAServerData'
            or attributes.get('Signed') != '0' or attributes.get('CAName') != ''
            or not attributes.get('CommonName') or not isinstance(value, str)
            or _SERVER_GUID.fullmatch(value) is None):
        return None
    identity = value.replace('_', '-').casefold()
    for other_path, other in nodes.items():
        values = [other['text'], other['tail'], *(item for key, item in
            other['attributes'].items() if other_path != path or key != 'GUID')]
        if any(isinstance(item, str) and identity in item.replace('_', '-').casefold()
                for item in values):
            return None
    return {'vendor_unsigned_server_identity': True}


def saved_project_xml_snapshot(root, *, sha256: str, size: int, xml_sha256: str) -> dict[str, Any]:
    """Reuse an already parsed, same-hashed saved XML observation privately."""
    tree = _tree(root)
    return {'snapshot_version': 1, 'sha256': sha256, 'size': size, 'xml_sha256': xml_sha256,
        'source_sha256': _source_digest(tree), '_xml_tree_sha256': canonical_digest(tree), '_source_tree': tree}


def native_creation_parameter_origin_from_snapshots(before, baseline, manifest):
    """Match observed parameters to the already bound native creation baseline."""
    try:
        provenance=manifest['provenance']
        baseline_sha=manifest['source']['sha256']
        if (manifest.get('source_kind')!='new_build' or not isinstance(provenance,dict)
                or set(provenance)!={'schema','workspace_id','creation_receipt_digest','saved_project_sha256','receipt_digest'}
                or provenance['schema']!='controlstudio-native-creation-provenance/v1'
                or str(uuid.UUID(provenance['workspace_id']))!=provenance['workspace_id']
                or provenance['workspace_id']!=manifest['workspace_id']
                or any(not isinstance(provenance[key],str) or not _SHA.fullmatch(provenance[key])
                    for key in ('creation_receipt_digest','saved_project_sha256','receipt_digest'))
                or canonical_digest({key:value for key,value in provenance.items() if key!='receipt_digest'})!=provenance['receipt_digest']
                or provenance['saved_project_sha256']!=baseline_sha
                or manifest['working_copy']['baseline_sha256']!=baseline_sha
                or baseline['sha256']!=baseline_sha):
            return None
        parameters=_CONFIG+'/Parameters[1]'
        old,initial=_checked_snapshot(before).get(parameters),_checked_snapshot(baseline).get(parameters)
        if old is None or initial!=old:
            return None
        return {'workspace_id':manifest['workspace_id'],'revision_id':manifest['revision_id'],
            'source_kind':'new_build','baseline_sha256':baseline_sha,
            'creation_receipt_digest':provenance['creation_receipt_digest'],
            'parameters_sha256':canonical_digest(initial)}
    except (ValueError,TypeError,KeyError,AttributeError,RecursionError):
        return None






def native_default_parameter_change(origin, before, after, comparison=None):
    """Recognize a changed native baseline, without accepting it as equal source."""
    try:
        parameters = _CONFIG + '/Parameters[1]'
        old, new = _checked_snapshot(before).get(parameters), _checked_snapshot(after).get(parameters)
        if (not isinstance(origin, dict) or origin.get('source_kind') != 'new_build'
                or old is None or new is None or old == new
                or canonical_digest(old) != origin.get('parameters_sha256')):
            return None
        transition = comparison if comparison is not None else compare_simulator_save(before, after)
        changes = transition.get('changed_fields')
        if (transition.get('verified') is not False or not isinstance(changes, list) or not changes
                or transition.get('before', {}).get('sha256') != before['sha256']
                or transition.get('after', {}).get('sha256') != after['sha256']
                or not any(change.get('allowed') is False for change in changes)
                or any(change.get('allowed') is not True and not (
                    change.get('path') == parameters or str(change.get('path', '')).startswith(parameters + '/'))
                    for change in changes)):
            return None
        return {'origin': origin, 'parameters_after_sha256': canonical_digest(new)}
    except (ValueError, TypeError, KeyError, RecursionError):
        return None


def _checked_snapshot(snapshot):
    if (not isinstance(snapshot, dict) or snapshot.get('snapshot_version') != 1
            or not isinstance(snapshot.get('sha256'), str) or not _SHA.fullmatch(snapshot['sha256'])
            or type(snapshot.get('size')) is not int or snapshot['size'] <= 0):
        raise SimulatorSavedSourceError('Invalid saved source snapshot')
    tree = snapshot.get('_source_tree')
    nodes = _nodes(tree)
    if (canonical_digest(tree) != snapshot.get('_xml_tree_sha256')
            or _source_digest(tree) != snapshot.get('source_sha256')):
        raise SimulatorSavedSourceError('Private snapshot identity changed')
    return nodes


def _value_proof(value):
    # Report actual differences without exposing XML strings or binary payloads.
    encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')
    return {'sha256': hashlib.sha256(encoded).hexdigest(), 'json_size': len(encoded),
        'is_null': value is None}


def compare_simulator_save(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Compare every XML field; authorize no input, load or outcome promotion."""
    output = {'verified': False, 'metadata_only': False, 'changed_fields': [],
        'before': {key: before.get(key) for key in ('sha256', 'size', 'source_sha256')} if isinstance(before, dict) else None,
        'after': {key: after.get(key) for key in ('sha256', 'size', 'source_sha256')} if isinstance(after, dict) else None}
    try:
        old_nodes, new_nodes = _checked_snapshot(before), _checked_snapshot(after)
    except (SimulatorSavedSourceError, TypeError, ValueError, RecursionError) as error:
        output['verification_error'] = str(error)
        return output
    changes = output['changed_fields']
    for path in sorted(old_nodes.keys() | new_nodes.keys()):
        old, new = old_nodes.get(path), new_nodes.get(path)
        if old is None or new is None:
            changes.append({'path': path, 'field': 'node', 'allowed': False,
                'change': 'added' if old is None else 'removed'})
            continue
        fields = ('tag', 'text', 'tail', 'child_tags')
        values = lambda node: {**{field: node[field] for field in ('tag', 'text', 'tail')},
            'child_tags': [child['tag'] for child in node['children']]}
        old_values, new_values = values(old), values(new)
        for key in old['attributes'].keys() | new['attributes'].keys():
            field = 'attribute:' + key
            old_values[field] = old['attributes'].get(key)
            new_values[field] = new['attributes'].get(key)
        for field in (*fields, *sorted(key for key in old_values if key.startswith('attribute:'))):
            if old_values[field] != new_values[field]:
                formatting_only = (path == _VARIABLE_COMMENT and field == 'text'
                    and _variable_comment_indent(old) and _variable_comment_indent(new))
                old_name = _runtime_network_name_projection(path, old) if field == 'attribute:Name' else None
                runtime_address_only = (old_name is not None
                    and old_name == _runtime_network_name_projection(path, new))
                old_server = _unsigned_server_identity(path, old, old_nodes) if field == 'attribute:GUID' else None
                generated_server_id_only = (old_server is not None
                    and old_server == _unsigned_server_identity(path, new, new_nodes))
                old_library = (_empty_library_projection(path, old) if field in {
                    'text', 'attribute:Compressed', 'attribute:' + _DATATYPE_ATTRIBUTE} else None)
                empty_library_serialization_only = (old_library is not None
                    and old_library == _empty_library_projection(path, new))
                changes.append({'path': path, 'field': field,
                    'allowed': ((path, field) in _ALLOWED or formatting_only or runtime_address_only
                        or generated_server_id_only or empty_library_serialization_only),
                    'before': _value_proof(old_values[field]), 'after': _value_proof(new_values[field])})
    verified = (all(change['allowed'] for change in changes)
        and before['source_sha256'] == after['source_sha256'])
    output.update(verified=verified, metadata_only=verified and bool(changes),
        source_sha256=before['source_sha256'] if verified else None)
    if not verified:
        output['verification_error'] = 'XML contains a change outside the exact observed simulator-save fields'
    return output


def native_write_completion_verified(gui: Any) -> bool:
    if not isinstance(gui, dict):
        return False
    write, success, probe = (gui.get(key) for key in ('write_dialog', 'success_dialog', 'xgsim_probe'))
    return bool(gui.get('ok') is True and gui.get('verified') is True and gui.get('status') == 'passed'
        and isinstance(write, dict) and isinstance(success, dict) and isinstance(probe, dict)
        and isinstance(write.get('confirm'), dict) and write['confirm'].get('dispatch_state') == 'posted_once'
        and write['confirm'].get('control_id') == 1
        and success.get('success_marker') is True and isinstance(success.get('confirm'), dict)
        and success['confirm'].get('dispatch_state') == 'posted_once' and success['confirm'].get('control_id') in {1, 2}
        and success['confirm'].get('closed') is True and probe.get('pass') is True
        and probe.get('host') == '127.0.0.1' and probe.get('port') == 2004
        and probe.get('transport') == 'XGT_FENET_SIMULATOR')
