from __future__ import annotations

import os
import secrets
from contextlib import suppress
from pathlib import Path
from typing import Protocol

from .constants import PRODUCT_DATA_DIRECTORY_NAME


class LaunchTokenStore(Protocol):
    def load(self) -> str | None: ...

    def rotate(self) -> str: ...


def default_token_path(environ: dict[str, str] | None = None) -> Path:
    values = os.environ if environ is None else environ
    local_app_data = values.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / PRODUCT_DATA_DIRECTORY_NAME / "session.token"
    return (
        Path.home()
        / ".config"
        / PRODUCT_DATA_DIRECTORY_NAME.casefold().replace(" ", "-")
        / "session.token"
    )


class FileLaunchTokenStore:
    """Persist the current user's Host launch secret outside browser storage."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_token_path()

    def load(self) -> str | None:
        try:
            token = self._path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except OSError:
            return None
        return token if _valid_token(token) else None

    def rotate(self) -> str:
        token = secrets.token_urlsafe(48)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._path.with_suffix(".token.tmp")
        temporary_path.write_text(token, encoding="utf-8")
        with suppress(OSError):
            temporary_path.chmod(0o600)
        temporary_path.replace(self._path)
        return token


def _valid_token(token: str) -> bool:
    return 32 <= len(token) <= 512 and all(
        not character.isspace() and ord(character) >= 33 for character in token
    )
