"""Native compiler diagnostics carried from the checked candidate to its agent."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ProgramCheckFailureObservationV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["controlstudio-program-check-failure/v1"] = Field(
        alias="schemaVersion",
    )
    messages: list[Annotated[str, Field(strict=True, max_length=16_384)]] = Field(
        min_length=1, max_length=200,
    )
