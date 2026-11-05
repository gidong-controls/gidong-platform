"""Best-effort, durable observations for the CA-01 live-evidence capture.

The launcher cannot see Windows' secure UAC desktop.  In particular, a
``ShellExecuteW(..., \"runas\", ...)`` success only proves that Windows accepted
the elevation handoff; it does *not* prove that an operator saw or approved a
specific prompt.  This module consequently records only facts the launcher
can observe and leaves the visible-prompt count to the live test observer.
"""

from __future__ import annotations

import json
import os
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .constants import PRODUCT_DATA_DIRECTORY_NAME

SCHEMA = "controlstudio-launcher-elevation-observation/v1"
_FILE_NAME = "elevation-observations.v1.json"
_MAX_OBSERVATIONS = 64


def default_elevation_receipt_path(
    environ: dict[str, str] | None = None,
) -> Path:
    values = os.environ if environ is None else environ
    local_app_data = values.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / PRODUCT_DATA_DIRECTORY_NAME / _FILE_NAME
    return (
        Path.home()
        / ".config"
        / PRODUCT_DATA_DIRECTORY_NAME.casefold().replace(" ", "-")
        / _FILE_NAME
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class ElevationReceiptStore:
    """Append launcher-observed elevation lifecycle results without blocking use.

    Receipt persistence is diagnostic/evidence support.  It must never make a
    working launcher fail because its local evidence directory is unavailable.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = (path or default_elevation_receipt_path()).resolve()

    @property
    def path(self) -> Path:
        return self._path

    def begin(
        self,
        *,
        initial_host_state: str,
        initial_host_verified: bool,
        role: str,
    ) -> dict[str, Any]:
        return {
            "observation_id": str(uuid4()),
            "started_at": _utc_now(),
            "initial_host_state": initial_host_state,
            "initial_host_verified": initial_host_verified,
            "launcher_role": role,
            # This becomes true only when the elevation broker accepted a
            # RunAs handoff. It deliberately does not mean a visible UAC
            # prompt was counted or approved.
            "elevation_relaunch_requested": False,
            "host_ready": False,
            "elevated_host_retention_verified": False,
        }

    def finish(
        self,
        observation: dict[str, Any],
        *,
        elevation_relaunch_requested: bool,
        host_ready: bool,
        elevated_host_retention_verified: bool,
        outcome: str,
    ) -> None:
        record = dict(observation)
        record.update(
            {
                "elevation_relaunch_requested": elevation_relaunch_requested,
                "host_ready": host_ready,
                "elevated_host_retention_verified": elevated_host_retention_verified,
                "outcome": outcome,
                "finished_at": _utc_now(),
            }
        )
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            observations = self._load()
            observations.append(record)
            document = {
                "schema": SCHEMA,
                "updated_at": _utc_now(),
                "observations": observations[-_MAX_OBSERVATIONS:],
            }
            temporary = self._path.with_name(f".{self._path.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_text(
                    json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                os.replace(temporary, self._path)
            finally:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
        except OSError:
            # The launcher has already performed (or refused) the operation.
            # Losing an optional local observation must not affect it.
            return

    def _load(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("schema") != SCHEMA or not isinstance(raw.get("observations"), list):
                return []
            return [item for item in raw["observations"] if isinstance(item, dict)]
        except (OSError, ValueError, json.JSONDecodeError):
            return []
