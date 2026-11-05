"""Read-only waiting owned by one already-started workspace recovery.

No dispatch, modal response, binding admission, or historical outcome settlement
is performed here. The caller owns bounded observations and exact scope checks.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


class RecoveryWaitScopeChanged(ValueError):
    """The original operation no longer owns this read-only wait."""


class OwnedLaunchMaterialChange(ValueError):
    def __init__(self, observation: dict[str, Any]):
        super().__init__("Original launch requires owned recovery observation")
        self.observation = copy.deepcopy(observation)


def owned_launch_wait_requires_park(state: Any) -> bool:
    """Only an explicitly terminal owned launch releases its target lease."""
    return isinstance(state, dict) and state.get("state") not in {"ready", "original_retired", "not_started"}


@dataclass(frozen=True)
class RecoveryMaterialChange:
    kind: str
    observation: dict[str, Any] | None = None


async def wait_for_owned_launch_binding(
    identity: dict[str, Any], *,
    validate_scope: Callable[[], Awaitable[dict[str, Any]]],
    observe_binding: Callable[[], Awaitable[dict[str, Any]]],
    record_pending: Callable[[dict[str, Any]], None],
    request_disconnected: Callable[[], Awaitable[bool]],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    """Join one original launch; each supplied read owns its bounded lifetime."""
    expected = copy.deepcopy(identity)
    while True:
        if await request_disconnected():
            raise asyncio.CancelledError
        if await validate_scope() != expected:
            raise RecoveryWaitScopeChanged("Owned launch binding wait scope changed")
        result = await observe_binding()
        if await request_disconnected():
            raise asyncio.CancelledError
        if await validate_scope() != expected:
            raise RecoveryWaitScopeChanged("Owned launch binding wait scope changed")
        if not isinstance(result, dict):
            raise ValueError("Owned launch binding observation is invalid")
        if result.get("schema") != "controlstudio-owned-launch-binding-wait/v1":
            # The common Open caller still owns all existing verified receipt,
            # attach and native admission checks. This loop grants no authority.
            return result
        if (result.get("owner_ref") != expected["owner_ref"] or result.get("scope") != expected["scope"]
                or result.get("verified") is not False or result.get("native_binding_available") is not False
                or result.get("automatic_replay") is not False):
            raise RecoveryWaitScopeChanged("Owned launch binding observation changed owner")
        record_pending(result)
        if result.get("state") in {"owned_modal", "original_retired", "launch_unresolved"}:
            raise OwnedLaunchMaterialChange(result)
        if result.get("state") not in {"launch_pending", "binding_pending"}:
            raise ValueError("Owned launch binding observation is invalid")
        await sleep(1.0)


def response_input_unknown(value: dict[str, Any]) -> bool:
    """Current response effect only; failure alone is not unknown input.

    An explicit false may describe a rejected input. Missing effect information
    on a failed response remains conservative. Never inspect historical data.
    """
    flags = []
    def visit(node):
        if not isinstance(node, dict):
            return
        flags.extend(node[key] for key in ('input_state_unknown', 'input_queue_state_unknown')
                     if key in node)
        for key in ('error', 'receipt', 'failure', 'nativeFailure'):
            visit(node.get(key))
        if node.get('schema') == 'controlstudio-native-failure/v1':
            visit(node.get('details'))
    visit(value)
    if flags:
        return any(flag is not False for flag in flags)
    return value.get('ok') is not True or bool(value.get('error') or value.get('failure'))


def completed_owned_cancel(value: dict[str, Any], ui: dict[str, Any]) -> bool:
    response = value.get('owned_ui_response', {})
    lifecycle = value.get('target_lifecycle', {})
    return (isinstance(response,dict) and isinstance(lifecycle,dict)
            and value.get('ok') is True and not value.get('error') and not value.get('failure')
            and not response_input_unknown(value) and value.get('action_attempted') is True
            and value.get('target_closed') is True and lifecycle.get('post_observation_verified') is True
            and lifecycle.get('target_closed') is True and ui.get('ui_kind') == 'modal'
            and response == {'ui_ref': ui.get('ui_ref'), 'ui_kind': 'modal', 'requested_response': 'cancel'})


def restored_cancel_receipt(records, ui: dict[str, Any], scope: dict[str, Any]):
    issued=ui.get('pending_recovery_identity',{})
    if any(issued.get(key)!=value for key,value in scope.items()):
        return None
    for entry in records:
        receipt=entry['receipt']
        value=receipt.get('result')
        if not isinstance(value,dict):
            return None
        continuation=value.get('current_workspace_continuation',{})
        current=value.get('current_workspace_recovery',{})
        same_operation=(isinstance(continuation,dict) and continuation.get('operation_id')==scope['operation_id']
            and continuation.get('phase')=='close_pending'
            and continuation.get('provenance')=='host_current_recovery_continuation/v1'
            and continuation.get('native_input_authority') is False)
        same_pending=(isinstance(current,dict) and current.get('operation_id')==scope['operation_id']
            and current.get('phase')=='close_pending' and current.get('recovery_only') is True
            and current.get('provenance')=='host_current_recovery_state/v1')
        if receipt.get('tool_name')=='recover_workspace' and receipt.get('arguments')=={}:
            if completed_owned_cancel(value,ui):
                if same_operation:
                    digest=hashlib.sha256(json.dumps(receipt,ensure_ascii=False,sort_keys=True,
                        separators=(',',':'),allow_nan=False).encode()).hexdigest()
                    return value,{'key':entry['key'],'sha256':digest}
                continue  # The same completed response may also have a separate raw record.
            if (same_operation or same_pending) and value.get('current_request_input_attempted') is False:
                continue
        if (receipt.get('tool_name')=='native_editor' and receipt.get('arguments',{}).get('action')=='observe'
                and value.get('read_only') is True and value.get('action_attempted') is False):
            continue
        return None
    return None


def consume_close_reconsideration(state: dict[str, Any], scope: dict[str, Any]) -> bool:
    proof = state.get('close_reconsideration')
    if (not isinstance(proof, dict) or proof.get('scope') != scope
            or proof.get('consumed') is not False
            or not isinstance(proof.get('response_result'),dict) or not isinstance(proof.get('ui_identity'),dict)
            or not completed_owned_cancel(proof['response_result'],proof['ui_identity'])):
        return False
    proof['consumed'] = True
    return True


def undelivered_close_reservation(state: dict[str, Any], scope: dict[str, Any]):
    proof=state.get('close_reconsideration')
    history=state.get('close_attempt_history',[])
    if (state.get('phase')!='close_pending' or not isinstance(proof,dict)
            or proof.get('scope')!=scope or proof.get('consumed') is not True
            or not completed_owned_cancel(proof.get('response_result',{}),proof.get('ui_identity',{}))
            or not history or history[-1].get('reconsideration',{}).get('scope')!=scope
            or history[-1]['reconsideration'].get('ui_identity')!=proof.get('ui_identity')):
        return None
    failure=state.get('last_failure',{}).get('failure',{})
    details=failure.get('details',{})
    if (details.get('phase')!='close_dispatch' or details.get('hwnd')!=scope['binding']['hwnd']
            or details.get('message')!=16 or details.get('wparam')!=0 or details.get('lparam')!=0):
        return None
    legacy=(failure.get('kind')=='XG5000_WIN32_POST_FAILED' and details.get('win32_error')==1816)
    notified=(failure.get('kind')=='XG5000_WIN32_NOTIFY_CLOSE_FAILED'
        and details.get('api')=='SendNotifyMessageW' and details.get('dispatch_state')=='not_delivered'
        and details.get('input_state_unknown') is False and details.get('handler_execution_verified') is False)
    if not (legacy or notified):
        return None
    digest=hashlib.sha256(json.dumps(state['last_failure'],ensure_ascii=False,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return None if digest in proof.get('released_failure_digests',[]) else digest


async def wait_for_recovery_change(
    identity: dict[str, Any], *,
    validate_scope: Callable[[], Awaitable[dict[str, Any]]],
    observe_change: Callable[[], Awaitable[RecoveryMaterialChange | None]],
    interval_seconds: float = 1.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> RecoveryMaterialChange:
    """Await material evidence, without provider calls or native input.

    Each supplied observation must have its own bounded lifetime. Cancellation
    propagates through the idle wait immediately; no shield surrounds this loop.
    A query error propagates, and is never interpreted as process retirement.
    """
    if type(interval_seconds) not in {int, float} or not 0 < interval_seconds <= 30:
        raise ValueError('Recovery observation interval is invalid')
    expected = copy.deepcopy(identity)
    while True:
        if await validate_scope() != expected:
            raise RecoveryWaitScopeChanged('Pending recovery identity changed before observation')
        change = await observe_change()
        if await validate_scope() != expected:
            raise RecoveryWaitScopeChanged('Pending recovery identity changed during observation')
        if change is not None:
            if not isinstance(change, RecoveryMaterialChange) or change.kind not in {
                    'owned_modal', 'original_retired', 'owned_modal_completed'}:
                raise ValueError('Recovery observation is not a material change')
            return change
        await sleep(interval_seconds)


def recovery_continuation(identity: dict[str, Any], *, state: str,
                          route_tool_name: str | None = None) -> dict[str, Any]:
    from controlstudio_contracts.native_operation_continuation import validate_recovery_continuation
    return validate_recovery_continuation({'schema_version': '1', 'provenance': 'host_current_recovery_continuation/v1',
        'operation_id': identity['operation_id'], 'phase': identity['phase'],
        'state': state, 'route_tool_name': route_tool_name,
        'native_input_authority': False})
