"""Closed simulator bit-reference shapes; address conversion belongs to CLI.

No CPU inference or device arithmetic is performed by this shared contract.
Explicitly selected notation is representation conversion, not CPU-family
inference or compatibility verification. D-word and absolute-bit contracts remain.
"""
from __future__ import annotations

import re

_ADDRESS = re.compile(r'[MP][0-9]{3,4}[0-9A-F]\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_NOTATION = 'xgk_compact_bit'


def simulator_bit_key(item, *, value=False, allow_timer=False):
    """Validate an exact input representation, without interpreting its address."""
    if not isinstance(item, dict):
        raise ValueError('Simulator bit reference must be an object')
    suffix = {'value'} if value else set()
    if set(item) == {'address', 'notation'} | suffix:
        if (not isinstance(item['address'], str) or _ADDRESS.fullmatch(item['address']) is None
                or item['notation'] != _NOTATION):
            raise ValueError('Simulator native bit address is unsupported')
        key = ('address', item['address'], item['notation'])
    elif set(item) == {'area', 'bit'} | suffix:
        if item['area'] not in (('M', 'P', 'T') if allow_timer else ('M', 'P')) or type(item['bit']) is not int or not 0 <= item['bit'] <= 2_097_151:
            raise ValueError('Simulator bit requires M/P area and an absolute integer index')
        key = (item['area'], item['bit'])
    else:
        raise ValueError('Choose exactly one native address or absolute bit reference')
    if value and type(item['value']) is not bool:
        raise ValueError('Simulator bit value must be boolean')
    return key


def resolved_simulator_devices(devices, resolution, saved_sha256, *, write=False, allow_timer=False):
    """Verify CLI resolution links for this exact request and saved source.

    The Host consumes the producer's mapping; it never repeats conversion.
    Every native input has exactly one link; aliases cannot hide duplicates.
    """
    if not isinstance(devices, list) or not 1 <= len(devices) <= 16:
        raise ValueError('Simulator bit references must contain 1 to 16 items')
    keys = [simulator_bit_key(item, value=write, allow_timer=allow_timer and not write) for item in devices]
    addresses = [key[1] for key in keys if key[0] == 'address']
    if not addresses:
        if resolution is not None:
            raise ValueError('Unexpected native address resolution')
        return [dict(item) for item in devices]
    if (not isinstance(resolution, dict) or set(resolution) != {'project_sha256', 'devices'}
            or not isinstance(resolution['project_sha256'],str)
            or _SHA.fullmatch(resolution['project_sha256']) is None
            or resolution['project_sha256'] != saved_sha256
            or not isinstance(resolution['devices'], list)):
        raise ValueError('Missing closed native address resolution')
    mapping = {}
    for item in resolution['devices']:
        if not isinstance(item, dict) or set(item) != {'address', 'notation', 'area', 'bit'}:
            raise ValueError('Invalid native address resolution link')
        simulator_bit_key({'address': item['address'], 'notation': item['notation']})
        simulator_bit_key({'area': item['area'], 'bit': item['bit']})
        if item['address'] in mapping or item['area'] != item['address'][0]:
            raise ValueError('Duplicate or cross-area native address resolution')
        mapping[item['address']] = {'area': item['area'], 'bit': item['bit']}
    if len(mapping) != len(addresses) or set(mapping) != set(addresses):
        raise ValueError('Native address resolution does not match the requested addresses')
    normalized = []
    for item, key in zip(devices, keys):
        target = mapping[key[1]] if key[0] == 'address' else {'area': item['area'], 'bit': item['bit']}
        normalized.append({**target, **({'value': item['value']} if write else {})})
    if len({(item['area'], item['bit']) for item in normalized}) != len(normalized):
        raise ValueError('Simulator references overlap after native resolution')
    return normalized
