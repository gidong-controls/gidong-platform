from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from controlstudio_contracts import (
    ProgramCheckFailureObservationV1,
)

from .models import ChangeTransaction, ChangeTransactionState

TERMINAL_CHANGE_OUTCOME_DISPOSITIONS = {
    ChangeTransactionState.COMPLETED: "applied",
    ChangeTransactionState.ROLLED_BACK: "rolled_back",
    ChangeTransactionState.FAILED: "failed",
    ChangeTransactionState.REJECTED: "rejected",
    ChangeTransactionState.EXPIRED: "expired",
}


def _digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def change_outcome_projection(transaction: ChangeTransaction) -> dict[str, Any]:
    """Project one terminal durable change into Agent-safe, digest-bound data."""

    disposition = TERMINAL_CHANGE_OUTCOME_DISPOSITIONS.get(transaction.state)
    if disposition is None:
        raise ValueError("change transaction has no terminal Agent outcome")
    unsigned: dict[str, Any] = {
        "schemaVersion": "1",
        "transactionId": transaction.id,
        "planDigest": transaction.plan_digest,
        "state": transaction.state.value,
        "disposition": disposition,
        "verificationEvidenceDigest": (
            _digest(transaction.result) if transaction.result else None
        ),
        "errorCode": transaction.error_code,
    }
    if transaction.state is ChangeTransactionState.ROLLED_BACK:
        verification = transaction.result.get("verification")
        rejection = verification.get("native_rejection") if isinstance(verification, dict) else None
        if isinstance(rejection, dict) and rejection.get("code") == "PROGRAM_CHECK_FAILED":
            diagnostic = rejection.get("programCheckFailure")
            if diagnostic is not None:
                observation = ProgramCheckFailureObservationV1.model_validate(diagnostic)
                unsigned["programCheckFailure"] = observation.model_dump(mode="json", by_alias=True)
    outcome_digest = _digest(unsigned)
    return {
        **unsigned,
        "evidenceId": str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"controlstudio:change-outcome:{transaction.id}:{outcome_digest}",
            )
        ),
        "outcomeDigest": outcome_digest,
    }


def change_outcome_evidence(projection: dict[str, Any]) -> dict[str, Any]:
    receipt_core = {
        "schema": "controlstudio-change-outcome-evidence/v1",
        **projection,
    }
    return {
        "evidenceId": projection["evidenceId"],
        "title": "승인된 변경 실행 결과",
        "kind": "change_outcome",
        "toolName": "host.change_outcome",
        "summary": (
            f"변경 {projection['transactionId']} 결과: "
            f"{projection['disposition']}"
        ),
        "confidence": "confirmed",
        "sourceAuthority": "host_durable_change_transaction",
        "sourceStatus": "verified_terminal",
        "receipt": {
            **receipt_core,
            "receiptDigest": _digest(receipt_core),
        },
    }
