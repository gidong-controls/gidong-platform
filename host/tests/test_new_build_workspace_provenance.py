from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from controlstudio_host.api_models import (
    LocalWorkspaceV1,
    NewBuildWorkspaceProvenanceV1,
)
from controlstudio_host.native_projects import (
    NativeProjectEvidenceError,
    NativeProjectService,
    _new_build_workspace_provenance,
)


class NewBuildWorkspaceProvenanceTests(unittest.TestCase):
    def test_capture_and_http_contract_preserve_exact_build_lineage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controlstudio-new-build-") as temporary:
            root = Path(temporary)
            project_path = root / "generated.xgwx"
            captured_path = root / "captured.xgwx"
            working_path = root / "working.xgwx"
            project_bytes = b"verified native build"
            for path in (project_path, captured_path, working_path):
                path.write_bytes(project_bytes)
            project_hash = hashlib.sha256(project_bytes).hexdigest()
            provenance = _new_build_workspace_provenance(
                native_project_id="11111111-1111-4111-8111-111111111111",
                build_bundle={
                    "bundleId": "2" * 64,
                    "reviewedPlan": {"planDigest": "3" * 64},
                },
                build_receipt={"schemaVersion": "receipt/v1", "verified": True},
                project_sha256=project_hash,
            )
            workspace = {
                "ok": True,
                "verified": True,
                "workspace_id": "22222222-2222-4222-8222-222222222222",
                "revision_id": "33333333-3333-4333-8333-333333333333",
                "source_kind": "new_build",
                "vendor": "ls",
                "engineering_system": "XG5000",
                "created_at": datetime(2026, 8, 16, tzinfo=timezone.utc).isoformat(),
                "source": {
                    "original_path": str(project_path),
                    "captured_path": str(captured_path),
                    "sha256": project_hash,
                    "original_state": "unchanged",
                    "original_current_sha256": project_hash,
                },
                "working_copy": {
                    "path": str(working_path),
                    "baseline_sha256": project_hash,
                    "current_sha256": project_hash,
                    "state": "clean",
                },
                "provenance": provenance,
            }

            validated = NativeProjectService._validate_workspace_capture(
                workspace,
                project_path=project_path,
                project_hash=project_hash,
                provenance=provenance,
            )
            self.assertIs(validated, workspace)

            api_workspace = LocalWorkspaceV1.model_validate(
                {
                    name: workspace[name]
                    for name in LocalWorkspaceV1.model_fields
                    if name in workspace
                }
            )
            self.assertIsInstance(
                api_workspace.provenance,
                NewBuildWorkspaceProvenanceV1,
            )
            serialized = api_workspace.model_dump(mode="json", by_alias=True)
            self.assertEqual(serialized["sourceKind"], "new_build")
            self.assertEqual(
                serialized["provenance"]["nativeProjectId"],
                provenance["native_project_id"],
            )
            self.assertEqual(
                serialized["provenance"]["receiptDigest"],
                provenance["receipt_digest"],
            )

    def test_capture_rejects_substituted_or_tampered_lineage(self) -> None:
        project_hash = "a" * 64
        provenance = _new_build_workspace_provenance(
            native_project_id="11111111-1111-4111-8111-111111111111",
            build_bundle={
                "bundleId": "2" * 64,
                "reviewedPlan": {"planDigest": "3" * 64},
            },
            build_receipt={"verified": True},
            project_sha256=project_hash,
        )
        source = {
            "original_path": "C:/generated.xgwx",
            "captured_path": "C:/captured.xgwx",
            "sha256": project_hash,
        }
        working = {
            "path": "C:/working.xgwx",
            "baseline_sha256": project_hash,
            "current_sha256": project_hash,
            "state": "clean",
        }
        raw = {
            "ok": True,
            "verified": True,
            "workspace_id": "22222222-2222-4222-8222-222222222222",
            "revision_id": "33333333-3333-4333-8333-333333333333",
            "source_kind": "new_build",
            "source": source,
            "working_copy": working,
            "provenance": provenance,
        }
        substituted = copy.deepcopy(raw)
        substituted["provenance"]["build_bundle_id"] = "9" * 64
        with self.assertRaisesRegex(
            NativeProjectEvidenceError,
            "does not match the created native project",
        ):
            NativeProjectService._validate_workspace_capture(
                substituted,
                project_path=Path(source["original_path"]),
                project_hash=project_hash,
                provenance=provenance,
            )

        invalid_api_provenance = copy.deepcopy(provenance)
        invalid_api_provenance["reviewed_plan_digest"] = "8" * 64
        with self.assertRaises(ValidationError):
            NewBuildWorkspaceProvenanceV1.model_validate(invalid_api_provenance)


if __name__ == "__main__":
    unittest.main()
