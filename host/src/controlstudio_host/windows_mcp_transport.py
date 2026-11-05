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

import asyncio
import codecs
import contextlib
import logging
import math
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, TextIO

import anyio
import anyio.lowlevel
import mcp.types as mcp_types
from anyio.abc import ByteReceiveStream
from anyio.streams.text import TextReceiveStream
from fastmcp.client.transports.base import ClientTransport
from mcp import ClientSession
from mcp.client import stdio as mcp_stdio
from mcp.os.win32.utilities import _create_windows_fallback_process
from mcp.shared.message import SessionMessage

logger = logging.getLogger(__name__)


class _MCPStartupProbe:
    """One startup's bounded scalar evidence; never emit protocol/private data."""

    _ERROR_TYPES = frozenset({
        "RuntimeError", "TimeoutError", "MCPStartupError", "ClosedResourceError",
        "BrokenResourceError", "ExceptionGroup", "BaseExceptionGroup",
        "CancelledError", "ValueError", "OSError", "NotImplementedError", "UnicodeDecodeError",
    })

    def __init__(self, configured_timeout: float) -> None:
        self.started = time.monotonic()
        self.configured_timeout_ms = (
            int(configured_timeout * 1000)
            if isinstance(configured_timeout, (int, float))
            and math.isfinite(configured_timeout) and configured_timeout >= 0
            else None
        )
        self.active = True
        self.transport_observed = False
        self.process_retired = False
        self.initialize_entered = False
        self.initialize_completed = False
        self.initialize_request_write_started = False
        self.initialize_request_write_completed = False
        self.parsed_initialize_match = False
        self.sdk_handoff_started = False
        self.sdk_handoff_completed = False
        self.initialized_notification_handoff_started = False
        self.initialized_notification_handoff_completed = False
        self.initialized_notification_write_started = False
        self.initialized_notification_write_completed = False
        self.initialize_elapsed_ms: int | None = None
        self.process_created_ms: int | None = None
        self.loop_max_lag_ms = 0
        self._initialize_started: float | None = None
        self._initialize_request_id: Any = None
        self._next_tick: float | None = None
        self._heartbeat: asyncio.Task[None] | None = None
        self.execution_context: dict[str, bool | str] = {}
        self.process_wrapper = "unobserved"
        self.stdout_reader_entered = False
        self.stdout_first_chunk_elapsed_ms: int | None = None
        self.stdout_chunks = 0
        self.stdout_bytes = 0
        self.stdout_newlines = 0
        self.stdout_parse_failures = 0
        self.stdout_eof = False
        self.stdout_read_error = "none"
        self.child_startup_phases = []
        self.child_startup_marker_invalid = False

    def stdout_data(self, chunk: bytes) -> None:
        if not self.active:return
        if self.stdout_first_chunk_elapsed_ms is None:
            self.stdout_first_chunk_elapsed_ms = int((time.monotonic() - self.started) * 1000)
        self.stdout_chunks += 1
        self.stdout_bytes += len(chunk)
        self.stdout_newlines += chunk.count(b"\n")

    @classmethod
    def error_type(cls, exc: BaseException | None) -> str:
        if exc is None:
            return "none"
        name = type(exc).__name__
        return name if name in cls._ERROR_TYPES else "other"

    def start(self) -> None:
        self._next_tick = time.monotonic() + 0.1
        self._heartbeat = asyncio.create_task(self._watch_loop(), name="mcp-startup-scalar-heartbeat")

    async def _watch_loop(self) -> None:
        deadline = self.started + max(30.0, (self.configured_timeout_ms or 0) / 1000.0)
        try:
            while self.active and time.monotonic() < deadline:
                assert self._next_tick is not None
                await asyncio.sleep(max(0.0, self._next_tick - time.monotonic()))
                now = time.monotonic()
                self._record_lag(now)
                self._next_tick = now + 0.1
        finally:
            self._next_tick = None

    def _record_lag(self, now: float) -> None:
        if self._next_tick is not None:
            self.loop_max_lag_ms = max(self.loop_max_lag_ms, int(max(0.0, now - self._next_tick) * 1000))

    def stop(self) -> None:
        if not self.active:
            return
        self._record_lag(time.monotonic())
        self.active = False
        self._initialize_request_id = None
        task = self._heartbeat
        if task is not None and not task.done():
            task.cancel()

    def initialize_start(self) -> None:
        if self.active:
            self.initialize_entered = True
            self._initialize_started = time.monotonic()

    def initialize_finish(self, completed: bool) -> None:
        if self.active:
            self.initialize_completed = completed
            if self._initialize_started is not None:
                self.initialize_elapsed_ms = int((time.monotonic() - self._initialize_started) * 1000)

    def outgoing_start(self, root: Any) -> str | None:
        if not self.active:
            return None
        if isinstance(root, mcp_types.JSONRPCRequest) and root.method == "initialize":
            # Private transient comparison only; erased at stop, never serialized.
            self._initialize_request_id = root.id
            self.initialize_request_write_started = True
            return "initialize"
        if isinstance(root, mcp_types.JSONRPCNotification) and root.method == "notifications/initialized":
            self.initialized_notification_write_started = True
            return "initialized"
        return None

    def outgoing_complete(self, kind: str | None) -> None:
        if self.active:
            if kind == "initialize":
                self.initialize_request_write_completed = True
            elif kind == "initialized":
                self.initialized_notification_write_completed = True

    def incoming_start(self, root: Any) -> bool:
        matched = bool(
            self.active and self._initialize_request_id is not None
            and isinstance(root, (mcp_types.JSONRPCResponse, mcp_types.JSONRPCError))
            and root.id == self._initialize_request_id
        )
        if matched:
            self.parsed_initialize_match = True
            self.sdk_handoff_started = True
        return matched

    def incoming_complete(self, matched: bool) -> None:
        if self.active and matched:
            self.sdk_handoff_completed = True

    def failure_summary(self, stage: str, exc: BaseException) -> dict[str, Any]:
        return {
            "schema": 1,
            "child_startup_phases": list(self.child_startup_phases),
            "child_startup_marker_invalid": self.child_startup_marker_invalid,
            "phase": stage if stage in {"process_initialize", "tool_catalog", "contract_manifest"} else "other",
            "error_type": self.error_type(exc),
            "cause_type": self.error_type(exc.__cause__ or exc.__context__),
            "elapsed_ms": int((time.monotonic() - self.started) * 1000),
            "configured_timeout_ms": self.configured_timeout_ms,
            "process_created_ms": self.process_created_ms,
            "transport_observed": self.transport_observed,
            "initialize_entered": self.initialize_entered,
            "initialize_completed": self.initialize_completed,
            "initialize_elapsed_ms": self.initialize_elapsed_ms,
            "initialize_request_write_started": self.initialize_request_write_started,
            "initialize_request_write_completed": self.initialize_request_write_completed,
            "parsed_initialize_match": self.parsed_initialize_match,
            "sdk_handoff_started": self.sdk_handoff_started,
            "sdk_handoff_completed": self.sdk_handoff_completed,
            "initialized_notification_handoff_started": self.initialized_notification_handoff_started,
            "initialized_notification_handoff_completed": self.initialized_notification_handoff_completed,
            "initialized_notification_write_started": self.initialized_notification_write_started,
            "initialized_notification_write_completed": self.initialized_notification_write_completed,
            "loop_max_lag_ms": self.loop_max_lag_ms,
            "execution_context": dict(self.execution_context),
            "process_wrapper": self.process_wrapper,
            "stdout_reader_entered": self.stdout_reader_entered,
            "stdout_first_chunk_elapsed_ms": self.stdout_first_chunk_elapsed_ms,
            "stdout_chunks": self.stdout_chunks,
            "stdout_bytes": self.stdout_bytes,
            "stdout_newlines": self.stdout_newlines,
            "stdout_parse_failures": self.stdout_parse_failures,
            "stdout_eof": self.stdout_eof,
            "stdout_read_error": self.stdout_read_error,
        }


class _ChildStartupMarkers:
    """Bounded closed markers from this exact child pipe; no control authority."""
    prefix=b'CONTROLSTUDIO_MCP_CHILD_STARTUP '
    phases=('import_started','module_ready','main_entered')
    def __init__(self,probe):
        self.probe=probe;self.pending=bytearray();self.discarding=False
    def invalidate(self):
        self.probe.child_startup_marker_invalid=True;self.probe.child_startup_phases.clear()
    def feed(self,data):
        if self.probe is None or not self.probe.active:return
        for part in data.splitlines(keepends=True):
            ended=part.endswith(b'\n')
            if not self.discarding:
                if len(self.pending)+len(part)>512:
                    if bytes(self.pending).startswith(self.prefix) or part.startswith(self.prefix):self.invalidate()
                    self.pending.clear();self.discarding=True
                else:self.pending.extend(part)
            if ended:
                if not self.discarding:self.line(bytes(self.pending).rstrip(b'\r\n'))
                self.pending.clear();self.discarding=False
    def line(self,value):
        if not value.startswith(self.prefix) or self.probe.child_startup_marker_invalid:return
        try:
            phase,number=value[len(self.prefix):].split(b' ');index=len(self.probe.child_startup_phases)
            if index>=3 or phase.decode('ascii')!=self.phases[index] or not number.isdigit() or len(number)>9:raise ValueError()
            elapsed=int(number)
            if self.probe.child_startup_phases and elapsed<self.probe.child_startup_phases[-1]['elapsed_ms']:raise ValueError()
            self.probe.child_startup_phases.append({'phase':self.phases[index],'elapsed_ms':elapsed})
        except (ValueError,UnicodeError):self.invalidate()
    def finish(self):
        self.pending.clear();self.discarding=False

async def _drain_child_stderr(stream,errlog,probe):
    parser=_ChildStartupMarkers(probe);decoder=codecs.getincrementaldecoder('utf-8')(errors='replace')
    try:
        while True:
            if hasattr(stream,'receive'):
                try:data=await stream.receive(4096)
                except anyio.EndOfStream:break
            else:
                data=await anyio.to_thread.run_sync(getattr(stream,'read1',stream.read),4096,abandon_on_cancel=True)
            if not data:break
            parser.feed(data);text=decoder.decode(data)
            if text:
                try:errlog.write(text);errlog.flush()
                except Exception:pass
        tail=decoder.decode(b'',final=True)
        if tail:
            try:errlog.write(tail);errlog.flush()
            except Exception:pass
    except (anyio.ClosedResourceError,anyio.BrokenResourceError,OSError,ValueError):pass
    finally:
        parser.finish()
        # The existing process context owns pipe closure after retirement.
        # Do not synchronously close a fallback file while its read thread
        # may be blocked; that would deadlock cancellation on the file lock.


class _StartupObservedStdout(ByteReceiveStream):
    """Count raw bytes before text decoding; retain no payload or exception text."""
    def __init__(self, stream: Any, probe: _MCPStartupProbe) -> None:
        self.stream,self.probe = stream,probe

    async def receive(self, max_bytes: int = 65536) -> bytes:
        try:
            data = await self.stream.receive(max_bytes)
        except anyio.EndOfStream:
            if self.probe.active:self.probe.stdout_eof = True
            raise
        except BaseException as exc:
            if self.probe.active:self.probe.stdout_read_error = self.probe.error_type(exc)
            raise
        self.probe.stdout_data(data)
        return data

    async def aclose(self) -> None:
        await self.stream.aclose()

    @property
    def extra_attributes(self) -> Any:
        return self.stream.extra_attributes


class _StartupObservedClientSession(ClientSession):
    def __init__(self, *args: Any, startup_probe: _MCPStartupProbe, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._startup_probe = startup_probe

    async def initialize(self) -> Any:
        self._startup_probe.initialize_start()
        try:
            result = await super().initialize()
        except BaseException:
            self._startup_probe.initialize_finish(False)
            raise
        self._startup_probe.initialize_finish(True)
        return result

    async def send_notification(self, notification: Any, related_request_id: Any = None) -> None:
        probe = self._startup_probe
        initialized = isinstance(notification.root, mcp_types.InitializedNotification)
        if probe.active and initialized:
            probe.initialized_notification_handoff_started = True
        await super().send_notification(notification, related_request_id)
        if probe.active and initialized:
            probe.initialized_notification_handoff_completed = True


def _open_process_cleanup_job(*, allow_gui_breakaway: bool = False) -> tuple[Any, Any]:
    """Own a process tree; only MCP may opt into explicit IDE breakaway."""

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
        flags = int(basic.get("LimitFlags", 0)) | win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if allow_gui_breakaway:
            flags |= win32job.JOB_OBJECT_LIMIT_BREAKAWAY_OK
        basic["LimitFlags"] = flags
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, information
        )
    except BaseException:
        win32api.CloseHandle(job)
        raise
    return job, (win32api, win32job)


def _assign_process_to_cleanup_job(process: Any, job: Any, api: tuple[Any, Any]) -> None:
    """Assign the exact created root; Agent calls this before resuming its thread."""

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


def _resume_suspended_cleanup_process(process: Any) -> None:
    """Resume only a newly created, still-suspended root after Job assignment."""
    import ctypes
    from ctypes import wintypes

    import psutil
    threads = psutil.Process(process.pid).threads()
    if len(threads) != 1:
        raise RuntimeError('Suspended cleanup root has no unique initial thread')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    opened = kernel.OpenThread
    opened.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    opened.restype = wintypes.HANDLE
    owner = kernel.GetProcessIdOfThread
    owner.argtypes = [wintypes.HANDLE]; owner.restype = wintypes.DWORD
    resume = kernel.ResumeThread
    resume.argtypes = [wintypes.HANDLE]; resume.restype = wintypes.DWORD
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]; close.restype = wintypes.BOOL
    handle = opened(0x0002 | 0x0800, False, threads[0].id)
    if not handle:
        raise RuntimeError('Suspended cleanup root thread could not be opened')
    try:
        if owner(handle) != process.pid or resume(handle) != 1:
            raise RuntimeError('Suspended cleanup root thread identity/state changed')
    finally:
        close(handle)


async def _retire_process_cleanup_job(job: Any, api: tuple[Any, Any], *, timeout: float) -> None:
    """Retire this owned tree even after its root exits; prove zero live members."""
    win32api, win32job = api
    def active():
        value = win32job.QueryInformationJobObject(job, win32job.JobObjectBasicAccountingInformation)
        count = value.get('ActiveProcesses') if isinstance(value, dict) else None
        if type(count) is not int or count < 0:
            raise RuntimeError('Cleanup Job live-process observation is unavailable')
        return count
    if active():
        win32job.TerminateJobObject(job, 1)
    deadline = time.monotonic() + timeout
    while active():
        if time.monotonic() >= deadline:
            raise RuntimeError('Cleanup Job did not retire its owned process tree')
        await asyncio.sleep(min(0.05, max(0, deadline-time.monotonic())))
    win32api.CloseHandle(job)


async def _create_process(
    command: str,
    args: list[str],
    *,
    env: dict[str, str],
    errlog: TextIO,
    cwd: str | Path | None,
) -> tuple[Any, Any, tuple[Any, Any]]:
    """Start the MCP and retain the Job handle until stdio teardown completes."""

    job, api = _open_process_cleanup_job(allow_gui_breakaway=True)
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
        _assign_process_to_cleanup_job(process, job, api)
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
    startup_probe: _MCPStartupProbe | None = None,
) -> AsyncIterator[tuple[Any, Any]]:
    """The upstream MCP stdio protocol with the one reviewed Job difference."""

    read_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_reader = anyio.create_memory_object_stream(0)
    process: Any | None = None
    job: Any | None = None
    api: tuple[Any, Any] | None = None
    started = time.monotonic()
    process_created_ms = 0
    stdin_messages = 0
    stdout_messages = 0
    try:
        process, job, api = await _create_process(
            mcp_stdio._get_executable_command(command),
            args,
            env=(
                {**mcp_stdio.get_default_environment(), **env}
                if env is not None
                else mcp_stdio.get_default_environment()
            ),
            errlog=subprocess.PIPE,
            cwd=cwd,
        )
        process_created_ms = int((time.monotonic() - started) * 1000)
        if startup_probe is not None and startup_probe.active:
            startup_probe.transport_observed = True
            startup_probe.process_created_ms = process_created_ms
            startup_probe.process_wrapper = "fallback" if hasattr(process, "popen") else "anyio"

        async def stdout_reader() -> None:
            nonlocal stdout_messages
            assert process is not None and process.stdout is not None
            source = process.stdout
            if startup_probe is not None and startup_probe.active:
                startup_probe.stdout_reader_entered = True
                source = _StartupObservedStdout(source,startup_probe)
            try:
                async with read_writer:
                    line_parts: list[str] = []
                    async for chunk in TextReceiveStream(source, encoding="utf-8"):
                        lines = chunk.split("\n")
                        line_parts.append(lines[0])
                        if len(lines) == 1:
                            continue
                        # Assemble the pending line only when its delimiter arrives.
                        lines[0] = "".join(line_parts)
                        line_parts = [lines.pop()]
                        for line in lines:
                            try:
                                message = mcp_types.JSONRPCMessage.model_validate_json(line)
                            except Exception as exc:  # pragma: no cover - upstream parity
                                if startup_probe is not None and startup_probe.active:
                                    startup_probe.stdout_parse_failures += 1
                                logger.exception("Failed to parse JSONRPC message from MCP server")
                                try:
                                    await read_writer.send(exc)
                                except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                                    # The session may have closed after its initialize deadline.
                                    # A late response must not replace that original failure.
                                    return
                                continue
                            stdout_messages += 1
                            matched = startup_probe.incoming_start(message.root) if startup_probe is not None else False
                            try:
                                await read_writer.send(SessionMessage(message))
                                if startup_probe is not None:
                                    startup_probe.incoming_complete(matched)
                            except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                                return
            except anyio.ClosedResourceError:  # pragma: no cover - upstream parity
                await anyio.lowlevel.checkpoint()
            except BaseException as exc:
                if startup_probe is not None and startup_probe.active:
                    startup_probe.stdout_read_error = startup_probe.error_type(exc)
                raise

        async def stdin_writer() -> None:
            nonlocal stdin_messages
            assert process is not None and process.stdin is not None
            try:
                async with write_reader:
                    async for session_message in write_reader:
                        encoded = session_message.message.model_dump_json(
                            by_alias=True, exclude_none=True
                        )
                        observed_kind = startup_probe.outgoing_start(session_message.message.root) if startup_probe is not None else None
                        await process.stdin.send((encoded + "\n").encode("utf-8"))
                        if startup_probe is not None:
                            startup_probe.outgoing_complete(observed_kind)
                        stdin_messages += 1
            except anyio.ClosedResourceError:  # pragma: no cover - upstream parity
                await anyio.lowlevel.checkpoint()

        async with anyio.create_task_group() as task_group:
            try:
                async with process:
                    task_group.start_soon(stdout_reader)
                    task_group.start_soon(stdin_writer)
                    if process.stderr is not None:
                        task_group.start_soon(_drain_child_stderr,process.stderr,errlog,startup_probe)
                    try:
                        yield read_stream, write_stream
                    except BaseException as exc:
                        returncode = getattr(process, "returncode", None)
                        if returncode is None and hasattr(process, "popen"):
                            returncode = process.popen.poll()
                        logger.warning(
                            "MCP stdio session failed error_type=%s elapsed_ms=%d "
                            "process_created_ms=%d stdin_messages=%d stdout_messages=%d "
                            "child_returncode=%s",
                            type(exc).__name__,
                            int((time.monotonic() - started) * 1000),
                            process_created_ms,
                            stdin_messages,
                            stdout_messages,
                            returncode,
                        )
                        raise
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
                        if startup_probe is not None:
                            # Termination requests alone are not retirement evidence.
                            try:
                                returncode = getattr(process, "returncode", None)
                                if returncode is None and hasattr(process, "popen"):
                                    returncode = process.popen.poll()
                                startup_probe.process_retired = type(returncode) is int
                            except Exception:
                                pass
            finally:
                # Retire I/O pumps only after process/pipe context cleanup. A
                # zero-buffer sender or idle writer may otherwise wait forever
                # for memory streams closed by the outer finally below.
                task_group.cancel_scope.cancel()
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
        startup_probe: _MCPStartupProbe | None = None,
    ) -> None:
        self.command = command
        self.args = list(args)
        self.env = dict(env) if env is not None else None
        self.cwd = cwd
        self.startup_probe = startup_probe

    @contextlib.asynccontextmanager
    async def connect_session(self, **session_kwargs: Any) -> AsyncIterator[ClientSession]:
        session_type = _StartupObservedClientSession if self.startup_probe is not None else ClientSession
        if self.startup_probe is not None:
            session_kwargs = {**session_kwargs, "startup_probe": self.startup_probe}
        async with _windows_stdio_client(
            command=self.command,
            args=self.args,
            env=self.env,
            cwd=self.cwd,
            errlog=sys.stderr,
            startup_probe=self.startup_probe,
        ) as (read_stream, write_stream), session_type(
            read_stream, write_stream, **session_kwargs
        ) as session:
            yield session
