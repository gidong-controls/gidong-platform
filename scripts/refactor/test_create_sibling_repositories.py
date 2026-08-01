from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import create_sibling_repositories as creator


class SiblingCreationTests(unittest.TestCase):
    def fixture(
        self,
        root: Path,
        *,
        private_public: bool = False,
        private_mcp: bool = False,
    ) -> tuple[Path, Path, Path]:
        source = root / "source"; source.mkdir()
        subprocess.run(["git", "init"], cwd=source, check=True, stdout=subprocess.DEVNULL)
        platform = source / "platform"; platform.mkdir()
        (platform / "public.py").write_text("import pydantic_ai\n" if private_public else "VALUE = 'public'\n")
        agent = source / "agent"; (agent / "src" / "controlstudio_agent").mkdir(parents=True)
        (agent / "src" / "controlstudio_agent" / "main.py").write_text("VALUE = 'agent'\n")
        (agent / "pyproject.toml").write_text(
            "[project]\nname = 'agent'\n\n[tool.uv.sources]\ncontracts = { path = '../contracts', editable = true }\n\n[tool.setuptools]\npackage-dir = {'' = 'src'}\n"
        )
        (agent / "node_modules").mkdir(); (agent / "node_modules" / "cache.js").write_text("cache")
        mcp = source / "mcp"; (mcp / "src" / "xg5000_control").mkdir(parents=True)
        (mcp / "src" / "xg5000_control" / "main.py").write_text(
            "import pydantic_ai\n" if private_mcp else "VALUE = 'mcp'\n"
        )
        (mcp / "pyproject.toml").write_text("[project]\nname = 'mcp'\n")
        (mcp / "artifacts").mkdir(); (mcp / "artifacts" / "secret.db").write_text("not copied")
        layout = {
            "components": {
                "platform": {"target_directory": "controlstudio-platform", "mappings": [{"from": "platform", "to": "host"}], "required_paths": ["host/public.py"]},
                "agent": {"target_directory": "controlstudio-agent", "mappings": [{"from": "agent", "to": "."}], "required_paths": ["pyproject.toml", "src/controlstudio_agent/main.py"]},
                "plc-mcp": {"target_directory": "controlstudio-plc-mcp", "mappings": [{"from": "mcp", "to": "."}], "required_paths": ["pyproject.toml", "src/xg5000_control/main.py"]},
            }
        }
        layout_path = root / "layout.json"; layout_path.write_text(json.dumps(layout))
        license_path = root / "INPUT-LICENSE"; license_path.write_text("Test license\n")
        notice_path = root / "INPUT-NOTICE"; notice_path.write_text("Test notice\n")
        metadata = {"repositories": {component: {
            "visibility": visibility, "license_path": str(license_path), "notice_path": str(notice_path),
        } for component, visibility in creator.COMPONENTS.items()}}
        metadata_path = root / "metadata.json"; metadata_path.write_text(json.dumps(metadata))
        return source, layout_path, metadata_path

    def create(self, root: Path, **kwargs: object) -> dict[str, object]:
        source, layout, metadata = self.fixture(root, **kwargs)
        return creator.create_repositories(source=source, workspace=root, layout_path=layout,
                                           metadata_path=metadata, execute=True, confirmation=creator.CONFIRMATION)

    def test_success_creates_clean_root_repositories_without_remote(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source, layout, metadata = self.fixture(root)
            original_agent = (source / "agent" / "pyproject.toml").read_text()
            result = creator.create_repositories(source=source, workspace=root, layout_path=layout,
                                                 metadata_path=metadata, execute=True, confirmation=creator.CONFIRMATION)
            self.assertTrue(result["executed"])
            self.assertIn("[tool.uv.sources]", original_agent)
            self.assertIn("[tool.uv.sources]", (source / "agent" / "pyproject.toml").read_text())
            for name in ("platform", "agent", "plc-mcp"):
                repo = root / f"controlstudio-{name}"
                self.assertEqual(subprocess.run(["git", "rev-list", "--count", "--all"], cwd=repo, text=True, capture_output=True, check=False).stdout.strip(), "1")
                self.assertEqual(subprocess.run(["git", "remote"], cwd=repo, text=True, capture_output=True, check=False).stdout.strip(), "")
                self.assertFalse((repo / ".gitmodules").exists())
                self.assertTrue((repo / "LICENSE").is_file() and (repo / "NOTICE").is_file())
                self.assertTrue((repo / ".gitignore").is_file())
            self.assertNotIn("[tool.uv.sources]", (root / "controlstudio-agent" / "pyproject.toml").read_text())
            self.assertFalse((root / "controlstudio-agent" / "agent").exists())
            self.assertFalse((root / "controlstudio-agent" / "node_modules").exists())
            self.assertFalse((root / "controlstudio-plc-mcp" / "artifacts").exists())

    def test_failure_rolls_back_all_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source, layout, metadata = self.fixture(root)
            before = sorted(path.relative_to(source).as_posix() for path in source.rglob("*"))
            with (
                mock.patch.object(
                    creator,
                    "_initialize_genesis",
                    side_effect=creator.CreationError("injected"),
                ),
                self.assertRaises(creator.CreationError),
            ):
                creator.create_repositories(source=source, workspace=root, layout_path=layout,
                                            metadata_path=metadata, execute=True, confirmation=creator.CONFIRMATION)
            self.assertEqual(before, sorted(path.relative_to(source).as_posix() for path in source.rglob("*")))
            self.assertFalse(any((root / f"controlstudio-{name}").exists() for name in creator.COMPONENTS))
            self.assertFalse(any(root.glob(".controlstudio-refactor-staging-*")))

    def test_default_dry_run_does_not_create_staging_or_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source, layout, metadata = self.fixture(root)
            result = creator.create_repositories(source=source, workspace=root, layout_path=layout,
                                                 metadata_path=metadata, execute=False)
            self.assertFalse(result["executed"])
            self.assertFalse(any((root / f"controlstudio-{name}").exists() for name in creator.COMPONENTS))
            self.assertFalse(any(root.glob(".controlstudio-refactor-staging-*")))

    def test_existing_target_is_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source, layout, metadata = self.fixture(root)
            target = root / "controlstudio-platform"; target.mkdir(); marker = target / "keep"; marker.write_text("keep")
            with self.assertRaises(creator.CreationError):
                creator.create_repositories(source=source, workspace=root, layout_path=layout,
                                            metadata_path=metadata, execute=True, confirmation=creator.CONFIRMATION)
            self.assertEqual(marker.read_text(), "keep")

    def test_target_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source, layout, metadata = self.fixture(root)
            value = json.loads(layout.read_text()); value["components"]["agent"]["target_directory"] = "../escaped"
            layout.write_text(json.dumps(value))
            with self.assertRaises(creator.CreationError):
                creator.create_repositories(source=source, workspace=root, layout_path=layout,
                                            metadata_path=metadata, execute=True, confirmation=creator.CONFIRMATION)
            self.assertFalse((root.parent / "escaped").exists())

    def test_private_code_in_public_snapshot_fails_before_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source, layout, metadata = self.fixture(root, private_public=True)
            with self.assertRaises(creator.CreationError):
                creator.create_repositories(source=source, workspace=root, layout_path=layout,
                                            metadata_path=metadata, execute=True, confirmation=creator.CONFIRMATION)
            self.assertFalse(any((root / f"controlstudio-{name}").exists() for name in creator.COMPONENTS))
            self.assertFalse(any(root.glob(".controlstudio-refactor-staging-*")))

    def test_private_mcp_partition_violation_fails_before_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source, layout, metadata = self.fixture(root, private_mcp=True)
            with self.assertRaises(creator.CreationError):
                creator.create_repositories(source=source, workspace=root, layout_path=layout,
                                            metadata_path=metadata, execute=True, confirmation=creator.CONFIRMATION)
            self.assertFalse(any((root / f"controlstudio-{name}").exists() for name in creator.COMPONENTS))
            self.assertFalse(any(root.glob(".controlstudio-refactor-staging-*")))


if __name__ == "__main__":
    unittest.main()
