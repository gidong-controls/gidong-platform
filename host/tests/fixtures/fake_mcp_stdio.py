"""Minimal deterministic MCP stdio fixture for transport integration tests."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def _reply(request_id: object, result: object) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
    sys.stdout.flush()


def _spawn_job_children(pid_file: Path) -> None:
    """Create one ordinary and one explicit-breakaway harmless child for Windows tests."""

    if sys.platform != "win32":
        raise RuntimeError("the Job-child fixture is Windows-only")
    child_command = [sys.executable, __file__, "--hold-child"]
    child_streams = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    ordinary = subprocess.Popen(child_command, **child_streams)
    try:
        breakaway = subprocess.Popen(
            child_command,
            creationflags=(
                subprocess.CREATE_NO_WINDOW | subprocess.CREATE_BREAKAWAY_FROM_JOB
            ),
            **child_streams,
        )
    except BaseException:
        ordinary.terminate()
        ordinary.wait(timeout=5)
        raise
    pid_file.write_text(
        json.dumps({"ordinaryPid": ordinary.pid, "breakawayPid": breakaway.pid}),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--hold-after-initialize", action="store_true")
    parser.add_argument("--hold-child", action="store_true")
    parser.add_argument("--spawn-job-children")
    arguments = parser.parse_args()
    if arguments.hold_child:
        time.sleep(60)
        return 0
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = request.get("method")
        request_id = request.get("id")
        if request_id is None:
            continue
        if method == "initialize":
            if arguments.spawn_job_children:
                _spawn_job_children(Path(arguments.spawn_job_children))
            _reply(
                request_id,
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "transport-fixture", "version": "1.0"},
                },
            )
            if arguments.hold_after_initialize:
                time.sleep(30)
        elif method == "tools/list":
            _reply(request_id, {"tools": []})
        elif method == "ping":
            _reply(request_id, {})
        else:
            _reply(request_id, {})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
