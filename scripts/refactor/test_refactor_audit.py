"""Stdlib regression tests for the refactor safety gates."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("refactor_audit.py")
POLICY = Path(__file__).with_name("partition-policy.json")


def run(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


class RefactorAuditTests(unittest.TestCase):
    def test_bom_checks_hash_and_component_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            components = []
            for identifier in ("platform", "agent", "plc-mcp"):
                artifact = root / f"{identifier}.zip"; artifact.write_bytes(identifier.encode())
                components.append({"id": identifier, "version": "0.1.0", "artifact": artifact.name,
                                   "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()})
            bom = root / "bom.json"
            contracts = {name: "1.0" for name in (
                "api_contract", "event_contract", "agent_contract", "mcp_product_contract",
                "workspace_contract", "audit_contract",
            )}
            for component in components:
                component["contracts"] = contracts
                component["signature"] = {"kind": "unsigned", "status": "not-for-production"}
            contract_artifact = root / "controlstudio_contracts-0.1.0-py3-none-any.whl"
            contract_artifact.write_bytes(b"public-contracts")
            contract_record = {
                "artifact": contract_artifact.name,
                "version": "0.1.0",
                "sha256": hashlib.sha256(contract_artifact.read_bytes()).hexdigest(),
            }
            bom.write_text(json.dumps({"bom_schema": "controlstudio-bom/v1", "product_release": "0.1.0",
                                       "contracts": contracts, "contract_artifact": contract_record,
                                       "components": components}))
            self.assertEqual(run("verify-bom", "--bom", str(bom), cwd=root).returncode, 0)
            components[0]["sha256"] = "wrong"
            bom.write_text(json.dumps({"bom_schema": "controlstudio-bom/v1", "product_release": "0.1.0",
                                       "contracts": contracts, "contract_artifact": contract_record,
                                       "components": components}))
            self.assertEqual(run("verify-bom", "--bom", str(bom), cwd=root).returncode, 1)

    def test_partition_rejects_private_import_from_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / "host").mkdir()
            (root / "host" / "bad.py").write_text("import xg5000_control\n")
            self.assertEqual(run("partition", "--candidate", str(root), "--component", "platform", "--policy", str(POLICY), cwd=root).returncode, 1)

    def test_partition_ignores_generated_virtual_environments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = root / ".contract-wheel-validation"
            generated.mkdir()
            (generated / "pyvenv.cfg").write_text("home = generated\n")
            (generated / "third_party.py").write_text("from pydantic_ai import mcp\n")
            self.assertEqual(
                run(
                    "partition", "--candidate", str(root), "--component", "agent",
                    "--policy", str(POLICY), cwd=root,
                ).returncode,
                0,
            )

    def test_public_history_allows_boundary_docs_but_rejects_private_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            (root / "README.md").write_text("The private Pydantic AI sidecar is supervised by Platform.\n")
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "public docs"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            self.assertEqual(run("public-history", "--repo", str(root), cwd=root).returncode, 0)
            (root / "private.py").write_text("import xg5000_control\n")
            subprocess.run(["git", "add", "private.py"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "bad"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            self.assertEqual(run("public-history", "--repo", str(root), cwd=root).returncode, 1)

    def test_public_history_rejects_local_user_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            (root / "evidence.md").write_text(
                "local path C:\\Users\\customer\\Documents\\machine.xgwx\n"
            )
            subprocess.run(["git", "add", "evidence.md"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "unsafe path"],
                cwd=root,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            self.assertEqual(run("public-history", "--repo", str(root), cwd=root).returncode, 1)

    def test_manifest_requires_real_artifacts_and_runs_mock_and_e2e(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = []
            for identifier in ("platform", "agent", "plc-mcp"):
                artifact = root / f"{identifier}.bin"; artifact.write_text(identifier); artifacts.append(str(artifact))
            manifest = {
                "repositories": [{"id": "platform", "path": str(root), "build": [sys.executable, "-c", ""], "test": [sys.executable, "-c", ""]}],
                "platform_mock": {"cwd": str(root), "command": [sys.executable, "-c", ""]},
                "packaged_e2e": {"artifact_only": True, "artifacts": artifacts, "cwd": str(root), "command": [sys.executable, "-c", ""]},
            }
            manifest_path = root / "manifest.json"; manifest_path.write_text(json.dumps(manifest))
            self.assertEqual(run("run-manifest", "--manifest", str(manifest_path), cwd=root).returncode, 0)

    def test_sibling_plan_is_dry_and_rejects_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "old"; source.mkdir()
            subprocess.run(["git", "init"], cwd=source, check=True, stdout=subprocess.DEVNULL)
            existing = root / "controlstudio-platform"; existing.mkdir()
            before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            result = run("sibling-plan", "--source", str(source), "--workspace", str(root), cwd=root)
            after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            self.assertEqual(result.returncode, 1); self.assertEqual(before, after)

    def test_snapshot_plan_flattens_private_components_and_rejects_sibling_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary); source = workspace / "source"; source.mkdir()
            subprocess.run(["git", "init"], cwd=source, check=True, stdout=subprocess.DEVNULL)
            (source / "platform").mkdir(); (source / "platform" / "contracts.ts").write_text("export type Contract = {}")
            for component, package in (("agent", "controlstudio_agent"), ("mcp", "xg5000_control")):
                package_root = source / component / "src" / package; package_root.mkdir(parents=True)
                (source / component / "pyproject.toml").write_text("[project]\nname = 'safe'\n")
                (package_root / "main.py").write_text("pass\n")
                (source / component / "node_modules").mkdir(); (source / component / "node_modules" / "ignored.js").write_text("ignored")
            layout = {
                "components": {
                    "platform": {"target_directory": "controlstudio-platform", "mappings": [{"from": "platform", "to": "."}], "required_paths": ["contracts.ts"]},
                    "agent": {"target_directory": "controlstudio-agent", "mappings": [{"from": "agent", "to": "."}], "required_paths": ["pyproject.toml", "src/controlstudio_agent/main.py"]},
                    "plc-mcp": {"target_directory": "controlstudio-plc-mcp", "mappings": [{"from": "mcp", "to": "."}], "required_paths": ["pyproject.toml", "src/xg5000_control/main.py"]},
                }
            }
            layout_path = workspace / "layout.json"; layout_path.write_text(json.dumps(layout))
            result = run("snapshot-plan", "--source", str(source), "--workspace", str(workspace), "--layout", str(layout_path), cwd=workspace)
            self.assertEqual(result.returncode, 0, result.stdout)
            evidence = json.loads(result.stdout)
            self.assertNotIn("node_modules/ignored.js", evidence["plans"]["agent"]["files_preview"])
            self.assertIn("agent/node_modules", evidence["plans"]["agent"]["excluded_generated_directories_preview"])
            (source / "agent" / "dependency.lock").write_text('{"link": "file:../mcp"}')
            result = run("snapshot-plan", "--source", str(source), "--workspace", str(workspace), "--layout", str(layout_path), cwd=workspace)
            self.assertEqual(result.returncode, 1); self.assertIn("source-sibling-path-dependency", result.stdout)


if __name__ == "__main__":
    unittest.main()
