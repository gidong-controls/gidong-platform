"""Windows stdio transport with an explicit, narrowly scoped GUI breakaway rule.

The upstream MCP stdio transport owns its server process in a Windows Job with
``KILL_ON_JOB_CLOSE``.  That is the right default for the MCP server itself,
but an XG5000 process launched by that server also needs to be able to honour
its already-reviewed ``CREATE_BREAKAWAY_FROM_JOB`` flag.  The upstream Job does
not opt into that one explicit breakaway, so a Host reload can otherwise close
the XG5000 process with the transport's process tree.

This module owns only the Windows process/Job seam.  It keeps the same MCP
stdio framing, shutdown sequence, and ``KILL_ON_JOB_CLOSE`` cleanup policy.
The additional Job flag does *not* detach ordinary descendants: only a child
which explicitly requests ``CREATE_BREAKAWAY_FROM_JOB`` can leave the Job.
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, TextIO

import anyio
import anyio.lowlevel
import mcp.types as mcp_types
from anyio.streams.text import TextReceiveStream
from fastmcp.client.transports.base import ClientTransport
from mcp import ClientSession
from mcp.client import stdio as mcp_stdio
from mcp.os.win32.utilities import _create_windows_fallback_process
from mcp.shared.message import SessionMessage

logger = logging.getLogger(__name__)


def _open_mcp_job_that_allows_explicit_gui_breakaway() -> tuple[Any, Any]:
    """Create the transport cleanup Job without trapping explicit IDE launches."""

    if sys.platform != "win32":
        raise RuntimeError("the ControlStudio Windows MCP transport requires Windows")
    try:
        import win32api
        import win32job
    except ImportError as exc:  # pragma: no cover - installed by MCP's Windows extra
        raise RuntimeError("Windows MCP Job support is unavailable") from exc

    job = win32job.CreateJobObject(None, "")
    try:
        information = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation
        )
        basic = information["BasicLimitInformation"]
        basic["LimitFlags"] = int(basic.get("LimitFlags", 0)) | (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | win32job.JOB_OBJECT_LIMIT_BREAKAWAY_OK
        )
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, information
        )
    except BaseException:
        win32api.CloseHandle(job)
        raise
    return job, (win32api, win32job)


def _assign_mcp_process_to_cleanup_job(process: Any, job: Any, api: tuple[Any, Any]) -> None:
    """Assign only the MCP server root to the owned cleanup Job."""

    win32api, win32job = api
    import win32con

    handle = win32api.OpenProcess(
        win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE,
        False,
        int(process.pid),
    )
    try:
        win32job.AssignProcessToJobObject(job, handle)
    finally:
        win32api.CloseHandle(handle)


async def _create_process(
    command: str,
    args: list[str],
    *,
    env: dict[str, str],
    errlog: TextIO,
    cwd: str | Path | None,
) -> tuple[Any, Any, tuple[Any, Any]]:
    """Start the MCP and retain the Job handle until stdio teardown completes."""

    job, api = _open_mcp_job_that_allows_explicit_gui_breakaway()
    process: Any | None = None
    try:
        try:
            process = await anyio.open_process(
                [command, *args],
                env=env,
                stderr=errlog,
                cwd=cwd,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except NotImplementedError:
            process = await _create_windows_fallback_process(command, args, env, errlog, cwd)
        _assign_mcp_process_to_cleanup_job(process, job, api)
        return process, job, api
    except BaseException:
        if process is not None:
            with contextlib.suppress(Exception):
                process.terminate()
        api[0].CloseHandle(job)
        raise


async def _terminate_process_tree(process: Any, job: Any, api: tuple[Any, Any]) -> None:
    """Terminate the MCP-owned Job only after the normal stdio grace period."""

    _win32api, win32job = api
    with contextlib.suppress(Exception):
        win32job.TerminateJobObject(job, 1)
    with contextlib.suppress(Exception):
        process.terminate()


@contextlib.asynccontextmanager
async def _windows_stdio_client(
    *,
    command: str,
    args: list[str],
    env: dict[str, str] | None,
    cwd: str | Path | None,
    errlog: TextIO,
) -> AsyncIterator[tuple[Any, Any]]:
    """The upstream MCP stdio protocol with the one reviewed Job difference."""

    read_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_reader = anyio.create_memory_object_stream(0)
    process: Any | None = None
    job: Any | None = None
    api: tuple[Any, Any] | None = None
    try:
        process, job, api = await _create_process(
            mcp_stdio._get_executable_command(command),
            args,
            env=(
                {**mcp_stdio.get_default_environment(), **env}
                if env is not None
                else mcp_stdio.get_default_environment()
            ),
            errlog=errlog,
            cwd=cwd,
        )

        async def stdout_reader() -> None:
            assert process is not None and process.stdout is not None
            try:
                async with read_writer:
                    buffer = ""
                    async for chunk in TextReceiveStream(process.stdout, encoding="utf-8"):
                        lines = (buffer + chunk).split("\n")
                        buffer = lines.pop()
                        for line in lines:
                            try:
                                message = mcp_types.JSONRPCMessage.model_validate_json(line)
                            except Exception as exc:  # pragma: no cover - upstream parity
                                logger.exception("Failed to parse JSONRPC message from MCP server")
                                await read_writer.send(exc)
                                continue
                            await read_writer.send(SessionMessage(message))
            except anyio.ClosedResourceError:  # pragma: no cover - upstream parity
                await anyio.lowlevel.checkpoint()

        async def stdin_writer() -> None:
            assert process is not None and process.stdin is not None
            try:
                async with write_reader:
                    async for session_message in write_reader:
                        encoded = session_message.message.model_dump_json(
                            by_alias=True, exclude_none=True
                        )
                        await process.stdin.send((encoded + "\n").encode("utf-8"))
            except anyio.ClosedResourceError:  # pragma: no cover - upstream parity
                await anyio.lowlevel.checkpoint()

        async with anyio.create_task_group() as task_group, process:
            task_group.start_soon(stdout_reader)
            task_group.start_soon(stdin_writer)
            try:
                yield read_stream, write_stream
            finally:
                if process.stdin is not None:
                    with contextlib.suppress(Exception):
                        await process.stdin.aclose()
                try:
                    with anyio.fail_after(mcp_stdio.PROCESS_TERMINATION_TIMEOUT):
                        await process.wait()
                except TimeoutError:
                    assert job is not None and api is not None
                    await _terminate_process_tree(process, job, api)
                except ProcessLookupError:  # pragma: no cover - already exited
                    pass
    finally:
        await read_stream.aclose()
        await write_stream.aclose()
        await read_writer.aclose()
        await write_reader.aclose()
        if job is not None and api is not None:
            with contextlib.suppress(Exception):
                api[0].CloseHandle(job)


class ControlStudioWindowsStdioTransport(ClientTransport):
    """The Host's Windows-only MCP transport with explicit GUI breakaway support."""

    def __init__(
        self,
        *,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self.command = command
        self.args = list(args)
        self.env = dict(env) if env is not None else None
        self.cwd = cwd

    @contextlib.asynccontextmanager
    async def connect_session(self, **session_kwargs: Any) -> AsyncIterator[ClientSession]:
        async with _windows_stdio_client(
            command=self.command,
            args=self.args,
            env=self.env,
            cwd=self.cwd,
            errlog=sys.stderr,
        ) as (read_stream, write_stream), ClientSession(
            read_stream, write_stream, **session_kwargs
        ) as session:
            yield session
