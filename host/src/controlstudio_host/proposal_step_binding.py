"""Host-owned, durable association between a proposal and its originating step.

This association is bookkeeping, never approval or native execution authority.
Legacy proposals without it remain readable but cannot guess a retry step.
"""

from __future__ import annotations

import re
from typing import Any

_EVENT = "mission.change_proposal_bound"
_SCHEMA = "controlstudio-proposal-step-binding/v1"


def _step_id(value: object) -> str | None:
    return value if isinstance(value, str) and value and value.strip() == value else None


def validate_proposal_step(snapshot: object, step_id: object) -> str:
    """Check the exact current checkpoint step before creating a proposal."""
    if (
        not isinstance(snapshot, dict)
        or not isinstance(snapshot.get("steps"), list)
        or _step_id(step_id) is None
    ):
        raise ValueError("proposal requires a checkpointed active mission step")
    matches = [
        step for step in snapshot.get("steps", [])
        if isinstance(step, dict) and step.get("stepId") == step_id
    ]
    if (
        snapshot.get("status") != "active"
        or snapshot.get("activeStepId") != step_id
        or len(matches) != 1
        or matches[0].get("status") != "active"
        or matches[0].get("outcome") is not None
    ):
        raise ValueError("proposal step differs from the checkpointed active step")
    return str(step_id)


def _identity(transaction: Any) -> dict[str, str]:
    identity = {
        "schemaVersion": _SCHEMA,
        "conversationId": transaction.conversation_id,
        "runId": transaction.run_id,
        "transactionId": transaction.id,
        "planDigest": transaction.plan_digest,
    }
    if any(not isinstance(value, str) or not value for value in identity.values()):
        raise ValueError("proposal binding requires a durable transaction identity")
    if re.fullmatch(r"[0-9a-f]{64}", identity["planDigest"]) is None:
        raise ValueError("proposal binding has an invalid plan digest")
    return identity


def _stored_binding(store: Any, identity: dict[str, str]) -> str | None:
    cursor = 0
    bound: str | None = None
    while True:
        events = store.list_events(
            conversation_id=identity["conversationId"], after_sequence=cursor, limit=250,
        )
        if not events:
            return bound
        for event in events:
            if event.sequence <= cursor:
                raise ValueError("proposal binding event cursor did not advance")
            cursor = event.sequence
            if event.event_type != _EVENT:
                continue
            payload = event.payload
            if payload.get("transactionId") != identity["transactionId"]:
                continue
            step_id = _step_id(payload.get("stepId"))
            if (
                step_id is None
                or payload != {**identity, "stepId": step_id}
                or event.conversation_id != identity["conversationId"]
                or event.run_id != identity["runId"]
                or (bound is not None and bound != step_id)
            ):
                raise ValueError("proposal step identity was rebound")
            bound = step_id


def record_proposal_step_binding(store: Any, transaction: Any, step_id: str) -> None:
    identity = _identity(transaction)
    if _step_id(step_id) is None:
        raise ValueError("proposal binding step is invalid")
    existing = _stored_binding(store, identity)
    if existing is not None:
        if existing != step_id:
            raise ValueError("proposal step identity was rebound")
        return
    store.append_event(
        event_type=_EVENT,
        conversation_id=identity["conversationId"],
        run_id=identity["runId"],
        payload={**identity, "stepId": step_id},
    )


def bound_proposal_step_id(store: Any, transaction: Any, mission_snapshot: object) -> str | None:
    try:
        step_id = _stored_binding(store, _identity(transaction))
    except (AttributeError, TypeError, ValueError):
        return None
    if (
        step_id is None
        or not isinstance(mission_snapshot, dict)
        or not isinstance(mission_snapshot.get("steps"), list)
    ):
        return None
    matches = [
        step for step in mission_snapshot.get("steps", [])
        if isinstance(step, dict) and step.get("stepId") == step_id
    ]
    if len(matches) != 1 or matches[0].get("outcome") is not None:
        return None
    if (
        mission_snapshot.get("status") == "waiting_for_approval"
        and mission_snapshot.get("activeStepId") is None
        and matches[0].get("status") == "pending"
    ) or (
        mission_snapshot.get("status") == "active"
        and mission_snapshot.get("activeStepId") == step_id
        and matches[0].get("status") == "active"
    ):
        return step_id
    return None
