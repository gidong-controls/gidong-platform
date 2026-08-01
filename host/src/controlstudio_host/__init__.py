"""ControlStudio local host."""

from __future__ import annotations

from typing import Any

__version__ = "0.1.0"

__all__ = ["HostConfig", "HostStore", "create_app"]


def __getattr__(name: str) -> Any:
    """Keep the Agent sidecar import graph free of Host execution modules."""

    if name == "create_app":
        from .app import create_app

        return create_app
    if name == "HostConfig":
        from .config import HostConfig

        return HostConfig
    if name == "HostStore":
        from .store import HostStore

        return HostStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
