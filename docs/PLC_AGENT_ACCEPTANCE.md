# PLC engineering Agent acceptance harness

`scripts/plc_agent_acceptance.manifest.json` is the single command manifest for
the deterministic part of CA-01 through CA-12. It deliberately runs only
repeatable local checks:

- Agent mission resume, legacy-repair, JSON fallback, and approval-wait tests;
- Host change-execution, durable resume, field-I/O projection, quarantined
  working-copy recovery/rebind, and Host source-restart contract tests;
- MCP subprocess-boundary, field-I/O binding, journal-pinned quarantined-backup
  restoration, independent CLI contract tests, and the CLI-owned production
  `list-programs`/`read-ld` closures (`list-programs` uses bounded SHA-bound
  XGWX XML instead of a project-tree row sweep; `read-ld` owns exact editor
  selection, dialog guards, native capture/decode, semantic projection, and
  private handoff; both share the UTF-8 canonical request wire);
- the production web build.

Run it from `gidong-platform`:

```powershell
host\.venv\Scripts\python.exe scripts\plc_agent_acceptance.py --deterministic-only
```

The command creates `artifacts/plc-agent-acceptance/run-*/receipt.json`, plus
bounded stdout/stderr logs for each manifest command. A deterministic-only run
returns success only when all deterministic commands pass; that exit code does **not**
mean live acceptance is complete. Its receipt still
has `overall_status: FAIL_CLOSED` while any live gate is missing. This is
intentional: passing unit, integration, or web-build checks can never claim an
XG5000 or SSH acceptance result.

The manifest includes the command-aware launch lease, absolute deadline,
direct-LD Host orchestration, post-close quarantine, and journal-pinned backup
restoration regressions. The consolidated run at `2026-08-30T23:11:09Z`, with
Agent `5a707b0`, completed all five deterministic command groups (255 test
executions; session `50168`, exit `0`). Its receipt is
`C:\Users\Administrator\Downloads\workspace\controlstudio-platform-main\artifacts\plc-agent-acceptance\run-6rg7qore\receipt.json`, bound to manifest SHA-256
`aba0b0d80046c7b9c66a6f3d0fb00cc82090564ccd565526633466e4f2640f7a`.
CA-01, CA-02, CA-04--CA-07 and CA-12 remain `NOT_RUN` without their external
live receipts, so that receipt's `overall_status` is still `FAIL_CLOSED`.

The 2026-09-01 CLI-owned production `read-ld` deterministic run passed all
five command groups at
`artifacts/plc-agent-acceptance-cli-owned-read/run-sth3ub2e/receipt.json`.
Its manifest SHA-256 is
`c1d5bf9b54eb4912644899a8f553a3d8b6ab76b54b4778ee61cec19bdacf8d3e`;
the independent CLI group ran 84 tests covering the shared UTF-8 wire decoder,
entry binding, raw clipboard capture, editor selection, fail-closed dialog
guards, and a subprocess proof that the production closure imports no
`xg5000_control` module. The receipt remains `FAIL_CLOSED` overall because no
external live-gate receipt was supplied.

The following CLI-owned catalog run also passed all five deterministic groups:
`artifacts/plc-agent-acceptance-cli-owned-catalog/run-7f9t2xt4/receipt.json`.
Its manifest SHA-256 is
`06503a913f3b0542cddbe0d361e721fcda79965c743c8b259bfe0ea6bd3bee15`.
The independent CLI group ran 93 tests and the MCP boundary group ran 126.
The catalog entry is covered for literal Korean paths, duplicate/ambiguous
program rejection, no MCP import closure, exact stable IDs, project title,
PID/HWND lifetime, and unchanged file SHA. The receipt remains `FAIL_CLOSED`
overall only because external live-gate receipts were not supplied.

Separately, real product run `fb410c7f-6cda-4c03-adb7-f18ba42a61ae` completed
one `project.list_programs` call and one actual `program.read_ld` call against
XG5000 PID 16228/HWND 723394. Evidence
`5deb704b-8167-57bb-a11c-7c4d343b600b` binds the unchanged project SHA-256
`2d55b3489f0f1a97666e79d00bd4ef6962d2e2e71fa44839193be04240308b48`,
payload SHA-256
`aca8fb5f154b49ae9431df075efee8971610fd83dca1ebba65821e6be7795bd7`,
and the answer `M00001 AND (M00000 OR M00010) -> M00010`. That observation is
traceability evidence, not a silently manufactured CA-02 receipt.

Use `--dry-run` to validate the manifest and inspect the exact commands without
running them. The runner never starts XG5000, requests UAC, opens a browser, or
connects over SSH.

## External live gates

현재 manifest의 live gate는 XG5000 프로젝트·오프라인 LD와 remote handoff 범위다. 물리 PLC
장비가 필요한 online observe/write acceptance는 현재 gate가 아니며, 장비 확보 후 PRD G3/G4용
별도 manifest로 추가한다. 따라서 물리 PLC가 없다는 사실은 이 harness의 `NOT_RUN`이나 현재
작업의 blocker 사유가 아니다.

CA-01, CA-02, the CA-04--CA-07 approved-mutation/fresh-reopen vertical, and
CA-12 need a separately captured live receipt. They remain `NOT_RUN`, and the
overall result remains `FAIL_CLOSED`, until each gate receives a JSON file via:

```powershell
host\.venv\Scripts\python.exe scripts\plc_agent_acceptance.py `
  --live-receipt CA-01=C:\evidence\ca01.json `
  --live-receipt CA-02=C:\evidence\ca02.json `
  --live-receipt CA-04-CA-07=C:\evidence\ca04-ca07.json `
  --live-receipt CA-12=C:\evidence\ca12.json
```

Each supplied receipt must have schema
`controlstudio-plc-live-gate-receipt/v1`, a matching `gate_id`, `status: PASS`,
`captured_at`, and the exact `manifest_sha256` printed in the harness receipt.
`captured_at` must be a timezone-aware ISO-8601 timestamp. It must also meet the gate-specific evidence contract below. Any malformed,
stale, or mismatched receipt is `FAIL_CLOSED`; the harness does not infer
success from a test result.

| Gate | Required strict evidence |
| --- | --- |
| CA-01 | `first_launch_ready: true`, integer `uac_prompt_count: 1`, and integer `subsequent_prompt_count: 0` |
| CA-02 | Exact positive PID/HWND, project path/SHA-256, program ID/name, plus verified `program.read_ld` evidence with its payload SHA-256 |
| CA-04-CA-07 | Approval ID plus approval-plan and result SHA-256; source/reopened PID and HWND must each differ; canonically identical source/reopened project paths; project hashes before/after with final hash equal to reopened SHA-256; `saved: true`; Program Check `status: PASS` and `passed: true`; readback is either `kind: MNE_AND_LD` with before/after MNE and LD SHA-256 (at least one changed pair), or `kind: LD` with one changed before/after LD SHA-256 pair. `kind: LD` is used for a reviewed operand-only vertical change and must not invent MNE evidence. |
| CA-12 | SSH entry ID and explicit disconnect; interactive Windows session ID plus XG5000 PID/HWND and retained handoff; preserved Host journal run ID, journal schema/SHA-256, and `host_durable_ssh_handoff_observation_v1` source assurance |

Boolean and integer fields are type-checked strictly (`true` is not accepted as
an integer). A plausible-looking JSON file that merely says `PASS` cannot
promote a deterministic test run to live acceptance.

## Narrow CA-04--CA-07 live procedure: disposable LD operand change

The next live run is intentionally one offline fixture only.  Use a disposable
backup/working copy, never the user's source project or a controller-connected
project.  The reviewed mutation is exactly `MainProgram` LD operand `P0020` to
`P0021`; do not substitute a different address, create a generic ladder edit,
or infer physical I/O behavior from this proof.

1. Start or retain one compatible interactive Host session.  If elevation is
   required, approve it once for that Host lifetime; do not request UAC per
   operation.  If UAC is actually denied, record that denial and do not request
   elevation again until a new explicit user request.  A readiness observation
   failure or timeout is not by itself evidence of denial.
2. Open the disposable copy as one user-visible attempt and record workspace
   revision, path/hash, PID, HWND, process creation time, selected program ID,
   and baseline LD payload digest.  If the result fails or is ambiguous, retain
   any live handle and diagnose it against the journal and identity evidence.
   Do not replay an open or another non-idempotent side effect whose outcome is
   uncertain, and do not reuse a consumed approval.  Safe inspection and
   implementation may continue only within the already authorized exact
   plan/file/mutation scope; an observation failure alone does not require a
   new user request.
3. Render a proposal containing the exact token offset, expected `P0020`, and
   replacement `P0021`; obtain Host approval; then apply only that approved
   operand mutation, validate it, save, and record the authoritative hash.
4. Invoke `project.verify_ld_operand_reopen` once.  Its standalone
   `fresh-reopen-ld-approval-readback` command closes only the reviewed source
   PID and opens the same saved path in a distinct PID/HWND.  Its operation
   budget is the lesser of the requested 120 seconds and the remaining portion
   of the adapter's single 135-second wall-clock budget minus the cleanup/JSON
   reserve.
5. In the fresh process, record stable `P0021` LD readback, Program Check with
   zero errors, the unchanged saved hash, and the approval/result bindings.
   Only the fully promoted terminal lease may be attached and committed; the
   intermediate project-open receipt is not transaction success.
6. Build the CA-04--CA-07 live receipt from those observations and submit it
   to the deterministic harness command above.  A missing receipt, unchanged
   PID/HWND, nonzero Program Check, hash mismatch, or unproved readback remains
   `NOT_RUN`/`FAIL_CLOSED`; it is never represented as a successful mutation.

After the source process is closed, any verification, attach, or commit failure
must leave both the MCP and Host transactions quarantined.  It must not reuse
the closed source HWND for rollback or retry silently.  Retain and diagnose any
live replacement handle and the journal result; never replay an uncertain
non-idempotent side effect or a consumed approval.  Quarantine does not by
itself stop safe inspection or implementation within the exact existing
authorization.  Only an actual UAC denial requires a new explicit user request
before another elevation attempt.

This acceptance is strictly offline LD/project evidence.  It does not write,
force, download, mode-change, or otherwise control a real PLC I/O point.

## CA-12 Host journal

`controlstudio_host.ssh_handoff.SshInteractiveHandoffJournal` persists an
adapter-observed SSH entry, interactive Windows session/XG5000 PID-HWND handoff,
and the matching SSH disconnect under one Host run ID. Its
`acceptance_draft()` produces the CA-12 evidence shape with a Host journal
SHA-256, but its status is always `NOT_RUN`: it never claims a remote SSH live
PASS. The module does not install, configure, start, or connect SSH.

An eventual privileged SSH transport adapter and a separate interactive-session
observer must call it with their fixed adapter identities. A real remote
topology still has to exercise that path and supply the independently observed
PASS receipt to this harness.

## CA-01 launcher observation

On Windows the Launcher writes its own observable lifecycle evidence to
`%LOCALAPPDATA%\ControlStudio\elevation-observations.v1.json`. Each observation
records whether the initial Host was already verified, whether Windows accepted
an elevation relaunch, Host readiness, and the post-readiness integrity-retention
check. An elevated child is labelled `elevated_handoff_child`, so it cannot be
mistaken for a subsequent user launch.

The Launcher cannot inspect Windows' secure UAC desktop. `elevation_relaunch_requested:
true` therefore means only that the `RunAs` handoff was accepted, not that a
visible prompt was counted or approved. CA-01 still needs a clean-profile,
interactive Windows observation to establish exactly one first-launch prompt
and zero prompts on a later retained-Host launch. CA-12 remains external until
an SSH/session-handoff implementation produces its own evidence.
