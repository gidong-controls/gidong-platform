"""Lossless checkpoint transfer independent of the model context window."""

import hashlib
import json
from typing import Any, Iterator


CHUNK_CHARACTERS = 16_384


def checkpoint_chunks(value: dict[str, Any]) -> Iterator[dict[str, Any]]:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    for index, offset in enumerate(range(0, len(text), CHUNK_CHARACTERS)):
        yield {"checkpointDigest": digest, "index": index,
               "text": text[offset:offset + CHUNK_CHARACTERS]}


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

    def finish(self, digest: str) -> dict[str, Any]:
        text = "".join(self._parts)
        self._parts.clear()
        expected, self._digest = self._digest, None
        if digest != expected or hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
            raise ValueError("checkpoint transfer digest differs")
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("checkpoint transfer must contain an object")
        return value
