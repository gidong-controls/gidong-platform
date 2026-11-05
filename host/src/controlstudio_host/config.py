from __future__ import annotations

import ipaddress
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit


def _default_data_dir() -> Path:
    release_channel = os.environ.get("CONTROLSTUDIO_RELEASE_CHANNEL", "development")
    directory_name = {
        "production": "ControlStudio",
        "beta": "ControlStudio Beta",
        "development": "ControlStudio Development",
    }.get(release_channel)
    if directory_name is None:
        raise ValueError("CONTROLSTUDIO_RELEASE_CHANNEL is invalid")
    if local_app_data := os.environ.get("LOCALAPPDATA"):
        return Path(local_app_data) / directory_name
    return Path.home() / f".{directory_name.casefold().replace(' ', '-')}"


def _default_port() -> int:
    return {
        "production": 8765,
        "beta": 8766,
        "development": 8767,
    }.get(os.environ.get("CONTROLSTUDIO_RELEASE_CHANNEL", "development"), 8767)


def _default_static_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "web" / "dist"


@dataclass(frozen=True)
class HostConfig:
    """Configuration for the local, interactive-user ControlStudio Host.

    The first product milestone deliberately accepts loopback binds only.
    Remote access belongs behind a separately authenticated HTTPS gateway.
    """

    bind_host: str = "127.0.0.1"
    port: int = field(default_factory=_default_port)
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
    release_channel: Literal["development", "beta", "production"] = "development"
    product_release: str = "source"
    development_model_base_url: str | None = None
    development_model_name: str | None = None
    development_model_timeout_seconds: float = 3600.0
    development_model_api_key: str | None = field(default=None, repr=False)

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
        if self.release_channel not in {"development", "beta", "production"}:
            raise ValueError("release_channel is invalid")
        if not self.product_release.strip() or len(self.product_release) > 128:
            raise ValueError("product_release is invalid")

        canonical_origin = self.canonical_origin or f"http://localhost:{self.port}"
        if not canonical_origin.startswith(("http://localhost:", "http://127.0.0.1:")):
            raise ValueError("canonical_origin must use a loopback HTTP origin")
        try:
            parsed = urlsplit(canonical_origin)
            origin_port = parsed.port
            if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}
                    or parsed.username is not None or parsed.password is not None
                    or origin_port is None or not 1 <= origin_port <= 65535
                    or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                    or any(character.isspace() or ord(character) < 33 for character in canonical_origin)):
                raise ValueError("invalid origin components")
        except ValueError as exc:
            raise ValueError("canonical_origin must use a loopback HTTP origin") from exc
        authority = parsed.hostname + (f":{origin_port}" if origin_port != 80 else "")
        object.__setattr__(self, "canonical_origin", f"http://{authority}")
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
        port = int(os.environ.get("CONTROLSTUDIO_PORT", str(_default_port())))
        database_path = Path(
            os.environ.get(
                "CONTROLSTUDIO_DATABASE",
                str(_default_data_dir() / "controlstudio.sqlite3"),
            )
        )
        knowledge_database_path_value = os.environ.get("CONTROLSTUDIO_KNOWLEDGE_DATABASE")
        static_dir = Path(os.environ.get("CONTROLSTUDIO_WEB_DIST", str(_default_static_dir())))
        canonical_origin = os.environ.get("CONTROLSTUDIO_CANONICAL_ORIGIN")
        launch_token = os.environ.get("CONTROLSTUDIO_LAUNCH_TOKEN") or secrets.token_urlsafe(48)
        release_channel = os.environ.get("CONTROLSTUDIO_RELEASE_CHANNEL", "development")
        product_release = os.environ.get("CONTROLSTUDIO_PRODUCT_RELEASE", "source")
        development_model_api_key = os.environ.pop(
            "CONTROLSTUDIO_DEVELOPMENT_MODEL_API_KEY",
            None,
        )
        return cls(
            bind_host=bind_host,
            port=port,
            database_path=database_path,
            knowledge_database_path=(
                Path(knowledge_database_path_value) if knowledge_database_path_value else None
            ),
            static_dir=static_dir,
            canonical_origin=canonical_origin,
            launch_token=launch_token,
            release_channel=cast(Literal["development", "beta", "production"], release_channel),
            product_release=product_release,
            development_model_base_url=(
                os.environ.get("CONTROLSTUDIO_DEVELOPMENT_MODEL_BASE_URL")
                if release_channel == "development"
                else None
            ),
            development_model_name=(
                os.environ.get("CONTROLSTUDIO_DEVELOPMENT_MODEL_NAME")
                if release_channel == "development"
                else None
            ),
            development_model_timeout_seconds=float(
                os.environ.get("CONTROLSTUDIO_DEVELOPMENT_MODEL_TIMEOUT_SECONDS", "3600")
                if release_channel == "development"
                else "3600"
            ),
            development_model_api_key=(
                development_model_api_key if release_channel == "development" else None
            ),
        )
