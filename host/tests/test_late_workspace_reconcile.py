from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from controlstudio_host.api_models import LocalWorkspaceV1
from controlstudio_host.app import _reconcile_late_workspace_open


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.01)


class _LateWorkspaceMCP:
    def __init__(
        self,
        *,
        instances: list[list[dict[str, Any]]],
        session: dict[str, Any],
    ) -> None:
        self.instance_results = list(instances)
        self.session = session
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_read_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        if name == "xg5000.instances":
            current = self.instance_results.pop(0) if self.instance_results else []
            return {"instances": current}
        if name == "xg5000.attach":
            return {"session": dict(self.session)}
        raise AssertionError(f"unexpected read tool: {name}")


def _workspace(root: Path) -> LocalWorkspaceV1:
    project = root / "working" / "machine.xgwx"
    source = root / "source" / "machine.xgwx"
    captured = root / "captured" / "machine.xgwx"
    for path in (project, source, captured):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"workspace")
    return LocalWorkspaceV1.model_validate(
        {
            "workspace_id": "11111111-1111-4111-8111-111111111111",
            "revision_id": "22222222-2222-4222-8222-222222222222",
            "source_kind": "local_file",
            "vendor": "ls",
            "engineering_system": "XG5000",
            "created_at": "2026-08-26T00:00:00+00:00",
            "source": {
                "original_path": str(source.resolve()),
                "captured_path": str(captured.resolve()),
                "sha256": "a" * 64,
                "original_state": "unchanged",
                "original_current_sha256": "a" * 64,
            },
            "working_copy": {
                "path": str(project.resolve()),
                "baseline_sha256": "b" * 64,
                "current_sha256": "b" * 64,
                "state": "clean",
            },
        }
    )


def _session(workspace: LocalWorkspaceV1, *, project_hash: str | None = None) -> dict[str, Any]:
    return {
        "session_id": "late-ready-session",
        "pid": 14544,
        "hwnd": 67072,
        "stage": "ANALYSIS_READY",
        "project_path": workspace.working_copy.path,
        "project_hash": project_hash or workspace.working_copy.current_sha256,
        "metadata": {
            "project_identity_verified_against_selected_pid": True,
            "project_identity_read_only_bound_to_selected_pid": False,
            "project_identity_assurance": "strong_readback",
        },
    }


class LateWorkspaceReconcileTests(unittest.IsolatedAsyncioTestCase):
    async def test_delayed_single_instance_is_exactly_attached_without_relaunch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-late-workspace-") as temporary:
            workspace = _workspace(Path(temporary))
            instance = {"pid": 14544, "hwnd": 67072, "title": "machine - XG5000"}
            mcp = _LateWorkspaceMCP(
                instances=[[], [instance]],
                session=_session(workspace),
            )
            clock = _Clock()
            bound: list[dict[str, Any]] = []

            reconciled = await _reconcile_late_workspace_open(
                mcp,  # type: ignore[arg-type]
                workspace,
                bind_session=lambda value: bound.append(value["session"])
                or value["session"],
                timeout_seconds=1.0,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )

            self.assertIsNotNone(reconciled)
            assert reconciled is not None
            launch, session = reconciled
            self.assertTrue(launch.verified)
            self.assertEqual(launch.identity_assurance, "strong_readback")
            self.assertEqual(session["pid"], 14544)
            self.assertEqual(bound, [_session(workspace)])
            self.assertEqual(
                mcp.calls,
                [
                    ("xg5000.instances", {}),
                    ("xg5000.instances", {}),
                    (
                        "xg5000.attach",
                        {
                            "pid": 14544,
                            "hwnd": 67072,
                            "project_path": workspace.working_copy.path,
                            "project_hash": workspace.working_copy.current_sha256,
                            "workspace_id": workspace.workspace_id,
                        },
                    ),
                ],
            )
            self.assertNotIn("xg5000.open_project", [name for name, _ in mcp.calls])

    async def test_multiple_instances_fail_closed_without_attach(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-late-multiple-") as temporary:
            workspace = _workspace(Path(temporary))
            instances = [
                {"pid": 14544, "hwnd": 67072},
                {"pid": 15555, "hwnd": 68000},
            ]
            mcp = _LateWorkspaceMCP(
                instances=[instances],
                session=_session(workspace),
            )
            clock = _Clock()

            reconciled = await _reconcile_late_workspace_open(
                mcp,  # type: ignore[arg-type]
                workspace,
                bind_session=lambda value: value["session"],
                timeout_seconds=1.0,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )

            self.assertIsNone(reconciled)
            self.assertEqual(mcp.calls, [("xg5000.instances", {})])

    async def test_exact_attach_hash_mismatch_fails_before_binding(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-late-mismatch-") as temporary:
            workspace = _workspace(Path(temporary))
            mcp = _LateWorkspaceMCP(
                instances=[[{"pid": 14544, "hwnd": 67072}]],
                session=_session(workspace, project_hash="c" * 64),
            )
            clock = _Clock()
            bound: list[dict[str, Any]] = []

            reconciled = await _reconcile_late_workspace_open(
                mcp,  # type: ignore[arg-type]
                workspace,
                bind_session=lambda value: bound.append(value["session"])
                or value["session"],
                timeout_seconds=1.0,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )

            self.assertIsNone(reconciled)
            self.assertEqual(bound, [])
            self.assertEqual([name for name, _ in mcp.calls], ["xg5000.instances", "xg5000.attach"])


if __name__ == "__main__":
    unittest.main()
