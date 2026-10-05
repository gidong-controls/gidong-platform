"""Conversation-local working plans, independent of executable draft revisions."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    task: str = Field(min_length=1, max_length=600)
    status: Literal["pending", "in_progress", "done"]


class WorkPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    objective: str = Field(min_length=1, max_length=1000)
    items: list[WorkItem] = Field(max_length=24)
    decisions: str = Field(default="", max_length=2000)
    next_action: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def unique_items(self) -> WorkPlan:
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("work item ids must be unique")
        return self


class WorkPlanConflict(ValueError):
    pass


class PlanningWorkMemory:
    """Append-only model work notes; no PLC source or execution status is changed."""

    def __init__(self, database_path: Path) -> None:
        self._path = database_path
        with closing(sqlite3.connect(self._path)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS planning_work_memory ("
                "conversation_id TEXT NOT NULL, version INTEGER NOT NULL, "
                "run_id TEXT NOT NULL, plan_json TEXT NOT NULL, "
                "PRIMARY KEY(conversation_id, version))"
            )

    @staticmethod
    def _read(connection: sqlite3.Connection, conversation_id: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT version, plan_json FROM planning_work_memory "
            "WHERE conversation_id=? ORDER BY version DESC LIMIT 1", (conversation_id,),
        ).fetchone()
        return {
            "version": row[0] if row else 0,
            "plan": json.loads(row[1]) if row else None,
            "basis": "model_work_plan",
        }

    def read(self, conversation_id: str) -> dict[str, Any]:
        with closing(sqlite3.connect(self._path)) as connection:
            return self._read(connection, conversation_id)

    def update(
        self, *, conversation_id: str, run_id: str,
        expected_version: int, plan: Any,
    ) -> dict[str, Any]:
        if type(expected_version) is not int or expected_version < 0:
            raise WorkPlanConflict("expected_version must be a non-negative integer")
        encoded = json.dumps(
            WorkPlan.model_validate(plan).model_dump(mode="json"),
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        if len(encoded.encode("utf-8")) > 32_768:
            raise WorkPlanConflict("work plan exceeds 32768 bytes")
        with closing(sqlite3.connect(self._path, timeout=30)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._read(connection, conversation_id)
            if current["version"] != expected_version:
                raise WorkPlanConflict("work plan version changed; inspect the current plan")
            if current["plan"] == json.loads(encoded):
                return current
            connection.execute(
                "INSERT INTO planning_work_memory "
                "(conversation_id, version, run_id, plan_json) VALUES (?, ?, ?, ?)",
                (conversation_id, expected_version + 1, run_id, encoded),
            )
            return self._read(connection, conversation_id)
