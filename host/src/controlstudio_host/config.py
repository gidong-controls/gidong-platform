from __future__ import annotations

import ipaddress
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _default_data_dir() -> Path:
    if local_app_data := os.environ.get("LOCALAPPDATA"):
        return Path(local_app_data) / "ControlStudio"
    return Path.home() / ".controlstudio"


def _default_static_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "web" / "dist"


@dataclass(frozen=True)
class HostConfig:
    """Configuration for the local, interactive-user ControlStudio Host.

    The first product milestone deliberately accepts loopback binds only.
    Remote access belongs behind a separately authenticated HTTPS gateway.
    """

    bind_host: str = "127.0.0.1"
    port: int = 8765
    database_path: Path = field(
        default_factory=lambda: _default_data_dir() / "controlstudio.sqlite3"
    )
    knowledge_database_path: Path | None = None
    static_dir: Path = field(default_factory=_default_static_dir)
    canonical_origin: str | None = None
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "[::1]")
    event_poll_interval_seconds: float = 0.25
    event_heartbeat_seconds: float = 15.0
    launch_token: str = field(default_factory=lambda: secrets.token_urlsafe(48))
    # These switches exist only for isolated fake-backend/validation harnesses.
    # They are deliberately not configurable through environment variables, so
    # a normal packaged Host cannot expose an unverified product capability.
    validation_enable_native_project_create: bool = False
    validation_enable_online_observe: bool = False

    def __post_init__(self) -> None:
        try:
            address = ipaddress.ip_address(self.bind_host)
        except ValueError as exc:
            raise ValueError("bind_host must be a loopback IP address") from exc
        if not address.is_loopback:
            raise ValueError("ControlStudio Host may only bind to loopback")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if self.event_poll_interval_seconds <= 0:
            raise ValueError("event_poll_interval_seconds must be positive")
        if self.event_heartbeat_seconds <= 0:
            raise ValueError("event_heartbeat_seconds must be positive")
        if len(self.launch_token) < 32 or len(self.launch_token) > 512:
            raise ValueError("launch_token must contain between 32 and 512 characters")
        if any(character.isspace() or ord(character) < 33 for character in self.launch_token):
            raise ValueError("launch_token contains an invalid character")

        canonical_origin = self.canonical_origin or f"http://localhost:{self.port}"
        if not canonical_origin.startswith(("http://localhost:", "http://127.0.0.1:")):
            raise ValueError("canonical_origin must use a loopback HTTP origin")
        object.__setattr__(self, "canonical_origin", canonical_origin.rstrip("/"))
        object.__setattr__(
            self,
            "database_path",
            Path(self.database_path).expanduser().resolve(),
        )
        knowledge_database_path = self.knowledge_database_path
        if knowledge_database_path is None:
            database_path = Path(self.database_path)
            knowledge_database_path = database_path.with_name(
                f"{database_path.stem}-knowledge.sqlite3"
            )
        object.__setattr__(
            self,
            "knowledge_database_path",
            Path(knowledge_database_path).expanduser().resolve(),
        )
        object.__setattr__(
            self,
            "static_dir",
            Path(self.static_dir).expanduser().resolve(),
        )

    @classmethod
    def from_env(cls) -> HostConfig:
        bind_host = os.environ.get("CONTROLSTUDIO_BIND_HOST", "127.0.0.1")
        port = int(os.environ.get("CONTROLSTUDIO_PORT", "8765"))
        database_path = Path(
            os.environ.get(
                "CONTROLSTUDIO_DATABASE",
                str(_default_data_dir() / "controlstudio.sqlite3"),
            )
        )
        knowledge_database_path_value = os.environ.get(
            "CONTROLSTUDIO_KNOWLEDGE_DATABASE"
        )
        static_dir = Path(os.environ.get("CONTROLSTUDIO_WEB_DIST", str(_default_static_dir())))
        canonical_origin = os.environ.get("CONTROLSTUDIO_CANONICAL_ORIGIN")
        launch_token = os.environ.get("CONTROLSTUDIO_LAUNCH_TOKEN") or secrets.token_urlsafe(48)
        return cls(
            bind_host=bind_host,
            port=port,
            database_path=database_path,
            knowledge_database_path=(
                Path(knowledge_database_path_value)
                if knowledge_database_path_value
                else None
            ),
            static_dir=static_dir,
            canonical_origin=canonical_origin,
            launch_token=launch_token,
        )
