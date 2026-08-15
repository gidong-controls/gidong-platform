from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from typing import Literal

SignalDirection = Literal[
    "request",
    "feedback",
    "permissive",
    "command",
    "state",
    "measurement",
    "fault",
    "event",
]


@dataclass(frozen=True, slots=True)
class SignalRole:
    role_id: str
    direction: SignalDirection
    description: str
    safety_relevant: bool = False


@dataclass(frozen=True, slots=True)
class RequiredDecision:
    decision_id: str
    prompt: str
    blocker_if_unresolved: bool = True


@dataclass(frozen=True, slots=True)
class VerificationScenario:
    scenario_id: str
    setup: str
    action: str
    expected: str


@dataclass(frozen=True, slots=True)
class ControlPattern:
    pattern_id: str
    revision: str
    title: str
    equipment_kind: str
    intent: str
    search_terms: tuple[str, ...]
    required_signal_roles: tuple[SignalRole, ...]
    optional_signal_roles: tuple[SignalRole, ...]
    required_decisions: tuple[RequiredDecision, ...]
    invariants: tuple[str, ...]
    alarm_expectations: tuple[str, ...]
    recovery_expectations: tuple[str, ...]
    required_questions: tuple[str, ...]
    verification_scenarios: tuple[VerificationScenario, ...]
    safety_boundary: str
    source_note: str
    trust: str = "curated_design_guidance"

    @property
    def blocking_decision_ids(self) -> tuple[str, ...]:
        return tuple(
            decision.decision_id
            for decision in self.required_decisions
            if decision.blocker_if_unresolved
        )


_COMMON_REQUIRED_DECISIONS = (
    RequiredDecision(
        "actual_io_mapping",
        "Map every required role to the verified project symbol and actual field I/O or "
        "internal interface; do not invent device addresses.",
    ),
    RequiredDecision(
        "signal_polarity_and_normal_state",
        "Confirm electrical and logical polarity, normal state, disconnected state, and "
        "faulted state for every field signal.",
    ),
    RequiredDecision(
        "timing_and_timeout_requirements",
        "Derive delays, filters, debounce, timeout, and monitoring windows from machine "
        "evidence and vendor limits; no generic time value is safe by default.",
    ),
    RequiredDecision(
        "deenergized_or_commanded_safe_state",
        "Confirm the risk-assessed safe output and commanded state for stop, fault, "
        "communications loss, restart, and power recovery.",
    ),
    RequiredDecision(
        "safety_function_ownership",
        "Identify safety functions owned by safety relays, a safety PLC, drives, or "
        "mechanical protection and define only the status interfaces consumed here.",
    ),
)

_COMMON_REQUIRED_QUESTIONS = (
    "Which verified project symbols and physical channels implement every required role?",
    "What polarity and normal, disconnected, and failed state applies to each signal?",
    (
        "Which timing, timeout, filter, or debounce values are justified by measured behavior "
        "and vendor documentation?"
    ),
    (
        "What is the risk-assessed safe state on stop, fault, communications loss, restart, "
        "and power recovery?"
    ),
    (
        "Which independent safety functions exist, and which read-only safety status may the "
        "standard PLC logic consume?"
    ),
)

_SAFETY_BOUNDARY = (
    "Vendor-neutral engineering guidance only. It neither generates nor claims a native "
    "LS ELECTRIC, Mitsubishi Electric, or Rockwell Automation binding. It cannot replace "
    "a safety PLC, safety relay, machine risk assessment, guarding, electrical design, "
    "vendor instructions, commissioning tests, or qualified human approval."
)

_SOURCE_NOTE = (
    "Curated ControlStudio design guidance. Validate it against the selected vendor and "
    "CPU manuals, project standards, electrical drawings, machine risk assessment, and "
    "the observed machine before implementation."
)


def _signal(
    role_id: str,
    direction: SignalDirection,
    description: str,
    *,
    safety_relevant: bool = False,
) -> SignalRole:
    return SignalRole(
        role_id=role_id,
        direction=direction,
        description=description,
        safety_relevant=safety_relevant,
    )


def _decision(decision_id: str, prompt: str) -> RequiredDecision:
    return RequiredDecision(decision_id=decision_id, prompt=prompt)


def _scenario(
    scenario_id: str,
    setup: str,
    action: str,
    expected: str,
) -> VerificationScenario:
    return VerificationScenario(
        scenario_id=scenario_id,
        setup=setup,
        action=action,
        expected=expected,
    )


def _pattern(
    *,
    pattern_id: str,
    title: str,
    equipment_kind: str,
    intent: str,
    search_terms: tuple[str, ...],
    required_signal_roles: tuple[SignalRole, ...],
    optional_signal_roles: tuple[SignalRole, ...],
    required_decisions: tuple[RequiredDecision, ...],
    invariants: tuple[str, ...],
    alarm_expectations: tuple[str, ...],
    recovery_expectations: tuple[str, ...],
    required_questions: tuple[str, ...],
    verification_scenarios: tuple[VerificationScenario, ...],
) -> ControlPattern:
    return ControlPattern(
        pattern_id=pattern_id,
        revision="1.0.0",
        title=title,
        equipment_kind=equipment_kind,
        intent=intent,
        search_terms=search_terms,
        required_signal_roles=required_signal_roles,
        optional_signal_roles=optional_signal_roles,
        required_decisions=_COMMON_REQUIRED_DECISIONS + required_decisions,
        invariants=invariants,
        alarm_expectations=alarm_expectations,
        recovery_expectations=recovery_expectations,
        required_questions=_COMMON_REQUIRED_QUESTIONS + required_questions,
        verification_scenarios=verification_scenarios,
        safety_boundary=_SAFETY_BOUNDARY,
        source_note=_SOURCE_NOTE,
    )


_BUILTIN_PATTERNS = (
    _pattern(
        pattern_id="motor.dol",
        title="Direct-on-line motor control",
        equipment_kind="motor",
        intent=(
            "Coordinate a maintained motor run request, permissives, starter command, "
            "feedback monitoring, fault reporting, and deliberate recovery."
        ),
        search_terms=("motor", "starter", "contactor", "DOL", "모터", "직입 기동"),
        required_signal_roles=(
            _signal("run_request", "request", "Process or operator demand for the motor."),
            _signal(
                "process_permissive",
                "permissive",
                "Aggregated non-safety process conditions required to run.",
            ),
            _signal(
                "safety_status_ok",
                "permissive",
                "Read-only status from the independent safety system.",
                safety_relevant=True,
            ),
            _signal(
                "overload_healthy",
                "feedback",
                "Verified overload or protection-device healthy indication.",
                safety_relevant=True,
            ),
            _signal(
                "starter_command",
                "command",
                "Command presented to the motor starter interface.",
                safety_relevant=True,
            ),
        ),
        optional_signal_roles=(
            _signal("running_feedback", "feedback", "Starter auxiliary or run feedback."),
            _signal("stop_request", "request", "Explicit process or operator stop demand."),
            _signal("reset_request", "request", "Deliberate request to reset a clearable fault."),
            _signal("local_control_active", "state", "Local control ownership indication."),
        ),
        required_decisions=(
            _decision(
                "restart_policy",
                "Choose whether a recovered run request requires a new operator/process action.",
            ),
            _decision(
                "feedback_monitoring_policy",
                "Define start and stop feedback expectations when physical feedback exists.",
            ),
        ),
        invariants=(
            (
                "A stop, lost permissive, unhealthy overload, or lost safety status dominates a "
                "run request."
            ),
            "The starter command is never asserted unless every required permissive is valid.",
            "A power or controller restart cannot cause an unreviewed automatic restart.",
        ),
        alarm_expectations=(
            "Distinguish protection trip, permissive loss, and command-versus-feedback mismatch.",
            "Report whether the motor failed to start or failed to stop when feedback exists.",
        ),
        recovery_expectations=(
            "Remove the starter command before accepting a reset.",
            "Require the fault cause to be absent and apply the confirmed restart policy.",
        ),
        required_questions=(
            "Is physical running feedback available and independent of the command?",
            "Who owns start/stop authority in automatic, manual, local, and maintenance modes?",
        ),
        verification_scenarios=(
            _scenario(
                "start_with_all_permissives",
                "All confirmed permissives are valid and no fault is active.",
                "Issue a run request.",
                "The starter command follows the approved sequence and feedback is monitored.",
            ),
            _scenario(
                "trip_while_running",
                "The motor is running.",
                "Make the overload indication unhealthy.",
                "The command reaches the confirmed safe state and a protection trip is retained.",
            ),
            _scenario(
                "power_recovery",
                "A run request existed before controller power loss.",
                "Restore power and valid inputs.",
                "Restart behavior exactly matches the reviewed restart policy.",
            ),
        ),
    ),
    _pattern(
        pattern_id="motor.reversing",
        title="Reversing motor control",
        equipment_kind="motor",
        intent=(
            "Coordinate forward and reverse requests with electrical/mechanical interlock "
            "assumptions, transition control, feedback monitoring, and fault recovery."
        ),
        search_terms=(
            "reversing motor",
            "forward reverse",
            "contactor",
            "정역 모터",
            "정회전 역회전",
        ),
        required_signal_roles=(
            _signal("forward_request", "request", "Demand for forward rotation."),
            _signal("reverse_request", "request", "Demand for reverse rotation."),
            _signal(
                "process_permissive",
                "permissive",
                "Aggregated non-safety process conditions for either direction.",
            ),
            _signal(
                "protection_healthy",
                "feedback",
                "Protection-device healthy indication.",
                safety_relevant=True,
            ),
            _signal(
                "forward_command",
                "command",
                "Command to the forward starter interface.",
                safety_relevant=True,
            ),
            _signal(
                "reverse_command",
                "command",
                "Command to the reverse starter interface.",
                safety_relevant=True,
            ),
        ),
        optional_signal_roles=(
            _signal("forward_feedback", "feedback", "Forward starter auxiliary feedback."),
            _signal("reverse_feedback", "feedback", "Reverse starter auxiliary feedback."),
            _signal(
                "zero_speed_feedback",
                "feedback",
                "Independent indication that direction reversal is mechanically permitted.",
                safety_relevant=True,
            ),
            _signal("reset_request", "request", "Deliberate clearable-fault reset request."),
        ),
        required_decisions=(
            _decision(
                "direction_transition_policy",
                "Define how an opposite-direction request stops, proves rest, and changes direction.",
            ),
            _decision(
                "hardware_interlock_evidence",
                "Verify the independent electrical and mechanical contactor interlocks.",
            ),
            _decision(
                "conflicting_request_policy",
                "Choose the deterministic response to simultaneous direction requests.",
            ),
        ),
        invariants=(
            "Forward and reverse commands are mutually exclusive in every scan and mode.",
            "Software exclusion supplements but never replaces physical contactor interlocking.",
            "A direction transition cannot begin until the reviewed stopped-state proof is met.",
        ),
        alarm_expectations=(
            "Detect conflicting requests and contradictory direction feedback.",
            "Distinguish failure to release the old direction from failure to engage the new one.",
        ),
        recovery_expectations=(
            "Return both commands to the confirmed safe state before reset or direction retry.",
            "Require fresh direction ownership after an interlock or feedback fault.",
        ),
        required_questions=(
            "What proves the motor and load can safely reverse?",
            "Are mechanically linked contactors and cross-wired auxiliary interlocks verified?",
        ),
        verification_scenarios=(
            _scenario(
                "mutual_exclusion",
                "Either direction may be requested.",
                "Exercise every direction-request combination.",
                "The two direction commands are never true together.",
            ),
            _scenario(
                "reverse_while_forward",
                "Forward running is confirmed.",
                "Request reverse.",
                "Forward releases, stopped-state proof is met, then reverse may engage.",
            ),
            _scenario(
                "stuck_contactor",
                "One command is removed.",
                "Keep its feedback active.",
                "The opposite command remains inhibited and a specific mismatch alarm is retained.",
            ),
        ),
    ),
    _pattern(
        pattern_id="cylinder.double_solenoid",
        title="Double-solenoid cylinder control",
        equipment_kind="pneumatic_or_hydraulic_cylinder",
        intent=(
            "Coordinate extend and retract requests, mutually exclusive valve commands, end "
            "position evidence, movement monitoring, and explicit recovery."
        ),
        search_terms=("cylinder", "solenoid", "extend retract", "실린더", "전진 후진"),
        required_signal_roles=(
            _signal("extend_request", "request", "Demand for the extended position."),
            _signal("retract_request", "request", "Demand for the retracted position."),
            _signal(
                "motion_permissive",
                "permissive",
                "Aggregated conditions allowing cylinder movement.",
                safety_relevant=True,
            ),
            _signal(
                "extended_feedback",
                "feedback",
                "Verified extended end-position indication.",
            ),
            _signal(
                "retracted_feedback",
                "feedback",
                "Verified retracted end-position indication.",
            ),
            _signal(
                "extend_command",
                "command",
                "Command to the extend valve interface.",
                safety_relevant=True,
            ),
            _signal(
                "retract_command",
                "command",
                "Command to the retract valve interface.",
                safety_relevant=True,
            ),
        ),
        optional_signal_roles=(
            _signal("pressure_healthy", "feedback", "Pressure-availability indication."),
            _signal("mid_position_feedback", "feedback", "Intermediate-position evidence."),
            _signal("reset_request", "request", "Deliberate motion-fault reset request."),
        ),
        required_decisions=(
            _decision(
                "valve_center_and_power_loss_behavior",
                "Confirm valve construction and physical motion after command or power removal.",
            ),
            _decision(
                "conflicting_request_policy",
                "Choose the deterministic response to simultaneous extend and retract requests.",
            ),
            _decision(
                "motion_completion_policy",
                "Define accepted end-position evidence and movement timeout handling.",
            ),
        ),
        invariants=(
            "Extend and retract commands are mutually exclusive.",
            "Contradictory end-position feedback prevents further automatic movement.",
            "Loss of movement permissive drives commands to the risk-assessed state.",
        ),
        alarm_expectations=(
            "Detect failure to leave the starting position and failure to reach the target.",
            "Detect contradictory end-position sensors and loss of pressure when observable.",
        ),
        recovery_expectations=(
            "Remove or place valve commands in the reviewed safe state before reset.",
            "Require an explicit recovery direction when the physical position is uncertain.",
        ),
        required_questions=(
            "What does the valve and cylinder physically do with both solenoids off?",
            "Can both end sensors be active during normal travel or only during a fault?",
            "Which machine zones can the cylinder occupy or collide with?",
        ),
        verification_scenarios=(
            _scenario(
                "normal_extend_retract",
                "Position and permissives are valid.",
                "Command each direction from the opposite confirmed end.",
                "Only the selected valve command energizes and target feedback completes motion.",
            ),
            _scenario(
                "sensor_contradiction",
                "The cylinder is not intentionally in a sensor-overlap zone.",
                "Assert both end-position indications.",
                "Automatic movement is inhibited and a contradiction alarm is retained.",
            ),
            _scenario(
                "motion_timeout",
                "A move has started.",
                "Withhold target feedback beyond the approved monitoring window.",
                "The command reaches the reviewed state and a direction-specific fault is retained.",
            ),
        ),
    ),
    _pattern(
        pattern_id="drive.vfd",
        title="Variable-frequency drive control",
        equipment_kind="variable_frequency_drive",
        intent=(
            "Coordinate drive enable/run direction, speed reference, readiness, status, fault, "
            "communications health, stopping behavior, and recovery."
        ),
        search_terms=("VFD", "inverter", "variable frequency drive", "인버터", "주파수 드라이브"),
        required_signal_roles=(
            _signal("run_request", "request", "Process demand for driven motion."),
            _signal(
                "drive_permissive",
                "permissive",
                "Aggregated process permission to run.",
                safety_relevant=True,
            ),
            _signal("drive_ready", "feedback", "Drive-reported readiness for commands."),
            _signal("drive_fault", "fault", "Drive-reported active fault status."),
            _signal("speed_reference", "command", "Validated engineering speed demand."),
            _signal(
                "run_command",
                "command",
                "Run or enable command through the selected drive interface.",
                safety_relevant=True,
            ),
        ),
        optional_signal_roles=(
            _signal("direction_request", "request", "Selected direction demand."),
            _signal("running_feedback", "feedback", "Drive running-status indication."),
            _signal("at_speed_feedback", "feedback", "Drive at-reference indication."),
            _signal("communication_healthy", "feedback", "Freshness of the drive control channel."),
            _signal("drive_reset_command", "command", "Qualified drive fault-reset command."),
            _signal(
                "safe_torque_off_status",
                "feedback",
                "Read-only status from independent safe torque off circuits.",
                safety_relevant=True,
            ),
        ),
        required_decisions=(
            _decision(
                "drive_control_interface",
                "Select and verify hardwired, fieldbus, or mixed command/status ownership.",
            ),
            _decision(
                "stop_category_and_ramp",
                "Select stopping behavior using the machine risk assessment and drive manual.",
            ),
            _decision(
                "speed_limits_and_units",
                "Define engineering units, sign, range, clamping, and source ownership.",
            ),
            _decision(
                "communications_loss_behavior",
                "Define command, reference, and restart behavior on stale or lost communications.",
            ),
        ),
        invariants=(
            "A run command is not issued unless readiness, permissives, and reference validity hold.",
            "Speed reference is bounded by reviewed machine and drive limits.",
            "Safe torque off status is observed but never synthesized by standard PLC logic.",
        ),
        alarm_expectations=(
            "Preserve drive fault identity and distinguish it from not-ready or communications loss.",
            "Detect command-versus-running and reference-versus-at-speed mismatch when available.",
        ),
        recovery_expectations=(
            "Remove or make safe the run demand before issuing a qualified drive reset.",
            "Apply the reviewed restart policy after fault or communications recovery.",
        ),
        required_questions=(
            "Which interface owns run, direction, speed, reset, and status?",
            "Which drive parameters implement stop, restart, and communications-loss behavior?",
            "Are speed and direction changes permitted while running?",
        ),
        verification_scenarios=(
            _scenario(
                "normal_run",
                "Drive readiness and process permissives are valid.",
                "Apply a valid run and speed request.",
                "Commands use the selected interface and status reaches the expected state.",
            ),
            _scenario(
                "communications_loss",
                "The drive is controlled over a monitored communications channel.",
                "Make status or command data stale.",
                "The approved stop behavior occurs and automatic restart remains controlled.",
            ),
            _scenario(
                "drive_fault_reset",
                "A drive fault is active.",
                "Clear its cause and request reset.",
                "Reset is issued only under approved conditions and run does not resume unexpectedly.",
            ),
        ),
    ),
    _pattern(
        pattern_id="axis.servo",
        title="Servo axis operation",
        equipment_kind="servo_axis",
        intent=(
            "Coordinate axis ownership, readiness, homing, motion requests, limits, completion, "
            "fault handling, and recovery without replacing certified motion safety."
        ),
        search_terms=("servo", "axis", "motion", "homing", "서보", "축", "원점"),
        required_signal_roles=(
            _signal("motion_request", "request", "Qualified request for a defined axis movement."),
            _signal(
                "axis_ready", "feedback", "Motion-system readiness for the requested operation."
            ),
            _signal("axis_fault", "fault", "Motion-system active fault status."),
            _signal(
                "motion_permissive",
                "permissive",
                "Aggregated process and independent-safety status permitting motion.",
                safety_relevant=True,
            ),
            _signal("axis_enable_command", "command", "Command enabling normal axis operation."),
            _signal("motion_command", "command", "Validated native-adapter motion request."),
            _signal("motion_complete", "feedback", "Completion evidence for the active request."),
        ),
        optional_signal_roles=(
            _signal("homed_state", "state", "Axis reference or home state."),
            _signal("in_position", "feedback", "Position-window status."),
            _signal(
                "positive_limit",
                "feedback",
                "Positive travel-limit indication.",
                safety_relevant=True,
            ),
            _signal(
                "negative_limit",
                "feedback",
                "Negative travel-limit indication.",
                safety_relevant=True,
            ),
            _signal("axis_reset_command", "command", "Qualified axis reset command."),
        ),
        required_decisions=(
            _decision(
                "coordinate_and_unit_contract",
                "Define coordinate system, units, sign, machine limits, and work offsets.",
            ),
            _decision(
                "homing_and_power_recovery_policy",
                "Define when position remains trustworthy and when re-homing is mandatory.",
            ),
            _decision(
                "motion_profile_limits",
                "Obtain reviewed velocity, acceleration, deceleration, jerk, and following limits.",
            ),
            _decision(
                "motion_safety_ownership",
                "Identify certified safe-motion functions and the standard PLC status boundary.",
            ),
        ),
        invariants=(
            "No motion is requested without ownership, readiness, valid coordinates, and permissives.",
            "A new request cannot overwrite an active motion transaction without an explicit policy.",
            "Software position limits do not replace physical limits or certified safe-motion functions.",
        ),
        alarm_expectations=(
            "Retain motion-system fault identity and distinguish reject, timeout, abort, and following error.",
            "Detect loss of trusted position or contradictory limit information.",
        ),
        recovery_expectations=(
            "Resolve motion ownership and physical position before reset or retry.",
            "Require homing when the reviewed position-validity policy says position is untrusted.",
        ),
        required_questions=(
            "Which motion platform and vendor-native commands will the adapter bind?",
            "What proves the axis path and destination are free of collision?",
            "When is axis position valid across enable, fault, stop, and power cycles?",
        ),
        verification_scenarios=(
            _scenario(
                "normal_positioning",
                "Axis position is trusted and all permissives are valid.",
                "Submit a reviewed movement within machine limits.",
                "One transaction executes and completion is tied to its verified outcome.",
            ),
            _scenario(
                "limit_or_permissive_loss",
                "Motion is active.",
                "Lose a relevant limit or permissive condition.",
                "The native motion system performs the reviewed stop and reports a retained cause.",
            ),
            _scenario(
                "power_recovery_position",
                "The controller or drive has restarted.",
                "Attempt a movement before position trust is re-established.",
                "Motion remains blocked until the approved position-validity policy is satisfied.",
            ),
        ),
    ),
    _pattern(
        pattern_id="alarm.lifecycle",
        title="Alarm lifecycle",
        equipment_kind="alarm_management",
        intent=(
            "Represent condition detection, alarm activation, acknowledgement, reset, history, "
            "and operator-visible state without confusing acknowledgement with correction."
        ),
        search_terms=("alarm", "fault", "acknowledge", "reset", "알람", "고장", "확인", "리셋"),
        required_signal_roles=(
            _signal("condition_active", "fault", "The evaluated abnormal process condition."),
            _signal("alarm_active", "state", "Operator-visible active alarm state."),
            _signal("acknowledge_request", "request", "Authenticated acknowledgement request."),
            _signal("reset_request", "request", "Authenticated clear/reset request."),
            _signal("unacknowledged_state", "state", "Alarm has not yet been acknowledged."),
        ),
        optional_signal_roles=(
            _signal(
                "shelve_request", "request", "Governed request to temporarily shelve indication."
            ),
            _signal("first_out_state", "state", "First detected cause in a related fault group."),
            _signal("history_event", "event", "Timestamped alarm lifecycle event."),
            _signal("maintenance_bypass_active", "state", "Visible governed bypass state."),
        ),
        required_decisions=(
            _decision(
                "alarm_latching_policy",
                "Choose active, return-to-normal, acknowledgement, and reset latching semantics.",
            ),
            _decision(
                "alarm_priority_and_group",
                "Assign priority, machine area, operator response, and related-cause grouping.",
            ),
            _decision(
                "shelving_and_bypass_governance",
                "Define whether shelving or bypass is allowed, authorized, timed, and audited.",
            ),
        ),
        invariants=(
            "Acknowledgement records human awareness and never makes an active condition healthy.",
            "Reset cannot clear a condition that remains active unless an explicitly governed policy says so.",
            "Alarm history preserves activation, acknowledgement, return-to-normal, reset, and bypass changes.",
        ),
        alarm_expectations=(
            "The alarm record contains a stable identity, cause, consequence, and expected response.",
            "Related symptom alarms do not hide the retained initiating cause.",
        ),
        recovery_expectations=(
            "Recovery guidance names the physical or logical condition to verify before reset.",
            "A reset does not automatically restart equipment unless separately reviewed and authorized.",
        ),
        required_questions=(
            "What is the condition, consequence, operator response, and alarm priority?",
            "Must the alarm latch after return to normal, and who may acknowledge or reset it?",
            "What evidence is required to declare the initiating cause resolved?",
        ),
        verification_scenarios=(
            _scenario(
                "active_then_acknowledged",
                "A new abnormal condition appears.",
                "Acknowledge it while the condition remains active.",
                "The alarm stays active, becomes acknowledged, and records both events.",
            ),
            _scenario(
                "return_and_reset",
                "A retained alarm has returned to normal.",
                "Issue an authorized reset.",
                "The retained state clears according to policy without causing an equipment restart.",
            ),
            _scenario(
                "power_cycle_history",
                "An alarm is active or retained.",
                "Restart the controller or host.",
                "Persistence and history match the approved alarm retention policy.",
            ),
        ),
    ),
    _pattern(
        pattern_id="operation.mode_manager",
        title="Operating mode manager",
        equipment_kind="machine_operation_mode",
        intent=(
            "Provide explicit ownership and transition rules for automatic, manual, maintenance, "
            "local, or other project-defined modes."
        ),
        search_terms=("mode", "automatic manual", "maintenance", "운전 모드", "자동 수동 보전"),
        required_signal_roles=(
            _signal("mode_request", "request", "Authenticated request for a project-defined mode."),
            _signal(
                "mode_transition_permissive",
                "permissive",
                "Conditions required to leave the current mode safely.",
                safety_relevant=True,
            ),
            _signal("active_mode", "state", "Single authoritative current operating mode."),
            _signal("mode_change_in_progress", "state", "A controlled mode transition is active."),
        ),
        optional_signal_roles=(
            _signal(
                "local_selector_state", "state", "Hardwired or device-local ownership selector."
            ),
            _signal("operator_identity", "state", "Authenticated actor and permission context."),
            _signal("mode_change_rejected", "event", "Reason a requested transition was rejected."),
        ),
        required_decisions=(
            _decision(
                "mode_set_and_authority",
                "Define the exact mode set and who or what may request each mode.",
            ),
            _decision(
                "mode_transition_matrix",
                "Define allowed transitions, handover conditions, and required intermediate states.",
            ),
            _decision(
                "outputs_across_mode_change",
                "Define output ownership and retained or reset sequence state for every transition.",
            ),
        ),
        invariants=(
            "Exactly one authoritative operating mode is active.",
            "Changing mode never bypasses safety functions or silently transfers output ownership.",
            "A rejected or incomplete transition leaves the machine in a defined, observable state.",
        ),
        alarm_expectations=(
            "Report conflicting selectors, unauthorized requests, and transition timeout separately.",
            "Make prolonged maintenance or bypass-related modes visible and auditable.",
        ),
        recovery_expectations=(
            "Recover to a reviewed mode based on physical state, not only a retained software flag.",
            "Require explicit ownership after controller restart when authority is ambiguous.",
        ),
        required_questions=(
            "Which modes exist and which output actions are allowed in each?",
            "Who owns commands when local selectors, HMI, and automatic sequence disagree?",
            "What sequence state is preserved or reinitialized at each mode transition?",
        ),
        verification_scenarios=(
            _scenario(
                "authorized_transition",
                "Current state satisfies the transition matrix.",
                "An authorized actor requests another mode.",
                "Ownership transfers once, all conditions are recorded, and one mode becomes active.",
            ),
            _scenario(
                "conflicting_authority",
                "Two command sources request incompatible modes.",
                "Present both requests together.",
                "The reviewed precedence rule applies and the conflict remains visible.",
            ),
            _scenario(
                "mode_change_during_sequence",
                "An automatic sequence is active.",
                "Request a mode that changes output ownership.",
                "The approved handover and sequence-state policy executes without an uncontrolled output.",
            ),
        ),
    ),
    _pattern(
        pattern_id="initialization_recovery",
        title="Initialization and recovery",
        equipment_kind="machine_lifecycle",
        intent=(
            "Reconstruct a trustworthy machine state after startup, interruption, fault, or "
            "uncertain position before normal operation resumes."
        ),
        search_terms=(
            "initialization",
            "recovery",
            "restart",
            "startup",
            "초기화",
            "복구",
            "재기동",
        ),
        required_signal_roles=(
            _signal(
                "initialize_request", "request", "Authorized request to establish initial state."
            ),
            _signal(
                "recovery_request", "request", "Authorized request to recover from interruption."
            ),
            _signal(
                "safety_status_ok",
                "permissive",
                "Read-only independent safety-system status.",
                safety_relevant=True,
            ),
            _signal(
                "state_evidence_valid", "feedback", "Physical-state evidence is fresh and coherent."
            ),
            _signal("lifecycle_state", "state", "Current initialization or recovery phase."),
            _signal("ready_for_operation", "state", "Verified readiness for normal operation."),
        ),
        optional_signal_roles=(
            _signal("home_request", "command", "Qualified request to home relevant equipment."),
            _signal(
                "manual_recovery_complete", "event", "Operator confirmation of a governed step."
            ),
            _signal("restart_inhibit", "state", "Retained reason normal restart is blocked."),
        ),
        required_decisions=(
            _decision(
                "trusted_state_evidence",
                "Define which sensor, drive, retained, and operator evidence establishes physical state.",
            ),
            _decision(
                "recovery_path_by_uncertainty",
                "Define recovery paths for known state, inconsistent state, and unknown state.",
            ),
            _decision(
                "retentive_state_policy",
                "Classify retained values as trustworthy, advisory, or invalid after each interruption.",
            ),
        ),
        invariants=(
            "Ready-for-operation is false until required physical evidence is coherent.",
            "Retained software state alone never proves actuator position or a collision-free machine.",
            "Recovery actions are ordered to avoid creating a new hazard while resolving uncertainty.",
        ),
        alarm_expectations=(
            "Distinguish missing evidence, contradictory state, failed recovery action, and operator wait.",
            "Retain the reason initialization cannot advance.",
        ),
        recovery_expectations=(
            "Offer only the recovery path justified by current physical evidence.",
            "Require deliberate confirmation before any motion whose starting state is uncertain.",
        ),
        required_questions=(
            "Which physical states must be known before automatic operation?",
            "Which values survive power loss, and which of them remain trustworthy?",
            "What collision-free order establishes home or a recoverable position?",
        ),
        verification_scenarios=(
            _scenario(
                "normal_startup",
                "All required physical evidence is valid and coherent.",
                "Request initialization.",
                "The lifecycle reaches ready only after every reviewed condition is proven.",
            ),
            _scenario(
                "contradictory_state",
                "Two required pieces of physical evidence conflict.",
                "Request initialization or recovery.",
                "Automatic progress is blocked and the contradiction is reported specifically.",
            ),
            _scenario(
                "interrupted_recovery",
                "A recovery action was interrupted.",
                "Resume the controller or workflow.",
                "Physical state is reacquired before any action is repeated.",
            ),
        ),
    ),
    _pattern(
        pattern_id="sequence.step_machine",
        title="Explicit sequence step machine",
        equipment_kind="process_sequence",
        intent=(
            "Represent deterministic sequence state, entry actions, continuous actions, transition "
            "conditions, time monitoring, abort, and recovery."
        ),
        search_terms=("sequence", "step", "state machine", "시퀀스", "스텝", "공정 단계"),
        required_signal_roles=(
            _signal("sequence_start_request", "request", "Qualified request to begin a sequence."),
            _signal(
                "sequence_stop_request", "request", "Request for the reviewed normal stop path."
            ),
            _signal("sequence_abort_request", "request", "Request for the reviewed abort path."),
            _signal(
                "transition_condition", "feedback", "Evidence required for the active transition."
            ),
            _signal("active_step", "state", "Single authoritative sequence step."),
            _signal(
                "sequence_state",
                "state",
                "Idle, running, stopping, complete, faulted, or recovery.",
            ),
        ),
        optional_signal_roles=(
            _signal("hold_request", "request", "Request to hold at a defined boundary."),
            _signal("resume_request", "request", "Request to leave a governed hold."),
            _signal(
                "step_timeout", "fault", "Active step exceeded its reviewed monitoring window."
            ),
            _signal("sequence_complete", "event", "Successful sequence completion event."),
        ),
        required_decisions=(
            _decision(
                "step_and_transition_contract",
                "Define every step, entry action, maintained action, exit condition, and destination.",
            ),
            _decision(
                "stop_hold_abort_semantics",
                "Define how normal stop, hold, abort, and fault differ for every active step.",
            ),
            _decision(
                "sequence_recovery_policy",
                "Define resume, retry, skip prohibition, rollback, or reinitialize behavior per step.",
            ),
        ),
        invariants=(
            "Exactly one authoritative step is active within one sequence instance.",
            "Transitions require explicit evidence and cannot occur solely because a request remained true.",
            "Abort, hold, and stop behavior are defined for every step that can command equipment.",
        ),
        alarm_expectations=(
            "Report the active step, unmet transition evidence, and elapsed monitoring context.",
            "Distinguish process fault, equipment fault, operator hold, and sequence-design inconsistency.",
        ),
        recovery_expectations=(
            "Recovery uses current machine evidence and a step-specific reviewed action.",
            "A controller restart cannot advance or repeat an output action without revalidation.",
        ),
        required_questions=(
            "What entry, continuous, exit, stop, and abort behavior belongs to each step?",
            "Which transition conditions are level, edge, acknowledged event, or transaction completion?",
            "Which steps are safe to resume, retry, or require reinitialization?",
        ),
        verification_scenarios=(
            _scenario(
                "normal_path",
                "The sequence starts from its reviewed initial state.",
                "Satisfy each transition using verified evidence.",
                "Each step is entered once and the expected terminal state is reached.",
            ),
            _scenario(
                "held_transition",
                "A step is active but its transition evidence is absent.",
                "Keep unrelated requests active.",
                "The sequence remains in the step and reports the actual unmet condition.",
            ),
            _scenario(
                "restart_mid_step",
                "Controller execution stops during an output-producing step.",
                "Restore execution.",
                "The step is revalidated or routed to recovery without blind replay or advancement.",
            ),
        ),
    ),
    _pattern(
        pattern_id="interlock.resource_owner",
        title="Exclusive resource ownership interlock",
        equipment_kind="shared_machine_resource",
        intent=(
            "Arbitrate access to a shared zone, actuator, path, material, or process resource with "
            "explicit request, grant, release, timeout, and stale-owner recovery."
        ),
        search_terms=(
            "interlock",
            "resource owner",
            "zone",
            "arbitration",
            "인터록",
            "점유",
            "충돌 방지",
        ),
        required_signal_roles=(
            _signal("owner_request", "request", "Claimant request for the shared resource."),
            _signal(
                "resource_available",
                "permissive",
                "Verified resource availability including external constraints.",
                safety_relevant=True,
            ),
            _signal("owner_grant", "state", "Exclusive grant associated with one claimant."),
            _signal(
                "owner_release", "request", "Claimant declaration that release conditions are met."
            ),
            _signal(
                "resource_occupied",
                "feedback",
                "Physical or process evidence of resource occupation.",
            ),
        ),
        optional_signal_roles=(
            _signal("owner_heartbeat", "feedback", "Freshness evidence for a remote claimant."),
            _signal("claim_priority", "state", "Governed arbitration priority."),
            _signal("forced_release_request", "request", "Governed recovery-only release request."),
            _signal("ownership_event", "event", "Grant, release, reject, or recovery audit event."),
        ),
        required_decisions=(
            _decision(
                "claimant_identity_and_scope",
                "Define all claimants and the exact physical/process boundary of the shared resource.",
            ),
            _decision(
                "arbitration_policy",
                "Define deterministic fairness, priority, queueing, and simultaneous-request behavior.",
            ),
            _decision(
                "release_and_stale_owner_policy",
                "Define evidence for normal release and governed recovery from a lost claimant.",
            ),
        ),
        invariants=(
            "At most one claimant owns the resource.",
            "Grant requires both logical availability and the reviewed physical-state evidence.",
            "Communication loss or stale ownership cannot silently transfer the resource.",
        ),
        alarm_expectations=(
            "Detect conflicting grants, excessive wait, stale owner, and physical occupation without owner.",
            "Report the current claimant and blocking condition without hiding safety-system status.",
        ),
        recovery_expectations=(
            "Forced release requires verified physical clearance and governed authorization.",
            "After ambiguity, all claimants reacquire ownership rather than relying on stale retained grants.",
        ),
        required_questions=(
            "What physical volume or process capacity does the resource represent?",
            "Which evidence proves it is clear, occupied, and safely releasable?",
            "How are stale remote claimants detected without unsafe automatic transfer?",
        ),
        verification_scenarios=(
            _scenario(
                "simultaneous_claims",
                "Multiple claimants are eligible.",
                "Submit requests concurrently.",
                "Exactly one grant follows the reviewed arbitration rule and the others remain waiting.",
            ),
            _scenario(
                "occupied_without_release",
                "A claimant owns and physically occupies the resource.",
                "Remove its request without valid release evidence.",
                "Ownership is retained or faulted according to policy; another claimant is not granted.",
            ),
            _scenario(
                "stale_owner",
                "A remote claimant owns the resource.",
                "Lose its freshness evidence.",
                "The resource enters governed recovery and is not automatically reassigned.",
            ),
        ),
    ),
    _pattern(
        pattern_id="command.request_ack_done",
        title="Request-acknowledge-done command handshake",
        equipment_kind="command_interface",
        intent=(
            "Exchange commands across tasks, controllers, devices, or software boundaries with "
            "explicit acceptance, completion, failure, cancellation, and stale-data handling."
        ),
        search_terms=(
            "handshake",
            "request ack done",
            "command interface",
            "핸드셰이크",
            "요청 응답 완료",
        ),
        required_signal_roles=(
            _signal(
                "command_request", "request", "New command request with stable payload semantics."
            ),
            _signal("command_acknowledged", "feedback", "Receiver accepted this command instance."),
            _signal("command_done", "feedback", "Receiver completed this command instance."),
            _signal(
                "command_failed", "fault", "Receiver ended this command instance unsuccessfully."
            ),
            _signal("interface_healthy", "feedback", "Communications and data freshness status."),
        ),
        optional_signal_roles=(
            _signal("command_cancel", "request", "Request to cancel an accepted command."),
            _signal(
                "command_rejected", "feedback", "Receiver refused the request before acceptance."
            ),
            _signal(
                "command_identity", "state", "Sequence, token, or edge policy linking the response."
            ),
            _signal("result_code", "state", "Machine-readable completion or failure detail."),
        ),
        required_decisions=(
            _decision(
                "command_instance_identity",
                "Select edge, sequence, token, or other proof that responses belong to this request.",
            ),
            _decision(
                "sender_receiver_reset_ownership",
                "Define which side asserts and clears every handshake element.",
            ),
            _decision(
                "timeout_retry_and_duplicate_policy",
                "Define monitoring, retry prohibition or idempotency, and duplicate handling.",
            ),
        ),
        invariants=(
            "A stale acknowledgement or completion cannot satisfy a new command.",
            "Each handshake element has exactly one owner responsible for asserting and clearing it.",
            "An uncertain result is reconciled before a command that may have side effects is retried.",
        ),
        alarm_expectations=(
            "Distinguish not acknowledged, accepted but not completed, rejected, failed, and stale interface.",
            "Preserve receiver result codes and command identity for diagnosis.",
        ),
        recovery_expectations=(
            "Reconcile the receiver's actual state after disconnect or restart.",
            "Never automatically retry a side-effecting command whose outcome is unknown.",
        ),
        required_questions=(
            "Can a command safely repeat, and how is duplicate execution prevented?",
            "What links acknowledgement and completion to the exact request instance?",
            "Which side owns clearing on success, failure, cancellation, and restart?",
        ),
        verification_scenarios=(
            _scenario(
                "normal_handshake",
                "The interface is healthy and idle.",
                "Issue one command.",
                "Acceptance and exactly one terminal outcome are tied to the same request instance.",
            ),
            _scenario(
                "stale_completion",
                "Completion from an earlier request remains visible.",
                "Issue a new request.",
                "The old completion is rejected as evidence for the new instance.",
            ),
            _scenario(
                "disconnect_after_accept",
                "The receiver acknowledged a side-effecting command.",
                "Lose communications before terminal status.",
                "The sender marks the outcome uncertain and reconciles instead of blindly retrying.",
            ),
        ),
    ),
    _pattern(
        pattern_id="analog.scaled_measurement",
        title="Scaled analog measurement",
        equipment_kind="analog_instrument",
        intent=(
            "Convert a raw channel into a validated engineering measurement with range handling, "
            "quality, filtering, alarms, calibration traceability, and substitution governance."
        ),
        search_terms=("analog", "scaling", "instrument", "sensor", "아날로그", "스케일링", "계측"),
        required_signal_roles=(
            _signal("raw_measurement", "measurement", "Raw input value from the selected channel."),
            _signal(
                "measurement_quality",
                "state",
                "Fresh, valid, bad, uncertain, or substituted quality.",
            ),
            _signal(
                "engineering_value", "measurement", "Scaled value in declared engineering units."
            ),
            _signal("channel_fault", "fault", "Hardware or configured range fault status."),
        ),
        optional_signal_roles=(
            _signal("filtered_value", "measurement", "Filtered engineering value when justified."),
            _signal("high_alarm", "fault", "Governed high-limit state."),
            _signal("low_alarm", "fault", "Governed low-limit state."),
            _signal("rate_of_change_alarm", "fault", "Governed rate-of-change state."),
            _signal("substitution_active", "state", "Visible governed substitution indication."),
        ),
        required_decisions=(
            _decision(
                "raw_and_engineering_ranges",
                "Obtain module raw range, instrument calibrated range, units, and out-of-range behavior.",
            ),
            _decision(
                "quality_and_fault_policy",
                "Define open-wire, underrange, overrange, stale, bad, and uncertain handling.",
            ),
            _decision(
                "filter_and_alarm_policy",
                "Justify filtering, alarm limits, hysteresis, delay, and rate checks from process needs.",
            ),
            _decision(
                "substitution_governance",
                "Define whether substitution is allowed, who authorizes it, and how it is displayed.",
            ),
        ),
        invariants=(
            "Engineering value always carries explicit units and quality.",
            "Bad or stale quality cannot silently appear as a trustworthy process value.",
            "Clamping, filtering, or substitution never hides the original channel fault.",
        ),
        alarm_expectations=(
            "Separate instrument/channel quality faults from process high or low alarms.",
            "Alarm evaluation uses the reviewed source value, hysteresis, and delay semantics.",
        ),
        recovery_expectations=(
            "Return from substitution or bad quality only after fresh valid evidence is established.",
            "Calibration changes are versioned and verified at representative points across the range.",
        ),
        required_questions=(
            "What are the module raw range, instrument range, units, calibration, and failure current?",
            "Which consumers must stop, hold, substitute, or alarm when quality is bad?",
            "Is filtering needed, and what process behavior justifies it?",
        ),
        verification_scenarios=(
            _scenario(
                "range_points",
                "The channel and calibration source are controlled.",
                "Apply representative low, middle, and high values.",
                "Engineering value, units, quality, and error stay within reviewed tolerances.",
            ),
            _scenario(
                "channel_fault",
                "The process value is currently valid.",
                "Create a detectable channel or instrument failure.",
                "Quality becomes bad, the fault stays visible, and consumers follow their reviewed policy.",
            ),
            _scenario(
                "substitution_exit",
                "Governed substitution is active.",
                "Restore a fresh valid physical measurement and request return.",
                "The transition is visible and avoids an unreviewed process discontinuity.",
            ),
        ),
    ),
)


def pattern_to_dict(pattern: ControlPattern) -> dict[str, object]:
    """Return a detached JSON-compatible representation of a pattern."""

    return json.loads(json.dumps(asdict(pattern), ensure_ascii=False))


def pattern_search_text(pattern: ControlPattern) -> str:
    """Render searchable text without inventing a vendor-native implementation."""

    sections = [
        pattern.pattern_id,
        pattern.title,
        pattern.equipment_kind,
        pattern.intent,
        " ".join(pattern.search_terms),
        " ".join(
            f"{signal.role_id}: {signal.description}"
            for signal in pattern.required_signal_roles + pattern.optional_signal_roles
        ),
        " ".join(decision.prompt for decision in pattern.required_decisions),
        " ".join(pattern.invariants),
        " ".join(pattern.alarm_expectations),
        " ".join(pattern.recovery_expectations),
        " ".join(pattern.required_questions),
        " ".join(
            f"{scenario.setup} {scenario.action} {scenario.expected}"
            for scenario in pattern.verification_scenarios
        ),
        pattern.safety_boundary,
        pattern.source_note,
    ]
    return "\n".join(section for section in sections if section)


def pattern_to_knowledge_document(pattern: ControlPattern) -> dict[str, object]:
    """Export one built-in pattern for the Host knowledge seed/index path."""

    return {
        "document_id": f"builtin-control-pattern:{pattern.pattern_id}@{pattern.revision}",
        "kind": "control_pattern",
        "title": pattern.title,
        "vendor": "vendor-neutral",
        "revision": pattern.revision,
        "trust": pattern.trust,
        "content": pattern_search_text(pattern),
        "metadata": pattern_to_dict(pattern),
    }


def iter_builtin_knowledge_documents() -> Iterator[dict[str, object]]:
    for pattern in _BUILTIN_PATTERNS:
        yield pattern_to_knowledge_document(pattern)
