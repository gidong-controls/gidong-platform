"""Work item statuses shared with Agent protocol diagnostics."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    task: str = Field(min_length=1, max_length=600)
    status: Literal["pending", "in_progress", "done"]
