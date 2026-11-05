"""Run-scoped authority for preparation followed by the existing native owner."""
from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from controlstudio_contracts import AgentToolDefinitionV1
from controlstudio_contracts.native_ld_commands import (
    native_ld_declared_rungs_schema,
    validate_native_ld_rungs,
)

from .mcp_client import MCPToolError, MCPToolPreflightError
from .native_workspace_host import NativeWorkspaceExecution, _rung_unknown


def native_project_name(display_name: str) -> str:
    return re.sub(r"\s+", "_", display_name)


def validate_preparation_display_name(reservation: dict[str, Any], display_name: str) -> None:
    """Reject another request title; this metadata grants no native authority."""
    if (not isinstance(reservation, dict)
            or ("display_name" in reservation and reservation["display_name"] != display_name)
            or ("display_name" not in reservation
                and reservation.get("project_name") != native_project_name(display_name))):
        raise MCPToolError("Native preparation belongs to a different project request")


def _native_cpu_selection(result: dict[str, Any]) -> dict[str, Any]:
    selection = result.get('cpu_selection')
    if (isinstance(selection, dict) and selection.get('source') == 'native_current_selection'
            and all(isinstance(selection.get(key), str) and 1 <= len(selection[key]) <= 80
                    for key in ('cpu_series', 'cpu_type'))):
        return {'selected_cpu_series': selection['cpu_series'], 'selected_cpu_type': selection['cpu_type'],
                'cpu_selection_source': selection['source']}
    return {}


def public_preparation_failure(result: dict[str, Any]) -> dict[str, Any]:
    """Field receipts include the private destination text; project only facts."""
    raw_error = result.get("error") if isinstance(result.get("error"), dict) else {}
    code = raw_error.get("kind")
    if not isinstance(code, str) or not code or not all(c.isalnum() or c == "_" for c in code):
        code = "native_preparation_failed"
    public = {"ok": False, "error": {"kind": code[:120]}, "automatic_replay": False,
              "input_state_unknown": result.get("input_state_unknown") is True,
              "creation_input_attempted": result.get("creation_input_attempted") is True,
              **_native_cpu_selection(result)}
    details = raw_error.get("details")
    invalid_fields = result.get("invalid_fields")
    if not isinstance(invalid_fields, list) and isinstance(details, dict):
        invalid_fields = details.get("invalid_fields")
    if isinstance(invalid_fields, list) and all(key in {"project_name", "plc_name", "program_name"} for key in invalid_fields):
        public["invalid_fields"] = list(invalid_fields)
    if code == "NATIVE_PREPARATION_CHOICE_UNAVAILABLE" and isinstance(details, dict):
        choices = details.get("choices")
        if isinstance(choices, list) and len(choices) <= 256 and all(isinstance(text, str) and len(text) <= 80 for text in choices):
            public["native_choices"] = list(choices)
    steps = []
    receipts = result.get("preparation_receipts")
    for row in receipts if isinstance(receipts, list) else []:
        if not isinstance(row, dict) or row.get("action") not in {"activate", "key", "save", "set_field", "native_choice", "accept_new_project", "open_program"}:
            continue
        receipt = row.get("receipt") if isinstance(row.get("receipt"), dict) else row
        fact = {"action": row["action"]}
        if type(receipt.get("action_attempted")) is bool:
            fact["action_attempted"] = receipt["action_attempted"]
        if type(receipt.get("input_events_inserted")) is int and receipt["input_events_inserted"] >= 0:
            fact["input_events_inserted"] = receipt["input_events_inserted"]
        selected = row.get("result")
        if row["action"] == "native_choice" and isinstance(selected, dict):
            fact["choice_verified"] = selected.get("verified") is True
        steps.append(fact)
    public["preparation_receipts"] = steps
    cancellation_steps = []
    for row in receipts if isinstance(receipts, list) else []:
        if not isinstance(row, dict) or row.get("phase") not in {"acknowledge_name_validation", "cancel_failed_project_form"}:
            continue
        cancellation_steps.append({key: row[key] for key in ("phase", "action_attempted", "input_state_unknown", "closed") if key in row})
    if cancellation_steps:
        public["cancellation_receipts"] = cancellation_steps
    return public


class NativeProjectPreparation(NativeWorkspaceExecution):
    def __init__(self, *, reservation: dict[str, Any], frame: dict[str, Any], initial_target: dict[str, Any] | None,
                 on_prepared: Any, on_cancelled: Any = None, idle_frame_authority: bool = False, **kwargs: Any):
        super().__init__(target={**reservation, **frame}, **kwargs)
        self.reservation, self.frame = deepcopy(reservation), deepcopy(frame)
        self.initial_target = deepcopy(initial_target)
        self.on_prepared = on_prepared
        self.on_cancelled = on_cancelled
        self.idle_frame_authority = idle_frame_authority
        self.prepared = False

    def _check_target(self) -> None:
        if self.prepared:
            return super()._check_target()
        if self.current_target() != self.initial_target:
            raise MCPToolError("Native preparation initial target changed")

    def tool_catalog(self) -> list[AgentToolDefinitionV1]:
        return [*super().tool_catalog(), *[AgentToolDefinitionV1(tool_name=name,
            description=description,
            parameters_json_schema={"type": "object", "additionalProperties": False,
                "properties": {**{key: {"type": "string", "minLength": 1, "maxLength": 80}
                               for key in ("cpu_series", "cpu_type", "plc_name", "program_name")},
                               **({'use_native_cpu_selection': {'type': 'boolean'}, 'continue_existing': {'type': 'boolean'},
                                   'rungs': native_ld_declared_rungs_schema()}
                                  if name == 'prepare_project' else {})},
                "required": ["cpu_series", "plc_name", "program_name"] + ([] if name == "prepare_project" else ["cpu_type"])}) for name, description in (
                    ("prepare_project", "Omit cpu_type to observe native choices, then select an exact cpu_type suited to the requested equipment and simulator. Never replay unknown creation."),
                    ("inspect_project_preparation", "Read the preparation journal state and pending creation facts using the original CPU/PLC/program fields; does not input or clear replay guards."),
                    ("cancel_project_preparation", "Explicitly cancel only a freshly proved vendor name-validation failure. It preserves the attempt and requires a separate new prepare_project call."),
                )]]

    async def _execute(self, name: str, arguments: dict[str, Any], *,
                       expected_simulator_sha256: str | None = None,
                       persist_observation: bool = True) -> dict[str, Any]:
        display = ({"display_name": self.reservation["display_name"]}
                   if "display_name" in self.reservation else {})
        preparation_tools = {"prepare_project": "workspace.prepare_native",
                             "inspect_project_preparation": "workspace.inspect_native_preparation",
                             "cancel_project_preparation": "workspace.cancel_native_preparation"}
        if name not in preparation_tools:
            if not self.prepared:
                self._check_target()
                return {"ok": False, "error": {"kind": "native_project_not_prepared", "action_attempted": False}}
            return await super()._execute(name, arguments,
                expected_simulator_sha256=expected_simulator_sha256,
                persist_observation=persist_observation)
        self._check_target()
        if self.prepared and name != "prepare_project":
            return {"ok": False, "error": {"kind": "native_project_already_prepared", "action_attempted": False}}
        if self.prepared:
            return {"ok": True, "already_prepared": True, "program_names": [self.program_name],
                    "workspace_id": self.target["workspace_id"], "revision_id": self.target["revision_id"], **display}
        fields = {"cpu_series", "cpu_type", "plc_name", "program_name"}
        required = fields - {"cpu_type"} if name == "prepare_project" else fields
        allowed = fields | ({'use_native_cpu_selection', 'rungs', 'continue_existing'} if name == 'prepare_project' else set())
        if (not required <= set(arguments) or not set(arguments) <= allowed
                or any(not isinstance(arguments[key], str) or not arguments[key].strip()
                       or len(arguments[key]) > 80 for key in arguments if key in fields)
                or 'use_native_cpu_selection' in arguments and type(arguments['use_native_cpu_selection']) is not bool
                or 'continue_existing' in arguments and type(arguments['continue_existing']) is not bool):
            raise MCPToolError("Native preparation fields are invalid")
        rungs = arguments.get('rungs')
        seed = None
        if 'rungs' in arguments:
            try:
                validate_native_ld_rungs(rungs)
            except ValueError as error:
                raise MCPToolPreflightError('EDITOR_BATCH_ARGUMENTS_INVALID', str(error),
                                            details={'invalid_fields': ['rungs']}) from error
            seed = next((contact for rung in rungs for branch in rung['branches'] for contact in branch), None)
            if seed is None:
                operand = next((rung['terminal']['operand'] for rung in rungs
                                if rung['terminal']['kind'] in {'OUT', 'SET', 'RST'}), None)
                if operand is None:
                    raise MCPToolPreflightError('EDITOR_BATCH_ARGUMENTS_INVALID',
                        'A contact or coil operand is required to initialize the native program seed.',
                        details={'invalid_fields': ['rungs']})
                seed = {'kind': 'NO', 'operand': operand}
        request = {**{key: value for key, value in arguments.items() if key != 'rungs'},
                   **{key: self.reservation[key] for key in ("workspace_id", "revision_id", "project_name")},
                   **self.frame}
        if not self.idle_frame_authority and self.initial_target and all(self.initial_target.get(key) for key in ("project_path", "project_hash", "session_id")):
            request.update(previous_project_path=self.initial_target["project_path"],
                           previous_project_hash=self.initial_target["project_hash"])
        if name == "cancel_project_preparation":
            request["replacement_project_name"] = native_project_name(self.reservation["project_name"])
        result = await self.gateway.call_workspace_tool(preparation_tools[name], request)
        self.record_raw(name, request, result)
        if result.get("ok") is not True:
            # Structured partial outcomes remain available to the model; no retry.
            return public_preparation_failure(result)
        if name == "prepare_project" and result.get('preparation_state') == 'cpu_selection_required':
            choices, series = result.get('native_choices'), result.get('cpu_series_choices')
            if ('cpu_type' in arguments or result.get('prepared') is not False
                    or result.get('creation_input_attempted') is not False or result.get('input_state_unknown') is not False
                    or result.get('requires_explicit_prepare') is not True or result.get('automatic_replay') is not False
                    or any(not isinstance(values, list) or len(values) > 256
                           or any(not isinstance(value, str) or len(value) > 512 for value in values)
                           for values in (choices, series))
                    or result.get('selected_cpu_series') != arguments['cpu_series']
                    or result.get('selected_cpu_series') not in series
                    or result.get('selected_cpu_type') is not None and result['selected_cpu_type'] not in choices):
                raise MCPToolError("Native CPU choices did not verify an uncreated selection phase")
            return {**{key: deepcopy(result[key]) for key in ('ok', 'preparation_state', 'prepared',
                'creation_input_attempted', 'input_state_unknown', 'automatic_replay', 'requires_explicit_prepare',
                'native_choices', 'cpu_series_choices', 'selected_cpu_series', 'selected_cpu_type')},
                'workspace_id': self.reservation['workspace_id'], 'revision_id': self.reservation['revision_id'], **display}
        if name == "inspect_project_preparation" and result.get("outcome") in {"not_dispatched", "setup_failed"}:
            state = "reserved" if result["outcome"] == "not_dispatched" else "setup_failed"
            if (result.get("read_only") is not True or result.get("preparation_state") != state
                    or result.get("prepared") is not False or result.get("creation_input_attempted") is not False
                    or result.get("input_state_unknown") is not False or result.get("destination_absent") is not True
                    or result.get("reserved_project_files_absent") is not True or result.get("prepare_available") is not True
                    or result.get("fresh_attempt_required") is not True or result.get("automatic_replay") is not False
                    or result.get("creation_replayed") is not False):
                raise MCPToolError("Preparation inspection did not prove an uncreated reservation")
            public = {key: deepcopy(result[key]) for key in ("ok", "read_only", "outcome", "preparation_state", "prepared",
                "creation_input_attempted", "input_state_unknown", "destination_absent", "reserved_project_files_absent",
                "prepare_available", "fresh_attempt_required", "automatic_replay", "creation_replayed")}
            if state == "setup_failed":
                failure = result.get("failure")
                if (not isinstance(failure, dict) or failure.get("ok") is not False
                        or failure.get("creation_input_attempted") is not False or failure.get("input_state_unknown") is not False):
                    raise MCPToolError("Preparation inspection did not retain its known setup failure")
                public["failure"] = public_preparation_failure(failure)
            return public
        if name == "inspect_project_preparation" and result.get("outcome") == "created_pending_save":
            if (result.get('read_only') is not True or result.get('continuation_available') is not True
                    or result.get('input_state_unknown') is not True or result.get('automatic_replay') is not False
                    or result.get('creation_replayed') is not False):
                raise MCPToolError('Preparation inspection did not prove the pending created target')
            return {**{key: deepcopy(result[key]) for key in ('ok', 'read_only', 'outcome',
                'continuation_available', 'input_state_unknown', 'automatic_replay', 'creation_replayed')},
                'prepared': False}
        if name == "inspect_project_preparation" and result.get("outcome") != "cancelled":
            if (result.get("read_only") is not True or result.get("outcome") != "vendor_validation_rejected"
                    or result.get("verified_not_created") is not True or result.get("destination_absent") is not True):
                raise MCPToolError("Preparation inspection did not prove a rejected, uncreated project")
            return {key: deepcopy(result[key]) for key in ("ok", "read_only", "outcome", "verified_not_created",
                "destination_absent", "reserved_project_files_absent", "invalid_fields", "automatic_replay",
                "creation_replayed", "fresh_attempt_required") if key in result}
        if name == "cancel_project_preparation" or (name == "inspect_project_preparation" and result.get("outcome") == "cancelled"):
            reservation, frame = result.get("reservation"), result.get("frame")
            replacement = native_project_name(self.reservation["project_name"])
            if (result.get("cancelled") is not True or result.get("idle_frame_verified") is not True
                    or result.get("verified_not_created") is not True or result.get("attempt_history_preserved") is not True
                    or result.get("remove_previous_project_binding_for_fresh_attempt") is not True
                    or not isinstance(reservation, dict) or not isinstance(frame, dict)
                    or any(reservation.get(key) != self.reservation[key] for key in ("workspace_id", "revision_id"))
                    or reservation.get("project_name") != replacement
                    or reservation.get("display_name") != self.reservation.get("display_name")
                    or reservation.get("project_path") != str(Path(self.reservation["project_path"]).with_name(f"{replacement}.xgwx"))
                    or any(frame.get(key) != self.frame[key] for key in ("pid", "hwnd", "expected_process_create_time"))):
                raise MCPToolError("Preparation cancellation did not verify its reserved idle target")
            if self.on_cancelled is None:
                raise MCPToolError("Preparation cancellation persistence is unavailable")
            await self.on_cancelled(result)
            self.reservation, self.frame = deepcopy(reservation), deepcopy(frame)
            self.idle_frame_authority = True
            self.target = {**self.reservation, **self.frame}
            self._check_target()
            return {"ok": True, "cancelled": True, "prepared": False, "verified_not_created": True,
                    "read_only": name == "inspect_project_preparation",
                    "fresh_attempt_required": True, "automatic_replay": False, "creation_replayed": False,
                    "attempt_history_preserved": True, "project_name": reservation["project_name"],
                    "workspace_id": reservation["workspace_id"], "revision_id": reservation["revision_id"], **display}
        workspace, session = result.get("workspace"), result.get("session")
        selection = _native_cpu_selection(result)
        if (name == 'prepare_project' and arguments.get('use_native_cpu_selection') is True
                and 'cpu_type' not in arguments
                and selection.get('selected_cpu_series') != arguments['cpu_series']):
            raise MCPToolError('Native preparation did not retain its actual CPU selection')
        source = workspace.get("source") if isinstance(workspace, dict) else None
        if (not isinstance(workspace, dict) or not isinstance(session, dict)
                or not isinstance(source, dict) or source.get("display_name") != self.reservation.get("display_name")
                or any(workspace.get(key) != self.reservation[key] for key in ("workspace_id", "revision_id"))
                or session.get("project_path") != self.reservation["project_path"]
                or any(session.get(key) != self.frame[key] for key in ("pid", "hwnd"))
                or result.get("program_name") != arguments["program_name"]
                or result.get("saved_project", {}).get("sha256") != session.get("project_hash")):
            raise MCPToolError("Native preparation returned a different target")
        self.target = await self.on_prepared(result)
        self.program_name, self.prepared = result["program_name"], True
        self.captured_source = {key: workspace["source"].get(key) for key in ("captured_path", "sha256")}
        self._check_target()
        public = {"ok": True, "workspace_id": workspace["workspace_id"], "revision_id": workspace["revision_id"],
                "program_name": result["program_name"], "program_names": result["program_names"],
                "saved_project": result["saved_project"], "source": "native_creation_and_saved_program_verification",
                **selection, **display}
        if rungs is not None:
            # Baseline and actual working binding are committed above. These
            # existing edit routes never run against the captured source.
            public.update(prepared=True, creation_completed=True)
            public['creation_saved_project'] = public.pop('saved_project')
            construction: dict[str, Any] = {'ok': False, 'stage': 'initialization'}
            public['construction'] = construction
            try:
                initialized = await super()._execute('initialize_ld_program', {
                    'program_name': self.program_name, 'element_kind': seed['kind'], 'operand': seed['operand']})
                construction['initialization'] = initialized
                if initialized.get('ok') is True and initialized.get('element_input', {}).get('native_readback_verified') is True:
                    from controlstudio_contracts.native_ld_commands import native_ld_source_sha256
                    rows = initialized.get('ld', {}).get('decoded', {}).get('rows')
                    source_sha = native_ld_source_sha256(rows)
                    construction['stage'] = 'declaration'
                    built = await super()._execute('native_editor', {'action': 'apply_ld_edits',
                        'program_name': self.program_name, 'capture_after': True, 'entries': [{
                            'action': 'write_ld_program', 'rungs': deepcopy(rungs),
                            'expected_source_sha256': source_sha, 'check_and_save': True}]})
                    construction['declaration'] = built
                    if built.get('ok') is True:
                        construction['stage'] = 'saved_observation'
                        observed = built.get('saved_project')
                        if not isinstance(observed, dict):
                            observed = await self._read_saved_project({})
                        construction['saved_project'] = observed
                        public['saved_project'] = observed
                        construction['ok'] = (observed.get('exists') is True and observed.get('stable_during_read') is True
                            and isinstance(observed.get('sha256'), str) and re.fullmatch('[0-9a-f]{64}', observed['sha256']) is not None)
                        if construction['ok']:
                            construction['stage'] = 'completed'
                else:
                    construction['input_state_unknown'] = _rung_unknown(initialized)
            except Exception as error:
                construction.update(ok=False, error={'kind': type(error).__name__, 'message': str(error)},
                                    input_state_unknown=construction['stage'] != 'saved_observation', automatic_replay=False)
            public['ok'] = construction['ok']
            public['input_state_unknown'] = _rung_unknown(construction)
        if self.current_recovery_admission is not None:
            generation = self.current_recovery_admission(self)
            if generation is not None:
                public['native_binding_generation'] = generation
        return public
