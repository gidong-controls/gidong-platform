# Three-repository refactor validation evidence

This matrix turns stages 0, 5, 6, 11, and 14 of the refactor plan into
fail-closed release gates. A missing evidence JSON is a failed gate, not a
waiver. The scripts intentionally do not create sibling repositories.

| Plan stage | Gate | Required evidence | Passing condition |
| --- | --- | --- | --- |
| 0 | Preserve source | `00-baseline.json`, separately retained Git bundle and mirror paths/hashes | Source HEAD and refs are recorded; any dirty state is recorded before archival. |
| 5 | Three sibling topology | `05-sibling-plan.json` | Workspace is not Git; three direct sibling paths are empty/nonexistent; plan says no remote/push/nested Git. Logical boundary and integration gates have already passed. |
| 5 | Snapshot and root layout | `05-snapshot-plan.json` | The source-to-sibling map is collision-free, excludes generated caches, keeps Platform TypeScript contracts, flattens Agent/MCP to their package roots, and has zero sibling source-path dependencies. |
| 5 | Partition ownership | One `05-*-partition.json` per component | Only allowlisted top-level content exists and cross-boundary source imports are zero. Review the policy before each new language or package layout. |
| 6 | Artifact identity | `06-bom.json`, immutable artifacts, BOM | Exactly platform, agent, and plc-mcp artifacts exist; every SHA-256 matches; product and five contract versions are present. Signature verification remains a required release-pipeline input in addition to SHA-256. |
| 11 | Public safety | `11-public-history.json`, license/notice review | All reachable blobs, branches, tags, and remote-tracking refs are scanned. No secrets, Agent/MCP code, fixtures, user data, or old remote refs are present. False positives require policy review; never suppression by omission. |
| 14 | Independent delivery | `14-build-and-packaged-e2e.json` | Each repository runs its own build and test. Final E2E consumes only packaged artifacts, not editable sibling sources. Platform mock launch passes without private artifacts. |
| 14 | PLC scope integrity | MCP real-XG5000 evidence and test reports | The existing read/offline mutation/rollback release gate remains green. Online PLC capabilities remain explicitly unverified and fail closed unless separately evidenced. |

## Running the gates

Use `scripts/refactor/RefactorValidation.ps1` after candidate repositories and
artifacts exist. It exits nonzero on every failed or missing requested gate and
writes deterministic JSON evidence to the directory you choose.

```powershell
pwsh scripts/refactor/RefactorValidation.ps1 `
  -SourceRepo C:\safe\controlstudio-monorepo `
  -Workspace C:\safe\ControlStudio-workspace `
  -EvidenceDirectory C:\safe\refactor-evidence
```

For a candidate phase, add `-PlatformCandidate`, `-AgentCandidate`, and
`-McpCandidate`. The current source tree must never be passed as a claimed
partition candidate: it intentionally contains all components during the
logical-boundary transition.

`snapshot-layout.json` is the only reviewed copy map. Agent copies `agent/**`
to the Agent repository root and MCP copies `mcp/**` to the MCP repository
root; neither leaves an `agent/` or `mcp/` wrapper directory in its sibling.
Platform retains its multi-package layout and explicitly includes
`packages/typescript/controlstudio-contracts`. Generated caches and build
outputs (including `.pytest-*`, `node_modules`, `*.egg-info`, `dist`, and user
`artifacts`) are excluded before destination collision and dependency checks.
Local package paths are allowed only when they resolve to another mapped path
inside the same future repository; an unmapped `../mcp`-style sibling reference
fails the dry run.

`run-manifest` accepts an externally supplied JSON manifest with explicit build
and test argv arrays plus a `packaged_e2e` argv array. Its E2E command must
install/launch the three recorded artifacts in an isolated directory and must
assert the Platform mock path works with no Agent/MCP artifact installed.
Both `packaged_e2e.artifact_only: true` and a separate `platform_mock` command
are mandatory; the tool fails closed when either declaration is absent.

## Reviewer checklist before the actual creation operation

- Archive verification has restored the bundle and mirror into disposable paths.
- All boundary, mock, contract, and regression tests are green.
- Each target is an empty direct sibling; no target is inside the source
  repository and the workspace parent itself has no `.git`.
- The public candidate has a clean genesis only; its complete reachable history
  has passed the scan and a human has reviewed license/notices/trademarks.
- The BOM records SHA-256, contract versions, signature material, and the exact
  immutable artifact filenames.
- No `git remote add`, `git push`, submodule, or source sibling dependency is
  part of the reviewed creation command.
