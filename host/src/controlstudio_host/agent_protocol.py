"""NDJSON protocol shared by the Platform Host and the Agent sidecar.

This module has no MCP dependency.  It intentionally validates the small
supervision wire separately from the model/tool payloads so a malformed child
cannot be mistaken for an Agent result.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from controlstudio_contracts import AGENT_PROTOCOL_VERSION_V1, AgentManifestV1
from controlstudio_contracts.checkpoint_transfer import (
    EncodedCheckpoint,
    NativeCheckpointState,
    VerifiedCheckpointTransfer,
    apply_native_checkpoint_update,
    native_checkpoint_state,
)
from controlstudio_contracts.native_failure import validate_native_failure
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_PROTOCOL_LINE_BYTES = 2_000_000


def _without_host_binding_admission(value: Any) -> Any:
    """External CLI/MCP diagnostics cannot mint or forward Host admission proof."""
    if isinstance(value, dict):
        return {key: _without_host_binding_admission(item) for key, item in value.items()
                if key not in {'host_binding_admission', 'finalization_settlement', 'completion_evidence_ref'}}
    if isinstance(value, list):
        return [_without_host_binding_admission(item) for item in value]
    return value


def validated_native_work_plan_progress(payload: Mapping[str, Any], run_id: str) -> dict[str, Any]:
    """Present the recorded model plan without promoting it to action evidence."""
    plan = payload.get('work_plan')
    elapsed = payload.get('elapsed_seconds')
    if (set(payload) != {'run_id', 'stage', 'elapsed_seconds', 'work_plan'}
            or payload.get('run_id') != run_id or payload.get('stage') != 'native_work_plan'
            or type(elapsed) is not int or elapsed < 0
            or not isinstance(plan, dict)
            or set(plan) != {'text', 'source', 'is_tool_success_evidence'}
            or not isinstance(plan.get('text'), str) or not plan['text'].strip()
            or len(plan['text']) > 6000 or plan.get('source') != 'model_working_memory'
            or plan.get('is_tool_success_evidence') is not False):
        raise AgentProtocolError('Agent native work plan progress is invalid')
    return {'stage': 'native_work_plan', 'elapsedSeconds': elapsed, 'work_plan': dict(plan)}


def validated_knowledge_event(payload: Mapping[str, Any], run_id: str) -> tuple[str, dict[str, Any]]:
    """A local search receipt cannot masquerade as a Host/native tool receipt."""
    event_type = payload.get("event_type")
    tool_name = payload.get('tool_name', 'search_engineering_knowledge')
    fields = {"run_id", "event_type", "request_id", "tool_call_id"}
    if 'tool_name' in payload:fields.add('tool_name')
    if event_type == "tool.completed":
        fields.add("knowledge_result_count")
    if (set(payload) != fields or payload.get("run_id") != run_id
            or tool_name not in {'search_engineering_knowledge','read_engineering_source'}
            or event_type not in {"tool.selected", "tool.started", "tool.completed", "tool.failed"}
            or any(not isinstance(payload.get(key), str) or not 1 <= len(payload[key]) <= 128
                   for key in ("request_id", "tool_call_id"))
            or (event_type == "tool.completed" and
                (type(payload["knowledge_result_count"]) is not int
                 or not 0 <= payload["knowledge_result_count"] <= 50))):
        raise AgentProtocolError("Agent knowledge event payload is invalid")
    event = {key: payload[key] for key in ("request_id", "tool_call_id")}
    event.update(tool_name="search_engineering_knowledge", activity_label="설비 자료·명령 매뉴얼 조회",
                 activity_role="자료 조사")
    if event_type == "tool.completed":
        event["knowledge_result_count"] = payload["knowledge_result_count"]
        event["activity_result"] = f"관련 자료 {payload['knowledge_result_count']}건을 조회했습니다."
    elif event_type == "tool.failed":
        event.update(failure_kind="knowledge_search", failure_reason="자료 조회를 완료하지 못했습니다.",
                     activity_result="자료 조회를 완료하지 못했습니다.")
    if tool_name == 'read_engineering_source':
        event.update(tool_name=tool_name,activity_label='첨부 자료 본문 읽기')
        if event_type == 'tool.completed':
            event['activity_result'] = f"첨부 자료의 본문 구간 {payload['knowledge_result_count']}개를 조회했습니다."
        elif event_type == 'tool.failed':
            event['failure_kind'] = 'engineering_source_read'
    return event_type, event


def _freeze_native_snapshot(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({key: _freeze_native_snapshot(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze_native_snapshot(item) for item in value)
    return value


def _native_snapshot_matches_json(snapshot: Any, value: Any) -> bool:
    """Compare against the exact dict/list freeze semantics without allocating."""
    if type(value) is dict:
        return (isinstance(snapshot, Mapping) and snapshot.keys() == value.keys()
                and all(_native_snapshot_matches_json(snapshot[key], item)
                        for key, item in value.items()))
    if type(value) is list:
        return (type(snapshot) is tuple and len(snapshot) == len(value)
                and all(_native_snapshot_matches_json(left, right)
                        for left, right in zip(snapshot, value)))
    # Container equality treats identical leaves as equal, including opaque
    # non-JSON leaves on legacy invalid-input paths; bool/int equality is kept.
    return snapshot is value or snapshot == value


def _native_completed_prefix_matches(snapshot: tuple, prefix: Any) -> bool:
    size = len(prefix)
    if (type(prefix) is list and len(snapshot) >= size
            and all(_native_snapshot_matches_json(snapshot[index], item)
                    for index, item in enumerate(prefix))):
        return True
    # Rejected or non-JSON-shaped inputs retain the previous comparison and
    # exception behavior. The ordinary accepted JSON prefix allocates no copy.
    return snapshot[:size] == _freeze_native_snapshot(prefix)


class ValidatedNativeCheckpoint(tuple, Mapping[str, Any]):
    """Host-only immutable mapping plus its fully verified canonical JSON/SHA.

    Tuple backing cannot be changed by dict/list base mutators. Its state is
    recursively MappingProxyType/tuple, never a mutable dict/list subclass.
    The Host callback reads this mapping; external wire DTOs remain ordinary JSON.
    """
    __slots__ = ()

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("Use the full native checkpoint validation boundary")

    def __getitem__(self, key: str) -> Any:
        return tuple.__getitem__(self, 0)[key]

    def __iter__(self):
        return iter(tuple.__getitem__(self, 0))

    def __len__(self) -> int:
        return len(tuple.__getitem__(self, 0))

    @property
    def canonical_json(self) -> str:
        encoded = tuple.__getitem__(self, 1)
        return encoded.text if type(encoded) is NativeCheckpointState else encoded

    @property
    def digest(self) -> str:
        return tuple.__getitem__(self, 2)

    @property
    def encoded(self) -> EncodedCheckpoint:
        """Reuse fully validated immutable canonical text; this grants no authority."""
        return tuple.__new__(EncodedCheckpoint, (self.canonical_json, self.digest))

    def completed_prefix_matches(self, old_public: Mapping[str, Any]) -> bool:
        if type(old_public) is StoredNativeCheckpointPrefix:
            update = self.append_update
            if update is not None:
                return (update['base_digest'] == old_public.digest
                        and tuple(update['base_counts']) == old_public.counts
                        and update['model_prompt'] == old_public.model_prompt)
            old_public = old_public.load()
        public = self['public_state']
        return (old_public['model_prompt'] == public['model_prompt']
                and _native_completed_prefix_matches(public['completed_pairs'], old_public['completed_pairs'])
                and _native_completed_prefix_matches(public['records'], old_public['records']))

    @property
    def incremental_state(self) -> NativeCheckpointState:
        if tuple.__len__(self) > 3:
            return tuple.__getitem__(self, 3)
        return native_checkpoint_state(json.loads(self.canonical_json))

    @property
    def append_update(self) -> dict | None:
        if tuple.__len__(self) > 4 and tuple.__getitem__(self, 4) is not None:
            return json.loads(tuple.__getitem__(self, 4))
        return None

    @property
    def restore_reference(self):
        overlay = tuple.__getitem__(self, 6) if tuple.__len__(self) > 6 else None
        base = self.append_base if overlay is not None else self
        return {'baseDigest': base.digest, 'baseCounts': list(base.incremental_state.counts),
                'checkpointDigest': self.digest, 'objective': json.loads(overlay) if overlay is not None else None}

    @property
    def append_base(self):
        if tuple.__len__(self) > 5:
            frozen, state = tuple.__getitem__(self, 5)
            return tuple.__new__(ValidatedNativeCheckpoint,(frozen,state,state.digest,state))
        return None


class StoredNativeCheckpointPrefix(Mapping):
    """Internal stored head identity; ordinary full callers load exact history."""
    def __init__(self, digest, counts, model_prompt, loader):
        self.digest, self.counts, self.model_prompt, self.load = digest, tuple(counts), model_prompt, loader

    def __getitem__(self, key):
        return self.load()[key]

    def __iter__(self):
        return iter(('model_prompt','completed_pairs','records'))

    def __len__(self):
        return 3


def validated_native_checkpoint_append(payload: VerifiedCheckpointTransfer,
        previous: ValidatedNativeCheckpoint) -> ValidatedNativeCheckpoint:
    if type(payload) is not VerifiedCheckpointTransfer or type(previous) is not ValidatedNativeCheckpoint:
        raise AgentProtocolError('Native append requires its verified full base')
    try:
        update = json.loads(payload.text)
        if json.dumps(update,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False) != payload.text:
            raise ValueError('noncanonical append')
        previous_state = previous.incremental_state
        state = apply_native_checkpoint_update(previous_state,update)
        appended = update['append']
        old_public, old_private = previous['public_state'], previous['private_state']
        frozen = MappingProxyType({'schemaVersion':'1',
            'public_state':MappingProxyType({'model_prompt':old_public['model_prompt'],
                'completed_pairs':old_public['completed_pairs']+_freeze_native_decoded_snapshot(appended['completed_pairs']),
                'records':old_public['records']+_freeze_native_decoded_snapshot(appended['records'])}),
            'private_state':MappingProxyType({'transport_records':old_private['transport_records']+
                _freeze_native_decoded_snapshot(appended['transport_records'])})})
        # Keep only the immediately previous immutable content/state, never a
        # recursive series of prior checkpoint objects. This lets a new run's
        # first delta seed/reuse its already verified cold-resume base.
        return tuple.__new__(ValidatedNativeCheckpoint,(frozen,state,state.digest,state,payload.text,
            (tuple.__getitem__(previous,0),previous_state)))
    except (TypeError,ValueError,KeyError) as exc:
        raise AgentProtocolError('Native append checkpoint is invalid') from exc


def native_checkpoint_objective(previous: ValidatedNativeCheckpoint, model_prompt: str,
                                record: dict[str, Any]) -> ValidatedNativeCheckpoint:
    """Own only the new objective/event; retain the exact immutable full prefix."""
    if type(previous) is not ValidatedNativeCheckpoint or type(model_prompt) is not str:
        raise AgentProtocolError('Native objective requires a validated snapshot')
    encoded_record = json.dumps(record, ensure_ascii=False, sort_keys=True,
                                separators=(',', ':'), allow_nan=False)
    _assert_native_decoded_json(record)
    state = previous.incremental_state
    current = NativeCheckpointState((json.dumps(model_prompt, ensure_ascii=False), state[1],
                                     state[2] + (encoded_record,), state[3]))
    public = previous['public_state']
    frozen = MappingProxyType({'schemaVersion': '1',
        'public_state': MappingProxyType({'model_prompt': model_prompt,
            'completed_pairs': public['completed_pairs'],
            'records': public['records'] + (_freeze_native_decoded_snapshot(record),)}),
        'private_state': previous['private_state']})
    overlay = json.dumps({'model_prompt': model_prompt, 'record': record}, ensure_ascii=False,
                         sort_keys=True, separators=(',', ':'), allow_nan=False)
    return tuple.__new__(ValidatedNativeCheckpoint, (frozen, current, current.digest, current, None,
        (tuple.__getitem__(previous, 0), state), overlay))


def native_checkpoint_history_extends(current: ValidatedNativeCheckpoint,
        previous: ValidatedNativeCheckpoint) -> bool:
    return (current.completed_prefix_matches(previous['public_state'])
        and _native_completed_prefix_matches(current['private_state']['transport_records'],
                                              previous['private_state']['transport_records']))


def _freeze_native_decoded_snapshot(value: Any) -> Any:
    """One strict JSON type walk creates the immutable snapshot."""
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError("native decoded object keys must be strings")
        return MappingProxyType({key: _freeze_native_decoded_snapshot(item)
                                 for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze_native_decoded_snapshot(item) for item in value)
    if value is not None and type(value) not in {str, bool, int, float}:
        raise ValueError("native decoded tree must contain JSON values")
    return value


def validated_native_checkpoint_transfer(payload: VerifiedCheckpointTransfer) -> ValidatedNativeCheckpoint:
    """Validate untrusted canonical JSON/shape once; reuse only its verified SHA.

    Transfer proof alone grants no checkpoint validity, binding or authority.
    Canonical equality also rejects duplicate keys and normalization changes.
    """
    if type(payload) is not VerifiedCheckpointTransfer:
        raise AgentProtocolError("Native checkpoint transfer proof is invalid")
    canonical, actual, encoded = _validated_native_checkpoint_parts(
        json.loads(payload.text), payload.digest, decoded_json=True, verified_transfer=payload)
    return tuple.__new__(ValidatedNativeCheckpoint,
                         (_freeze_native_decoded_snapshot(canonical), encoded, actual))


def validated_native_checkpoint_snapshot(checkpoint: Mapping[str, Any], digest: str | None = None,
        *, decoded_json: bool = True) -> ValidatedNativeCheckpoint:
    if type(checkpoint) is ValidatedNativeCheckpoint:
        if digest is not None and checkpoint.digest != digest:
            raise AgentProtocolError('Native checkpoint digest mismatched')
        return checkpoint
    canonical, actual, encoded = _validated_native_checkpoint_parts(
        checkpoint, digest, decoded_json=decoded_json, native_state=True)
    state = encoded if type(encoded) is NativeCheckpointState else native_checkpoint_state(canonical)
    if state.digest != actual:
        raise AgentProtocolError('Native checkpoint canonical state differs')
    return tuple.__new__(ValidatedNativeCheckpoint, (_freeze_native_snapshot(canonical), state, actual, state))


def validated_native_checkpoint(
    checkpoint: Mapping[str, Any], digest: str | None = None, *, decoded_json: bool = False,
) -> tuple[dict[str, Any], str]:
    """Keep native public evidence separate from private transport continuation."""
    canonical, actual, _ = _validated_native_checkpoint_parts(checkpoint, digest, decoded_json=decoded_json)
    return canonical, actual


def _validated_native_checkpoint_parts(
    checkpoint: Mapping[str, Any], digest: str | None = None, *, decoded_json: bool = False,
    verified_transfer: VerifiedCheckpointTransfer | None = None,
    native_state: bool = False,
) -> tuple[dict[str, Any], str, str | NativeCheckpointState]:
    if (set(checkpoint) != {"schemaVersion", "public_state", "private_state"}
            or checkpoint.get("schemaVersion") != "1"):
        raise AgentProtocolError("Native checkpoint shape is invalid")
    try:
        encoded = (native_checkpoint_state(checkpoint) if native_state and decoded_json else
                   json.dumps(checkpoint, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False))
        if decoded_json:
            # Only transfer.finish's JSON-decoded tree uses this path. Refuse
            # normalization-requiring Python objects; retain every full digest check.
            if verified_transfer is None:
                _assert_native_decoded_json(checkpoint)
            canonical = checkpoint
        else:
            canonical = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise AgentProtocolError("Native checkpoint is not strict JSON") from exc
    if verified_transfer is not None:
        if encoded != verified_transfer.text:
            raise AgentProtocolError("Native checkpoint digest mismatched")
        actual = verified_transfer.digest
    else:
        actual = (encoded.digest if type(encoded) is NativeCheckpointState else
                  hashlib.sha256(encoded.encode("utf-8")).hexdigest())
    public, private = canonical["public_state"], canonical["private_state"]
    if (not isinstance(public, dict)
            or set(public) != {"model_prompt", "completed_pairs", "records"}
            or not isinstance(public["model_prompt"], str)
            or not isinstance(public["completed_pairs"], list)
            or not isinstance(public["records"], list)
            or not isinstance(private, dict)
            or set(private) != {"transport_records"}
            or not isinstance(private["transport_records"], list)):
        raise AgentProtocolError("Native checkpoint state is invalid")
    # Agent-authored observation/history is untrusted data, not new authority.
    # Host-to-Agent tool results retain their separate identity scrub/check.
    if digest is not None and digest != actual:
        raise AgentProtocolError("Native checkpoint digest mismatched")
    return canonical, actual, encoded


def _assert_native_decoded_json(value: Any) -> None:
    pending = [value]
    while pending:
        item = pending.pop()
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("native decoded object keys must be strings")
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
        elif item is not None and type(item) not in {str, bool, int, float}:
            raise ValueError("native decoded tree must contain JSON values")


async def native_checkpoint_boundary(operation: Any) -> Any:
    """Drain already-received checkpoint work before propagating cancellation.

    A cancelled to_thread await does not stop its worker. Keeping the complete
    validation/persistence boundary joined prevents late writes and subsequent
    frames from overtaking this completed public prefix. No tool is dispatched.
    """
    task = asyncio.ensure_future(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        # Retrieve worker failure without replacing cancellation or exposing data.
        if task.done():
            try:
                task.result()
            except BaseException:
                pass
        raise


def is_native_failure_field(key: str, value: Any) -> bool:
    """Recognize explicit protocol slots, never arbitrary diagnostic field names."""
    return (key == "nativeFailure" or key in {"error", "failure"}
            and isinstance(value, Mapping) and "schema" in value)


def scrub_native_tool_result(value: Any) -> Any:
    """Remove transport identity, not typed LD ports or JSON Pointer paths.

    Native decoded LD is file data, not a transport/credential envelope. Its
    field names (including user instruction parameters) must remain intact.
    """
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise AgentProtocolError("Native result keys must be text")
            name = _normalized_agent_field_name(key)
            if (name in _FORBIDDEN_AGENT_PAYLOAD_FIELDS
                    or name.endswith(("hwnd", "pid"))
                    or name in {"handle", "windowhandle", "processid", "mdiclienthwnds"}
                    or name in (_REDACTED_AGENT_RESULT_FIELDS - {"port", "timeoutseconds"})
                    or (name.endswith("path") and isinstance(item, str)
                        and not item.startswith("/"))):
                continue
            if is_native_failure_field(key, item):
                # Public diagnostics have their own versioned contract; their
                # field names are evidence, never a transport capability.
                result[key] = validate_native_failure(item)
            elif key == "decoded" and isinstance(item, Mapping):
                result[key] = json.loads(json.dumps(item, ensure_ascii=False, allow_nan=False))
            else:
                result[key] = scrub_native_tool_result(item)
        return result
    if isinstance(value, (list, tuple)):
        return [scrub_native_tool_result(item) for item in value]
    return value


def assert_native_public_payload(value: Any) -> None:
    """Check native envelopes without treating decoded LD user fields as identity."""
    def envelope(item: Any) -> Any:
        if isinstance(item, Mapping):
            result = {}
            for key, nested in item.items():
                if is_native_failure_field(key, nested):
                    validate_native_failure(nested)
                    result[key] = {}
                elif key in {"decoded", "native_structure"} and isinstance(nested, Mapping):
                    result[key] = {}
                else:
                    result[key] = envelope(nested)
            return result
        if isinstance(item, (list, tuple)):
            return [envelope(nested) for nested in item]
        return item

    assert_agent_safe_payload(envelope(value))


def validated_transcript_checkpoint(
    checkpoint: Mapping[str, Any], digest: str | None = None,
) -> tuple[dict[str, Any], str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate historical, fully settled messages; this grants no authority.

    SDK request envelopes may merge on continuation. Monotonic persistence
    therefore compares exact tool parts and user prompts, not message counts.
    """
    if set(checkpoint) != {"schemaVersion", "messages"} or checkpoint.get("schemaVersion") != "1":
        raise AgentProtocolError("Agent transcript checkpoint shape is invalid")
    _assert_finite_protocol_numbers(checkpoint)
    try:
        encoded = json.dumps(checkpoint, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        canonical = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise AgentProtocolError("Agent transcript checkpoint is not strict JSON") from exc
    actual_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    if digest is not None and digest != actual_digest:
        raise AgentProtocolError("Agent transcript checkpoint digest mismatched")
    messages = canonical["messages"]
    if not isinstance(messages, list) or not messages:
        raise AgentProtocolError("Agent transcript messages are invalid")
    pending: dict[str, str] = {}
    tool_parts: list[dict[str, Any]] = []
    user_parts: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("kind") not in {"request", "response"}:
            raise AgentProtocolError("Agent transcript message is invalid")
        parts = message.get("parts")
        if not isinstance(parts, list):
            raise AgentProtocolError("Agent transcript message parts are invalid")
        if pending and message["kind"] != "request":
            raise AgentProtocolError("Agent transcript has an unsettled tool exchange")
        for part in parts:
            if not isinstance(part, dict) or not isinstance(part.get("part_kind"), str):
                raise AgentProtocolError("Agent transcript part is invalid")
            kind = part["part_kind"]
            if kind == "thinking":
                raise AgentProtocolError("Agent transcript contains private thinking")
            if kind == "user-prompt":
                user_parts.append(part)
            if kind == "tool-call":
                call_id, name = part.get("tool_call_id"), part.get("tool_name")
                if message["kind"] != "response" or not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name or call_id in pending:
                    raise AgentProtocolError("Agent transcript tool call is invalid")
                pending[call_id] = name
                tool_parts.append(part)
            elif kind == "tool-return" or (kind == "retry-prompt" and part.get("tool_call_id")):
                call_id = part.get("tool_call_id")
                if message["kind"] != "request" or call_id not in pending or (
                    part.get("tool_name") != pending[call_id]
                    and not (kind == "retry-prompt" and part.get("tool_name") is None)
                ):
                    raise AgentProtocolError("Agent transcript tool result is unpaired")
                del pending[call_id]
                tool_parts.append(part)
            elif kind == "retry-prompt" and part.get("tool_name") is not None:
                raise AgentProtocolError("Agent transcript retry is unpaired")
    if pending:
        raise AgentProtocolError("Agent transcript has unfinished tool calls")
    return canonical, actual_digest, tool_parts, user_parts


class AgentProtocolError(RuntimeError):
    """The sidecar sent an invalid, unsafe, or incompatible protocol frame."""


class AgentProtocolMessage(BaseModel):
    """Strict outer envelope for a single newline-delimited JSON frame."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: str = Field(min_length=1, max_length=32)
    type: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


_FORBIDDEN_AGENT_PAYLOAD_FIELDS = frozenset(
    {
        "authorizationsecret",
        "approvalsecret",
        "approvaltoken",
        "sessionid",
        "pid",
        "hwnd",
        "projectpath",
        "projecthash",
        "engineeringprocessid",
        "engineeringwindowhandle",
        "controllerendpoint",
    }
)

_REDACTED_AGENT_RESULT_FIELDS = _FORBIDDEN_AGENT_PAYLOAD_FIELDS | frozenset(
    {
        # Tool results never carry model or service credentials.  Normalize
        # spelling first so snake/camel/kebab variants share one policy.
        "apikey",
        "accesstoken",
        "authtoken",
        "authorization",
        "authorizationheader",
        "authorizationtoken",
        "cookie",
        "csrftoken",
        "credential",
        "credentials",
        "customheaders",
        "password",
        "passwd",
        "privatekey",
        "proxyauthorization",
        "refreshtoken",
        "secret",
        "secretkey",
        "setcookie",
        "signingkey",
        "sessiontoken",
        "token",
        # Controller observation is Host-owned too.  The Agent receives the
        # observation payload, never the connected endpoint selected by the UI.
        "endpoint",
        "host",
        "port",
        "timeoutseconds",
        # MCP analysis artifacts stay Host-owned. The model receives their
        # structured facts and evidence receipt, never local artifact paths.
        "payloadpath",
        "markdownpath",
        "receiptpath",
    }
)


def _normalized_agent_field_name(value: str) -> str:
    """Collapse snake/camel/kebab spelling before applying boundary policy."""

    return "".join(character for character in value.casefold() if character.isalnum())


def _is_redacted_agent_result_field(value: str) -> bool:
    normalized = _normalized_agent_field_name(value)
    return (
        normalized in _REDACTED_AGENT_RESULT_FIELDS
        or normalized.endswith(
            (
                "apikey",
                "accesstoken",
                "authtoken",
                "authorizationtoken",
                "bearertoken",
                "credential",
                "credentials",
                "csrftoken",
                "idtoken",
                "password",
                "passwd",
                "refreshtoken",
                "secret",
                "sessiontoken",
            )
        )
        or normalized == "path"
        or normalized.endswith("path")
        or normalized.endswith("directory")
    )


def _assert_finite_protocol_numbers(value: Any) -> None:
    """Reject JSON extensions that would otherwise be rewritten as null."""

    if isinstance(value, float):
        if not math.isfinite(value):
            raise AgentProtocolError("Agent protocol payload is not strict JSON")
        return
    if isinstance(value, Mapping):
        for nested in value.values():
            _assert_finite_protocol_numbers(nested)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for nested in value:
            _assert_finite_protocol_numbers(nested)


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def encode_message(message_type: str, payload: Mapping[str, Any] | None = None) -> bytes:
    """Encode exactly one protocol frame, suitable for a binary stdin/stdout pipe."""

    raw_payload = dict(payload or {})
    _assert_finite_protocol_numbers(raw_payload)
    message = AgentProtocolMessage(
        protocol_version=AGENT_PROTOCOL_VERSION_V1,
        type=message_type,
        payload=raw_payload,
    )
    encoded = json.dumps(
        message.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    framed = encoded + b"\n"
    if len(framed) > MAX_PROTOCOL_LINE_BYTES:
        raise AgentProtocolError("Agent protocol message exceeds the allowed size")
    return framed


def decode_message(line: bytes) -> AgentProtocolMessage:
    """Decode and version-check one sidecar stdout or Host stdin frame."""

    if not line or len(line) > MAX_PROTOCOL_LINE_BYTES:
        raise AgentProtocolError("Agent protocol line is missing or too large")
    try:
        raw = json.loads(
            line.decode("utf-8"),
            parse_constant=_reject_non_finite_json_constant,
        )
        message = AgentProtocolMessage.model_validate(raw)
    except (UnicodeDecodeError, ValueError, ValidationError) as exc:
        raise AgentProtocolError("Agent protocol frame is invalid") from exc
    if message.protocol_version != AGENT_PROTOCOL_VERSION_V1:
        raise AgentProtocolError("Agent protocol version is not supported")
    return message


def parse_manifest(payload: Mapping[str, Any]) -> AgentManifestV1:
    try:
        manifest = AgentManifestV1.model_validate(payload)
    except ValidationError as exc:
        raise AgentProtocolError("Agent manifest is invalid") from exc
    if manifest.protocol_version != AGENT_PROTOCOL_VERSION_V1:
        raise AgentProtocolError("Agent manifest version is not supported")
    return manifest


def assert_agent_safe_payload(value: Any) -> None:
    """Reject target identity and approval material before it reaches the child.

    This guards the explicit protocol boundary; it is not a replacement for
    the Host's MCP-side injection and stale-value checks.
    """

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if (
                    isinstance(key, str)
                    and _normalized_agent_field_name(key)
                    in _FORBIDDEN_AGENT_PAYLOAD_FIELDS
                ):
                    raise AgentProtocolError(
                        "Host attempted to send protected target or approval data to Agent"
                    )
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)

    visit(value)


def scrub_agent_tool_result(value: Any) -> Any:
    """Remove Host-owned target and endpoint fields from a tool response.

    This is applied immediately before Host sends an external-tool result to
    the sidecar.  It complements (rather than replaces) schema removal on the
    request path, since a vendor read result may echo injected arguments.
    """

    if isinstance(value, Mapping):
        return {
            key: scrub_agent_tool_result(item)
            for key, item in value.items()
            if not isinstance(key, str)
            or not _is_redacted_agent_result_field(key)
        }
    if isinstance(value, list):
        return [scrub_agent_tool_result(item) for item in value]
    if isinstance(value, tuple):
        return [scrub_agent_tool_result(item) for item in value]
    return value
