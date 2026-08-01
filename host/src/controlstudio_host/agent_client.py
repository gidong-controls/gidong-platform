"""Host-side supervisor for the versioned Pydantic AI Agent sidecar."""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from controlstudio_contracts import ExternalToolRequestV1
from pydantic import ValidationError

from .agent_protocol import (
    AgentProtocolError,
    AgentProtocolMessage,
    assert_agent_safe_payload,
    decode_message,
    encode_message,
    parse_manifest,
    scrub_agent_tool_result,
)
from .agent_runtime import (
    AgentOutputContractError,
    AgentRunOutput,
    ChangePlanCallback,
    ModelCapabilityResult,
    TokenCallback,
    ToolEventCallback,
)
from .execution_broker import ExecutionBroker
from .model_connection import StoredModelConnection
from .models import Message, MessageRole

logger = logging.getLogger(__name__)

_SIDECAR_STRIPPED_ENVIRONMENT = frozenset(
    {
        "CONTROLSTUDIO_MCP_AUTHORIZATION_SECRET",
        "CONTROLSTUDIO_APPROVAL_SECRET",
    }
)


class AgentSidecarError(RuntimeError):
    """A sanitized failure reported by the supervised Agent process."""


class AgentSidecarCrashed(AgentSidecarError):
    """The sidecar exited or violated the protocol while a Host operation ran."""


class AgentSupervisor:
    """Own the child lifecycle and its stdout-only NDJSON protocol.

    The command is intentionally fixed by default.  Neither the MCP process nor
    its authorization secret is inherited as an explicit command argument.
    """

    def __init__(
        self,
        *,
        command: Sequence[str] | None = None,
        startup_timeout_seconds: float = 10.0,
        shutdown_timeout_seconds: float = 3.0,
        message_timeout_seconds: float = 600.0,
        pipe_drain_timeout_seconds: float = 5.0,
    ) -> None:
        # A shipped Platform resolves the installed private artifact from PATH
        # or an explicitly injected immutable artifact command. It never
        # discovers a checkout-relative or sibling Agent source tree.
        configured_command = _configured_agent_command()
        if command is not None:
            self._command = tuple(command)
        elif configured_command is not None:
            self._command = configured_command
        elif development_command := _monorepo_agent_command():
            self._command = development_command
        else:
            self._command = ("controlstudio-agent",)
        self._startup_timeout_seconds = startup_timeout_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._message_timeout_seconds = message_timeout_seconds
        self._pipe_drain_timeout_seconds = pipe_drain_timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._messages: asyncio.Queue[AgentProtocolMessage | None] = asyncio.Queue()
        self._lifecycle_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._manifest_agent_id: str | None = None

    @property
    def manifest_agent_id(self) -> str | None:
        return self._manifest_agent_id

    async def ensure_started(self) -> None:
        async with self._lifecycle_lock:
            if self._process is not None and self._process.returncode is None:
                return
            await self._discard_dead_process()
            try:
                process = await asyncio.create_subprocess_exec(
                    *self._command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=_sidecar_environment(),
                )
            except OSError as exc:
                raise AgentSidecarCrashed("Agent sidecar could not be started") from exc
            self._process = process
            self._messages = asyncio.Queue()
            self._stdout_task = asyncio.create_task(
                self._read_stdout(process), name="controlstudio-agent-sidecar-stdout"
            )
            self._stderr_task = asyncio.create_task(
                self._drain_stderr(process), name="controlstudio-agent-sidecar-stderr"
            )
            try:
                initialized = await asyncio.wait_for(
                    self._request({"type": "initialize"}, expected="initialized"),
                    timeout=self._startup_timeout_seconds,
                )
                manifest_payload = initialized.payload.get("manifest")
                if not isinstance(manifest_payload, Mapping):
                    raise AgentProtocolError("Agent initialization omitted a manifest")
                manifest = parse_manifest(manifest_payload)
                self._manifest_agent_id = manifest.agent_id
                health = await asyncio.wait_for(
                    self._request({"type": "health"}, expected="health.result"),
                    timeout=self._startup_timeout_seconds,
                )
                if health.payload.get("status") != "ready":
                    raise AgentProtocolError("Agent sidecar health check failed")
            except BaseException as exc:
                await self._terminate_process()
                if isinstance(exc, AgentSidecarError):
                    raise
                raise AgentSidecarCrashed("Agent sidecar handshake failed") from exc

    async def run(
        self,
        payload: Mapping[str, Any],
        *,
        on_message: Callable[[AgentProtocolMessage], Awaitable[None]],
    ) -> None:
        """Run one supervised Agent operation until its terminal sidecar frame."""

        await self.ensure_started()
        async with self._run_lock:
            await self._send("run.start", payload)
            while True:
                message = await self._next_message_with_timeout("run response")
                if message.type == "run.started":
                    continue
                await on_message(message)
                if message.type in {"run.final", "run.error", "run.cancelled"}:
                    return

    async def send_tool_result(self, payload: Mapping[str, Any]) -> None:
        await self._send("tool.result", payload)

    async def sync_knowledge(self, documents: Sequence[Mapping[str, Any]]) -> int:
        """Replace the Agent's disposable derived source/index snapshot."""

        await self.ensure_started()
        async with self._run_lock:
            await self._send("knowledge.sync", {"documents": list(documents)})
            response = await self._next_message_with_timeout("knowledge sync")
            if response.type != "knowledge.synced":
                raise AgentProtocolError("Agent sidecar rejected knowledge synchronization")
            count = response.payload.get("count")
            if not isinstance(count, int) or count < 0:
                raise AgentProtocolError("Agent knowledge synchronization result is invalid")
            return count

    async def search_knowledge(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Ask the Agent sidecar to rank its disposable derived index."""

        await self.ensure_started()
        async with self._run_lock:
            await self._send("knowledge.search", payload)
            response = await self._next_message_with_timeout("knowledge search")
            if response.type != "knowledge.search.result":
                raise AgentProtocolError("Agent sidecar rejected knowledge search")
            results = response.payload.get("results")
            if not isinstance(results, list):
                raise AgentProtocolError("Agent knowledge search result is invalid")
            return dict(response.payload)

    async def cancel_run(self, run_id: str) -> None:
        process = self._process
        if process is None or process.returncode is not None:
            return
        try:
            await self._send("run.cancel", {"run_id": run_id})
        except AgentSidecarError:
            # The caller is already cancelling; a child crash cannot make a
            # Host cancellation fail or turn into a different run result.
            return

    async def test_capabilities(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        await self.ensure_started()
        async with self._run_lock:
            await self._send("capabilities.test", payload)
            while True:
                message = await self._next_message_with_timeout("capability response")
                if message.type == "capabilities.result":
                    return message.payload
                if message.type == "protocol.error":
                    raise AgentSidecarError("Agent sidecar rejected the capability request")

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            process = self._process
            if process is None:
                return
            if process.returncode is None:
                try:
                    await self._send("shutdown", {})
                    while True:
                        response = await self._next_message_with_timeout(
                            "shutdown response",
                            timeout_seconds=self._shutdown_timeout_seconds,
                        )
                        if response.type == "shutdown.complete":
                            break
                except (AgentSidecarError, asyncio.TimeoutError):
                    pass
            await self._terminate_process()

    async def _request(
        self,
        command: Mapping[str, Any],
        *,
        expected: str,
    ) -> AgentProtocolMessage:
        message_type = command.get("type")
        if not isinstance(message_type, str):
            raise AgentProtocolError("Agent command type is invalid")
        await self._send(message_type, command.get("payload", {}))
        while True:
            response = await self._next_message()
            if response.type == expected:
                return response
            if response.type == "protocol.error":
                raise AgentProtocolError("Agent sidecar rejected a protocol command")

    async def _send(self, message_type: str, payload: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.returncode is not None or process.stdin is None:
            raise AgentSidecarCrashed("Agent sidecar is not running")
        try:
            encoded = encode_message(message_type, payload)
            async with self._write_lock:
                process.stdin.write(encoded)
                await asyncio.wait_for(
                    process.stdin.drain(), timeout=self._pipe_drain_timeout_seconds
                )
        except (BrokenPipeError, ConnectionError, OSError, asyncio.TimeoutError) as exc:
            await self._terminate_process()
            raise AgentSidecarCrashed("Agent sidecar pipe closed") from exc

    async def _next_message(self) -> AgentProtocolMessage:
        message = await self._messages.get()
        if message is None:
            await self._terminate_process()
            raise AgentSidecarCrashed("Agent sidecar exited unexpectedly")
        if message.type == "protocol.error":
            raise AgentSidecarError("Agent sidecar reported a protocol error")
        return message

    async def _next_message_with_timeout(
        self,
        operation: str,
        *,
        timeout_seconds: float | None = None,
    ) -> AgentProtocolMessage:
        try:
            return await asyncio.wait_for(
                self._next_message(),
                timeout=(
                    self._message_timeout_seconds
                    if timeout_seconds is None
                    else timeout_seconds
                ),
            )
        except asyncio.TimeoutError as exc:
            await self._terminate_process()
            raise AgentSidecarCrashed(f"Agent sidecar timed out waiting for {operation}") from exc

    async def _read_stdout(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        try:
            while line := await process.stdout.readline():
                try:
                    message = decode_message(line)
                except AgentProtocolError:
                    logger.warning("Agent sidecar emitted an invalid stdout frame")
                    break
                await self._messages.put(message)
        finally:
            await self._messages.put(None)

    async def _drain_stderr(self, process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        # stderr is allowed for diagnostics, but do not mirror arbitrary child
        # text into Host logs where endpoint data or credentials could spread.
        emitted = False
        while await process.stderr.readline():
            emitted = True
        if emitted:
            logger.warning("Agent sidecar emitted diagnostics on stderr")

    async def _discard_dead_process(self) -> None:
        if self._process is not None:
            await self._terminate_process()

    async def _terminate_process(self) -> None:
        process = self._process
        self._process = None
        self._manifest_agent_id = None
        if process is not None and process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=self._shutdown_timeout_seconds)
            except asyncio.TimeoutError:
                try:
                    process.terminate()
                except ProcessLookupError:
                    # Windows can report the timeout just as the child exits.
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=self._shutdown_timeout_seconds)
                except asyncio.TimeoutError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        # The transport already observed the child exit.
                        pass
                    await process.wait()
        tasks = [task for task in (self._stdout_task, self._stderr_task) if task is not None]
        self._stdout_task = None
        self._stderr_task = None
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=self._shutdown_timeout_seconds,
                )
            except asyncio.TimeoutError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)


class SubprocessAgentClient:
    """Production Agent runtime adapter; Host retains all external authority."""

    def __init__(
        self,
        settings: StoredModelConnection,
        broker: ExecutionBroker,
        supervisor: AgentSupervisor,
    ) -> None:
        self._settings = settings
        self._broker = broker
        self._supervisor = supervisor
        self._change_plan_callback: ChangePlanCallback | None = None
        self._knowledge_snapshot_callback: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None

    def bind_change_planner(self, callback: ChangePlanCallback) -> None:
        self._change_plan_callback = callback

    def bind_knowledge_snapshot(
        self, callback: Callable[[], Awaitable[list[dict[str, Any]]]]
    ) -> None:
        self._knowledge_snapshot_callback = callback

    async def run(
        self,
        *,
        prompt: str,
        history: Sequence[Message],
        conversation_id: str,
        run_id: str,
        trusted_context: dict[str, Any] | None = None,
        on_token: TokenCallback | None = None,
        on_tool_event: ToolEventCallback | None = None,
    ) -> AgentRunOutput:
        if self._knowledge_snapshot_callback is not None:
            # Sync before every run: deletion/update and a restarted sidecar
            # cannot retain stale Agent-derived index entries.
            documents = await self._knowledge_snapshot_callback()
            await self._supervisor.sync_knowledge(documents)
        tools = await self._broker.agent_tool_catalog()
        catalog = {tool.tool_name for tool in tools}
        payload = {
            "conversation_id": conversation_id,
            "run_id": run_id,
            "prompt": prompt,
            "history": _safe_history(history),
            "trusted_context": _safe_trusted_context(
                trusted_context,
                knowledge_search_available=self._knowledge_snapshot_callback is not None,
                change_planner_available=self._change_plan_callback is not None,
            ),
            "model": _model_payload(self._settings),
            "tools": [tool.model_dump(mode="json") for tool in tools],
        }
        assert_agent_safe_payload(payload)
        result: AgentRunOutput | None = None

        async def handle(message: AgentProtocolMessage) -> None:
            nonlocal result
            if message.type == "run.token":
                if message.payload.get("run_id") != run_id:
                    raise AgentProtocolError("Agent token belongs to another run")
                delta = message.payload.get("delta")
                if isinstance(delta, str) and on_token is not None:
                    await on_token(delta)
                return
            if message.type == "tool.request":
                request = _tool_request_from_message(message, conversation_id, run_id)
                await self._dispatch_tool_request(
                    request,
                    catalog=catalog,
                    on_tool_event=on_tool_event,
                )
                return
            if message.type == "run.final":
                if message.payload.get("run_id") != run_id:
                    raise AgentProtocolError("Agent final result belongs to another run")
                result = _output_from_payload(message.payload.get("output"))
                return
            if message.type == "run.cancelled":
                raise asyncio.CancelledError
            if message.type == "run.error":
                if message.payload.get("code") == "output_contract":
                    raise AgentOutputContractError("Agent output failed its structured contract")
                raise AgentSidecarError("Agent sidecar could not complete the run")
            raise AgentProtocolError("unexpected Agent sidecar run message")

        try:
            await self._supervisor.run(payload, on_message=handle)
        except asyncio.CancelledError:
            await asyncio.shield(self._supervisor.cancel_run(run_id))
            raise
        if result is None:
            raise AgentSidecarError("Agent sidecar ended without a final result")
        return result

    async def _dispatch_tool_request(
        self,
        request: ExternalToolRequestV1,
        *,
        catalog: set[str],
        on_tool_event: ToolEventCallback | None,
    ) -> None:
        try:
            if request.tool_name in catalog:
                if on_tool_event is None:
                    async def ignore_event(_event_type: str, _payload: dict[str, Any]) -> None:
                        return None
                    sink = ignore_event
                else:
                    sink = on_tool_event
                value = await self._broker.execute_external_tool_request(request, sink=sink)
            elif (
                request.tool_name == "change.propose_offline"
                and self._change_plan_callback is not None
            ):
                value = await self._change_plan_callback(**request.arguments)
            else:
                raise AgentSidecarError("Agent requested a disallowed Host callback")
        except asyncio.CancelledError:
            await self._supervisor.send_tool_result(
                {
                    "request_id": request.request_id,
                    "status": "failed",
                    "failure_kind": "cancelled",
                }
            )
            raise
        except Exception:  # noqa: BLE001 - never expose Host callback diagnostics to Agent
            await self._supervisor.send_tool_result(
                {
                    "request_id": request.request_id,
                    "status": "failed",
                    "failure_kind": "host_policy",
                }
            )
            return
        await self._supervisor.send_tool_result(
            {
                "request_id": request.request_id,
                "status": "completed",
                "result": scrub_agent_tool_result(value),
            }
        )

    async def test_capabilities(self) -> ModelCapabilityResult:
        request_id = str(uuid.uuid4())
        payload = {"request_id": request_id, "model": _model_payload(self._settings)}
        assert_agent_safe_payload(payload)
        response = await self._supervisor.test_capabilities(payload)
        if response.get("request_id") != request_id:
            raise AgentProtocolError("Agent capability result belongs to another request")
        result = response.get("result")
        if not isinstance(result, dict):
            return ModelCapabilityResult(
                success=False,
                tested_at=datetime.now(timezone.utc),
                completion=False,
                streaming=False,
                tool_calling=False,
                tool_result_continuation=False,
                structured_output=False,
                message="The Agent sidecar could not verify model capabilities.",
            )
        try:
            tested_at = datetime.fromisoformat(str(result["tested_at"]))
            return ModelCapabilityResult(
                success=bool(result["success"]),
                tested_at=tested_at,
                completion=bool(result["completion"]),
                streaming=bool(result["streaming"]),
                tool_calling=bool(result["tool_calling"]),
                tool_result_continuation=bool(result["tool_result_continuation"]),
                structured_output=bool(result["structured_output"]),
                message=str(result["message"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AgentProtocolError("Agent capability result is invalid") from exc


def _model_payload(settings: StoredModelConnection) -> dict[str, Any]:
    return {
        "protocol": settings.protocol,
        "base_url": settings.base_url,
        "model": settings.model,
        "timeout_seconds": settings.timeout_seconds,
        # Model credentials are needed by the process that calls the model, but
        # are never persisted, logged, passed in argv, or sent to MCP.
        "api_key": settings.secrets.api_key,
        "custom_headers": settings.secrets.custom_headers,
    }


def _configured_agent_command() -> tuple[str, ...] | None:
    value = os.environ.get("CONTROLSTUDIO_AGENT_COMMAND", "").strip()
    if not value:
        return None
    parts = shlex.split(value, posix=False)
    command = tuple(
        part[1:-1]
        if len(part) >= 2 and part[0] == part[-1] and part[0] in {'"', "'"}
        else part
        for part in parts
    )
    if not command:
        return None
    return command


def _monorepo_agent_command() -> tuple[str, ...] | None:
    """Resolve the Agent's installed console entry point for local development.

    This intentionally does not import private Agent source or walk its source
    tree.  A release installation has no adjacent development environment and
    therefore falls through to the packaged command on ``PATH``.
    """

    repository_root = Path(__file__).resolve().parents[3]
    executable = repository_root / "agent" / ".venv" / "Scripts" / "controlstudio-agent.exe"
    if executable.is_file():
        return (str(executable),)
    return None


def _sidecar_environment() -> dict[str, str]:
    """Pass ordinary process settings while withholding Host approval material."""

    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _SIDECAR_STRIPPED_ENVIRONMENT
    }
    return environment


def _safe_history(history: Sequence[Message]) -> list[dict[str, Any]]:
    return [
        {
            "role": item.role.value,
            "content": item.content,
            "created_at": item.created_at.isoformat(),
        }
        for item in history[-100:]
        if item.role in {MessageRole.USER, MessageRole.ASSISTANT}
    ]


def _safe_trusted_context(
    value: dict[str, Any] | None,
    *,
    knowledge_search_available: bool,
    change_planner_available: bool,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if value.get("workflow") == "new_project_planning":
        brief = value.get("project_brief")
        return {
            "workflow": "new_project_planning",
            "project_brief": {
                key: item
                for key, item in brief.items()
                if isinstance(key, str) and isinstance(item, str)
            }
            if isinstance(brief, dict)
            else {},
            "current_prompt_is_structured_brief": bool(
                value.get("current_prompt_is_structured_brief")
            ),
            "knowledge_search_available": knowledge_search_available,
        }
    context: dict[str, Any] = {
        "workflow": "engineering",
        "knowledge_search_available": knowledge_search_available,
        "change_planner_available": change_planner_available,
    }
    if value.get("change_request_scope") in {
        "analysis_only",
        "offline_candidate_revision",
        "permanent_offline_revision",
    }:
        context["change_request_scope"] = value["change_request_scope"]
    if isinstance(value.get("stage"), str):
        context["stage"] = value["stage"]
    return context


def _tool_request_from_message(
    message: AgentProtocolMessage,
    conversation_id: str,
    run_id: str,
) -> ExternalToolRequestV1:
    try:
        request = ExternalToolRequestV1.model_validate(message.payload)
    except ValidationError as exc:
        raise AgentProtocolError("Agent external tool request is invalid") from exc
    if request.conversation_id != conversation_id or request.run_id != run_id:
        raise AgentProtocolError("Agent external tool request targets another run")
    return request


def _output_from_payload(value: Any) -> AgentRunOutput:
    if not isinstance(value, Mapping):
        raise AgentProtocolError("Agent final output is invalid")
    try:
        workflow = value.get("workflow", "engineering")
        if workflow not in {"engineering", "new_project_planning"}:
            raise ValueError("workflow")
        plan_status = value.get("plan_status")
        if plan_status not in {None, "needs_clarification", "ready_for_review"}:
            raise ValueError("plan_status")
        content = value["content"]
        model = value["model"]
        if not isinstance(content, str) or not isinstance(model, str):
            raise TypeError("output fields")
        return AgentRunOutput(
            content=content,
            model=model,
            input_tokens=int(value["input_tokens"]),
            output_tokens=int(value["output_tokens"]),
            workflow=workflow,
            plan_status=plan_status,
            project_name=value.get("project_name") if isinstance(value.get("project_name"), str) else None,
            plan_artifact=value.get("plan_artifact") if isinstance(value.get("plan_artifact"), dict) else None,
            plan_digest=value.get("plan_digest") if isinstance(value.get("plan_digest"), str) else None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AgentProtocolError("Agent final output is invalid") from exc
