from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from controlstudio_packaging.bom import (
    PackagingError,
    assemble,
    build_bom,
    verify_bom,
)

CONTRACTS = {
    "api_contract": "1.0", "event_contract": "1.0", "agent_contract": "1.0",
    "mcp_product_contract": "1.0", "workspace_contract": "1.0", "audit_contract": "1.0",
}


class BomTests(unittest.TestCase):
    def test_platform_builder_never_reaches_into_private_source_trees(self):
        root = Path(__file__).resolve().parents[2]
        builder = (root / "packaging" / "scripts" / "build_standalone.py").read_text(encoding="utf-8")
        self.assertIn('"controlstudio-platform"', builder)
        self.assertNotIn('REPO / "agent"', builder)
        self.assertNotIn('REPO / "mcp"', builder)
        self.assertNotIn("xg5000_control", builder)

    def test_platform_builds_the_public_contract_artifact_without_private_sources(self):
        root = Path(__file__).resolve().parents[2]
        builder = (root / "packaging" / "scripts" / "build_contracts_wheel.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("controlstudio-contracts", builder)
        self.assertNotIn('REPOSITORY / "agent"', builder)
        self.assertNotIn('REPOSITORY / "mcp"', builder)

    def test_platform_snapshot_contains_only_platform_packaging_entrypoint(self):
        root = Path(__file__).resolve().parents[2]
        entrypoints = root / "packaging" / "entrypoints"
        self.assertEqual(sorted(path.name for path in entrypoints.glob("*_main.py")), ["platform_main.py"])

    def test_platform_entrypoint_dispatches_same_executable_as_internal_host(self):
        root = Path(__file__).resolve().parents[2]
        entrypoint = (root / "packaging" / "entrypoints" / "platform_main.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"--host-executable"', entrypoint)
        self.assertIn("sys.executable", entrypoint)
        self.assertIn("controlstudio_host.__main__", entrypoint)
        self.assertIn("--controlstudio-internal-host", entrypoint)

    def test_portable_launcher_supports_windows_powershell_and_artifact_probe(self):
        root = Path(__file__).resolve().parents[2]
        launcher = (root / "packaging" / "scripts" / "Start-ControlStudio.ps1").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("ConvertFrom-Json -Depth", launcher)
        self.assertIn("$ArtifactSelfTest", launcher)
        self.assertIn("--controlstudio-artifact-self-test", launcher)
        assembler = (root / "packaging" / "scripts" / "assemble_release.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("Start-ControlStudio.ps1", assembler)

    def create_release(self, directory: Path):
        components = {}
        for name in ("platform", "agent", "plc-mcp"):
            artifact = directory / f"controlstudio-{name}.exe"
            artifact.write_bytes(f"immutable {name}".encode())
            components[name] = {"artifact": str(artifact), "version": "0.1.0"}
        contract_artifact = directory / "controlstudio_contracts-0.1.0-py3-none-any.whl"
        contract_artifact.write_bytes(b"immutable public contracts")
        return build_bom(
            product_release="0.1.0",
            components=components,
            contracts=CONTRACTS,
            contract_artifact={"artifact": str(contract_artifact), "version": "0.1.0"},
        )

    def test_bom_verifies_and_assembles_only_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bom = self.create_release(root)
            verify_bom(bom, root)
            bom_path = root / "bom.json"
            bom_path.write_text(json.dumps(bom), encoding="utf-8")
            output = assemble(bom_path, root, root / "portable")
            self.assertEqual(
                {p.name for p in output.iterdir()},
                {
                    "controlstudio.bom.json", "controlstudio-platform.exe",
                    "controlstudio-agent.exe", "controlstudio-plc-mcp.exe",
                    "controlstudio_contracts-0.1.0-py3-none-any.whl",
                },
            )

    def test_tampered_artifact_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bom = self.create_release(root)
            (root / "controlstudio-agent.exe").write_bytes(b"tampered")
            with self.assertRaisesRegex(PackagingError, "SHA-256 mismatch"):
                verify_bom(bom, root)

    def test_tampered_public_contract_wheel_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bom = self.create_release(root)
            (root / bom["contract_artifact"]["artifact"]).write_bytes(b"tampered")
            with self.assertRaisesRegex(PackagingError, "contract_artifact"):
                verify_bom(bom, root)

    def test_contract_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bom = self.create_release(root)
            bom["components"][1]["contracts"]["agent_contract"] = "2.0"
            with self.assertRaisesRegex(PackagingError, "contract versions"):
                verify_bom(bom, root)

    def test_unsigned_is_never_a_production_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bom = self.create_release(root)
            with self.assertRaisesRegex(PackagingError, "production signing"):
                verify_bom(bom, root, require_production=True)

    def test_development_hmac_detects_manifest_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            components = {}
            for name in ("platform", "agent", "plc-mcp"):
                artifact = root / f"{name}.exe"
                artifact.write_bytes(name.encode())
                components[name] = {"artifact": str(artifact), "version": "0.1.0"}
            contract_artifact = root / "controlstudio_contracts-0.1.0-py3-none-any.whl"
            contract_artifact.write_bytes(b"contracts")
            old = os.environ.get("CONTROLSTUDIO_PACKAGING_DEV_SIGNING_KEY")
            os.environ["CONTROLSTUDIO_PACKAGING_DEV_SIGNING_KEY"] = "local-test-key"
            try:
                bom = build_bom(
                    product_release="0.1.0", components=components, contracts=CONTRACTS,
                    contract_artifact={"artifact": str(contract_artifact), "version": "0.1.0"},
                    signing="development-hmac-sha256", signing_key="local-test-key",
                )
                verify_bom(bom, root)
                bom["components"][0]["version"] = "0.2.0"
                with self.assertRaisesRegex(PackagingError, "signature"):
                    verify_bom(bom, root)
            finally:
                if old is None:
                    os.environ.pop("CONTROLSTUDIO_PACKAGING_DEV_SIGNING_KEY", None)
                else:
                    os.environ["CONTROLSTUDIO_PACKAGING_DEV_SIGNING_KEY"] = old


if __name__ == "__main__":
    unittest.main()
