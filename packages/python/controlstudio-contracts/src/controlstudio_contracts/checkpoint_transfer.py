"""Lossless checkpoint transfer independent of the model context window."""

import hashlib
import json
from typing import Any, Iterator

CHUNK_CHARACTERS = 16_384


class EncodedCheckpoint(tuple):
    """Immutable canonical bytes/digest, independent of the mutable source tree."""
    __slots__ = ()

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("Use encode_checkpoint")

    @property
    def text(self) -> str:
        return self[0]

    @property
    def digest(self) -> str:
        return self[1]

    def chunks(self) -> Iterator[dict[str, Any]]:
        for index, offset in enumerate(range(0, len(self.text), CHUNK_CHARACTERS)):
            yield {"checkpointDigest": self.digest, "index": index,
                   "text": self.text[offset:offset + CHUNK_CHARACTERS]}


class VerifiedCheckpointTransfer(EncodedCheckpoint):
    """Host-local proof of an exact joined UTF-8 transfer and its SHA, not schema."""
    __slots__ = ()


def encode_checkpoint(value: dict[str, Any]) -> EncodedCheckpoint:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return tuple.__new__(EncodedCheckpoint, (text, digest))


def checkpoint_chunks(value: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield from encode_checkpoint(value).chunks()


NATIVE_DELTA_SCHEMA = "controlstudio-native-checkpoint-append/v1"
_NATIVE_ARRAYS = ("completed_pairs", "records", "transport_records")


def _native_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


class NativeCheckpointState(tuple):
    """Canonical per-entry bytes; previous entries remain immutable and exact."""
    __slots__ = ()

    def __new__(cls, values):
        state = tuple.__new__(cls, values)
        digest = hashlib.sha256()
        for part in state.parts(): digest.update(part.encode("utf-8"))
        return tuple.__new__(cls, (*values, digest.hexdigest()))

    @property
    def counts(self) -> tuple[int, int, int]:
        return tuple(len(items) for items in self[1:4])

    def parts(self) -> Iterator[str]:
        yield '{"private_state":{"transport_records":['
        for index, item in enumerate(self[3]):
            if index: yield ','
            yield item
        yield ']},"public_state":{"completed_pairs":['
        for index, item in enumerate(self[1]):
            if index: yield ','
            yield item
        yield '],"model_prompt":' + self[0] + ',"records":['
        for index, item in enumerate(self[2]):
            if index: yield ','
            yield item
        yield ']},"schemaVersion":"1"}'

    @property
    def text(self) -> str:
        return ''.join(self.parts())

    @property
    def digest(self) -> str:
        return self[4]


def native_checkpoint_state(value: dict[str, Any]) -> NativeCheckpointState:
    if (type(value) is not dict or set(value) != {'schemaVersion','public_state','private_state'}
            or value['schemaVersion'] != '1'):
        raise ValueError('native checkpoint shape is invalid')
    public, private = value['public_state'], value['private_state']
    if (type(public) is not dict or set(public) != {'model_prompt','completed_pairs','records'}
            or type(public['model_prompt']) is not str
            or type(private) is not dict or set(private) != {'transport_records'}
            or any(type(items) is not list for items in
                   (public['completed_pairs'],public['records'],private['transport_records']))):
        raise ValueError('native checkpoint state is invalid')
    return NativeCheckpointState((_native_json(public['model_prompt']),
        *(tuple(_native_json(item) for item in items) for items in
          (public['completed_pairs'],public['records'],private['transport_records']))))


def encode_native_checkpoint_update(value: dict[str, Any], previous: NativeCheckpointState | None
        ) -> tuple[EncodedCheckpoint, NativeCheckpointState]:
    current = native_checkpoint_state(value)
    # Compare canonical entry bytes, not object identity. A mutable historical
    # entry cannot silently cross this boundary; legacy full transport retains
    # its original prefix rejection/acceptance semantics on any changed prefix.
    if (previous is None or previous[0] != current[0]
            or any(len(new) < len(old) or new[:len(old)] != old
                   for old,new in zip(previous[1:4],current[1:4]))):
        return tuple.__new__(EncodedCheckpoint,(current.text,current.digest)), current
    suffixes = [list(items[length:]) for items,length in zip(current[1:4],previous.counts)]
    update = {'schema':NATIVE_DELTA_SCHEMA,'base_digest':previous.digest,
        'digest':current.digest,'base_counts':list(previous.counts),
        'model_prompt':json.loads(current[0]),
        'append':{name:[json.loads(item) for item in items]
                  for name,items in zip(_NATIVE_ARRAYS,suffixes)}}
    return encode_checkpoint(update),current


def apply_native_checkpoint_update(previous: NativeCheckpointState, value: dict[str, Any]
        ) -> NativeCheckpointState:
    if (type(value) is not dict or set(value) != {'schema','base_digest','digest','base_counts','model_prompt','append'}
            or value['schema'] != NATIVE_DELTA_SCHEMA or type(previous) is not NativeCheckpointState
            or type(value['base_counts']) is not list or len(value['base_counts']) != 3
            or any(type(count) is not int or count < 0 for count in value['base_counts'])
            or tuple(value['base_counts']) != previous.counts
            or value['base_digest'] != previous.digest or _native_json(value['model_prompt']) != previous[0]
            or type(value['append']) is not dict or set(value['append']) != set(_NATIVE_ARRAYS)
            or any(type(value['append'][name]) is not list for name in _NATIVE_ARRAYS)):
        raise ValueError('native checkpoint append base or shape differs')
    state = NativeCheckpointState((previous[0],*(old+tuple(_native_json(item) for item in value['append'][name])
        for name,old in zip(_NATIVE_ARRAYS,previous[1:4]))))
    if type(value['digest']) is not str or state.digest != value['digest']:
        raise ValueError('native checkpoint append digest differs')
    return state


class CheckpointTransfer:
    def __init__(self) -> None:
        self._parts: list[str] = []
        self._digest: str | None = None

    def append(self, part: dict[str, Any]) -> None:
        if (
            set(part) != {"checkpointDigest", "index", "text"}
            or type(part["index"]) is not int
            or part["index"] != len(self._parts)
            or not isinstance(part["text"], str)
            or not 0 < len(part["text"]) <= CHUNK_CHARACTERS
            or not isinstance(part["checkpointDigest"], str)
            or len(part["checkpointDigest"]) != 64
            or (self._digest is not None and part["checkpointDigest"] != self._digest)
        ):
            raise ValueError("checkpoint transfer chunk is invalid or out of order")
        self._digest = part["checkpointDigest"]
        self._parts.append(part["text"])

    def finish_encoded(self, digest: str) -> VerifiedCheckpointTransfer:
        text = "".join(self._parts)
        self._parts.clear()
        expected, self._digest = self._digest, None
        if digest != expected or hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
            raise ValueError("checkpoint transfer digest differs")
        return tuple.__new__(VerifiedCheckpointTransfer, (text, digest))

    def finish(self, digest: str) -> dict[str, Any]:
        value = json.loads(self.finish_encoded(digest).text)
        if not isinstance(value, dict):
            raise ValueError("checkpoint transfer must contain an object")
        return value
