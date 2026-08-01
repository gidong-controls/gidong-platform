#!/usr/bin/env python3
"""Transactionally create the three local ControlStudio repositories.

The CLI is a dry-run unless both ``--execute`` and the exact confirmation are
provided.  It never configures a remote, invokes push, or creates submodules.
All repositories are completely prepared in a workspace-local staging
directory before atomic moves expose the final sibling paths.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIRMATION = "CREATE-THREE-LOCAL-REPOSITORIES"
COMPONENTS = {"platform": "public", "agent": "private", "plc-mcp": "private"}
PARTITION_POLICY_PATH = Path(__file__).with_name("partition-policy.json")
IGNORED_DIRECTORIES = {
    ".git", ".venv", "node_modules", "dist", "build", ".pytest_cache", "__pycache__",
    ".mypy_cache", ".ruff_cache", ".tox", ".coverage", "coverage", ".next", "artifacts",
}
LOCAL_PATH_REFERENCE = re.compile(
    r"(?im)\bpath\s*=\s*[\"']([^\"']+)|[\"'](?:file|link):([^\"']+)"
)
PUBLIC_SECRET_PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "openai-key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "local-user-path": re.compile(r"(?i)(?:[A-Z]:\\Users\\[^\\\r\n]+|/(?:home|Users)/[^/\s]+/)"),
    "private-import": re.compile(r"(?m)^\s*(?:from|import)\s+(?:pydantic_ai|xg5000_control)\b"),
}
PUBLIC_FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".pfx", ".p12"}
REPOSITORY_IGNORE = """# Local environments, caches, credentials, and build output\n.venv/\n__pycache__/\n.pytest_cache/\n.pytest-*/\n.ruff_cache/\n.mypy_cache/\n*.egg-info/\nbuild/\ndist/\nnode_modules/\nartifacts/\n.env\n.env.*\n!.env.example\n*.db\n*.sqlite\n*.sqlite3\n*.log\n"""


class CreationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CopyItem:
    source: Path
    destination: Path


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False)


def _git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return _run(["git", *arguments], repo)


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise CreationError(f"unsafe relative path: {value}")
    return path


def _is_ignored_directory(name: str) -> bool:
    return name in IGNORED_DIRECTORIES or name.startswith(".pytest-") or name.endswith(".egg-info")


def _files(root: Path) -> Iterable[Path]:
    for directory, directories, files in os.walk(root):
        for name in directories:
            if (Path(directory) / name).is_symlink():
                raise CreationError(f"snapshot directory symlink is forbidden: {Path(directory) / name}")
        if (".git" in directories and Path(directory) != root) or ".git" in files:
            raise CreationError(f"nested Git metadata is forbidden: {Path(directory) / '.git'}")
        directories[:] = [name for name in directories if not _is_ignored_directory(name)]
        for name in files:
            if name == ".gitmodules":
                raise CreationError(f"submodule metadata is forbidden: {Path(directory) / name}")
            path = Path(directory) / name
            if path.is_symlink():
                raise CreationError(f"snapshot file symlink is forbidden: {path}")
            if path.is_file():
                yield path


def _remove_tree(path: Path) -> None:
    """Remove only a validated staging/rollback tree, including readonly Git objects."""

    def retry_writable(function: Callable[..., None], name: str, _exception: Any) -> None:
        os.chmod(name, stat.S_IWRITE)
        function(name)

    shutil.rmtree(path, onerror=retry_writable)


def _copy_plan(source: Path, specification: dict[str, Any]) -> list[CopyItem]:
    planned: list[CopyItem] = []
    destinations: set[str] = set()
    for mapping in specification.get("mappings", []):
        source_rel = _safe_relative(str(mapping["from"]))
        destination_rel = _safe_relative(str(mapping["to"]))
        origin = source / source_rel
        if not origin.exists() or ".git" in source_rel.parts:
            raise CreationError(f"missing or forbidden mapping source: {source_rel.as_posix()}")
        pairs: Iterable[tuple[Path, Path]]
        if origin.is_file():
            pairs = [(origin, destination_rel)]
        else:
            pairs = ((file, destination_rel / file.relative_to(origin)) for file in _files(origin))
        for file, destination in pairs:
            normalized = destination.as_posix()
            if normalized in destinations:
                raise CreationError(f"snapshot destination collision: {normalized}")
            destinations.add(normalized)
            planned.append(CopyItem(file, destination))
    for required in specification.get("required_paths", []):
        if str(required) not in destinations:
            raise CreationError(f"required package or contract is absent: {required}")
    return planned


def _strip_uv_sources(pyproject: Path) -> None:
    """Remove the complete monorepo-only table without changing the source."""
    if not pyproject.is_file():
        raise CreationError("Agent release pyproject.toml is missing")
    lines = pyproject.read_text(encoding="utf-8").splitlines(keepends=True)
    output: list[str] = []
    skipping = False
    found = False
    table = re.compile(r"^\s*\[tool\.uv\.sources\]\s*(?:#.*)?$")
    next_table = re.compile(r"^\s*\[")
    for line in lines:
        if table.match(line.rstrip("\r\n")):
            skipping = found = True
            while output and not output[-1].strip():
                output.pop()
            continue
        if skipping and next_table.match(line):
            skipping = False
            if output and output[-1].strip():
                output.append("\n")
        if not skipping:
            output.append(line)
    value = "".join(output).rstrip() + "\n"
    if found:
        pyproject.write_text(value, encoding="utf-8", newline="\n")
    if "[tool.uv.sources]" in pyproject.read_text(encoding="utf-8"):
        raise CreationError("Agent release pyproject still contains tool.uv.sources")


def _validate_local_paths(repo: Path) -> None:
    destinations = {path.relative_to(repo).as_posix() for path in _files(repo)}
    for file in _files(repo):
        if file.suffix.lower() not in {".toml", ".json", ".yaml", ".yml", ".lock"}:
            continue
        content = file.read_text(encoding="utf-8", errors="replace")
        config = file.relative_to(repo)
        for match in LOCAL_PATH_REFERENCE.finditer(content):
            reference = (match.group(1) or match.group(2)).replace("\\", "/")
            if Path(reference).is_absolute() or re.match(r"^[A-Za-z]:/", reference):
                raise CreationError(f"absolute source dependency in {config.as_posix()}: {reference}")
            import posixpath
            target = posixpath.normpath(posixpath.join(config.parent.as_posix(), reference))
            if target == ".." or target.startswith("../"):
                raise CreationError(f"sibling source dependency in {config.as_posix()}: {reference}")
            if not any(item == target or item.startswith(target.rstrip("/") + "/") for item in destinations):
                raise CreationError(f"unmapped local dependency in {config.as_posix()}: {reference}")


def _scan_public_tree(repo: Path) -> None:
    for file in _files(repo):
        relative = file.relative_to(repo).as_posix()
        parts = relative.split("/")
        if parts[0] in {"agent", "mcp", "artifacts"}:
            raise CreationError(f"private path in public snapshot: {relative}")
        if file.suffix.lower() in PUBLIC_FORBIDDEN_SUFFIXES or file.name.lower() == ".env":
            raise CreationError(f"sensitive artifact in public snapshot: {relative}")
        if file.stat().st_size > 2_000_000:
            continue
        text = file.read_text(encoding="utf-8", errors="replace")
        for name, pattern in PUBLIC_SECRET_PATTERNS.items():
            if pattern.search(text):
                raise CreationError(f"public security scan found {name}: {relative}")


def _scan_partition(repo: Path, component: str) -> None:
    """Apply the repository boundary policy to the exact staged snapshot."""

    policy = json.loads(PARTITION_POLICY_PATH.read_text(encoding="utf-8"))
    repository = policy.get("repositories", {}).get(component)
    if not isinstance(repository, dict):
        raise CreationError(f"partition policy is missing component: {component}")
    allowed_roots = tuple(str(item) for item in repository.get("allowed_top_level", ()))
    forbidden_prefixes = tuple(str(item) for item in repository.get("forbidden_path_prefixes", ()))
    patterns = {
        str(name): re.compile(str(expression))
        for name, expression in repository.get("forbidden_content_patterns", {}).items()
    }
    findings: list[str] = []
    for file in _files(repo):
        relative = file.relative_to(repo).as_posix()
        top_level = relative.split("/", 1)[0]
        if allowed_roots and top_level not in allowed_roots and not top_level.startswith("."):
            findings.append(f"not-allowlisted:{relative}")
        if any(relative == prefix or relative.startswith(f"{prefix.rstrip('/')}/") for prefix in forbidden_prefixes):
            findings.append(f"forbidden-path:{relative}")
        if file.suffix.lower() in {".py", ".toml", ".json", ".yaml", ".yml", ".md", ".ps1", ".ts", ".tsx"}:
            text = file.read_text(encoding="utf-8", errors="replace")
            findings.extend(
                f"{name}:{relative}" for name, pattern in patterns.items() if pattern.search(text)
            )
    if findings:
        raise CreationError(
            f"{component} partition/privacy scan failed: {', '.join(sorted(findings)[:20])}"
        )


def _copy_required_metadata(repo: Path, component: str, metadata: dict[str, Any]) -> None:
    item = metadata.get("repositories", {}).get(component)
    if not isinstance(item, dict) or item.get("visibility") != COMPONENTS[component]:
        raise CreationError(f"visibility metadata is missing or invalid for {component}")
    for field, destination in (("license_path", "LICENSE"), ("notice_path", "NOTICE")):
        raw = item.get(field)
        if not isinstance(raw, str) or not Path(raw).is_absolute() or not Path(raw).resolve().is_file():
            raise CreationError(f"{field} is required for {component}")
        shutil.copy2(Path(raw).resolve(), repo / destination)
    repository_metadata = {
        "schema_version": 1,
        "component": component,
        "visibility": COMPONENTS[component],
        "history": "clean-genesis",
        "remote": None,
    }
    (repo / ".controlstudio-repository.json").write_text(
        json.dumps(repository_metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _ensure_repository_ignore(repo: Path) -> None:
    """Every clean-genesis repository owns ignore rules for local byproducts."""

    ignore = repo / ".gitignore"
    if not ignore.exists():
        ignore.write_text(REPOSITORY_IGNORE, encoding="utf-8", newline="\n")


def _scan_public_index(repo: Path) -> None:
    """Verify the exact index that would become the first public commit."""
    indexed = _git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR")
    if indexed.returncode:
        raise CreationError(indexed.stderr.strip() or "public index could not be enumerated")
    indexed_paths = {line.strip().replace("\\", "/") for line in indexed.stdout.splitlines() if line.strip()}
    working_paths = {file.relative_to(repo).as_posix() for file in _files(repo)}
    if indexed_paths != working_paths:
        raise CreationError("public staged index does not exactly match the validated snapshot")
    _scan_public_tree(repo)
    written = _git(repo, "write-tree")
    if written.returncode or not re.fullmatch(r"[a-f0-9]{40,64}", written.stdout.strip()):
        raise CreationError("public staged tree could not be materialized for pre-commit review")


def _initialize_genesis(repo: Path, component: str, *, precommit: Callable[[Path], None] | None = None) -> None:
    initialized = _git(repo, "init", "--initial-branch=main")
    if initialized.returncode:
        raise CreationError(initialized.stderr.strip() or f"git init failed for {component}")
    for key, value in (("user.name", "ControlStudio Local Refactor"), ("user.email", "local-refactor@invalid")):
        configured = _git(repo, "config", key, value)
        if configured.returncode:
            raise CreationError(configured.stderr.strip())
    added = _git(repo, "add", "--all")
    if added.returncode:
        raise CreationError(added.stderr.strip())
    if precommit is not None:
        precommit(repo)
    committed = _git(repo, "commit", "-m", f"chore: clean genesis for controlstudio-{component}")
    if committed.returncode:
        raise CreationError(committed.stderr.strip())
    count = _git(repo, "rev-list", "--count", "--all")
    remote = _git(repo, "remote")
    status = _git(repo, "status", "--porcelain=v1")
    if count.stdout.strip() != "1" or remote.stdout.strip() or status.stdout.strip() or (repo / ".gitmodules").exists():
        raise CreationError(f"genesis invariants failed for {component}")


def _verify_public_history(repo: Path) -> None:
    if _git(repo, "rev-list", "--count", "--all").stdout.strip() != "1":
        raise CreationError("public history is not a single clean genesis")
    if _git(repo, "remote").stdout.strip() or _git(repo, "for-each-ref", "refs/remotes", "--format=%(refname)").stdout.strip():
        raise CreationError("public history contains remote configuration or refs")
    _scan_public_tree(repo)


def _validate_inputs(source: Path, workspace: Path, layout: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Path]:
    if not (source / ".git").exists():
        raise CreationError("source must be the preserved Git repository")
    if not workspace.is_dir() or (workspace / ".git").exists():
        raise CreationError("workspace must exist and must not be a Git repository")
    if source.parent != workspace:
        raise CreationError("source must be a direct child of the workspace")
    specifications = layout.get("components")
    if not isinstance(specifications, dict) or set(specifications) != set(COMPONENTS):
        raise CreationError("layout must define exactly platform, agent, and plc-mcp")
    targets: dict[str, Path] = {}
    for component, expected_visibility in COMPONENTS.items():
        specification = specifications[component]
        directory = _safe_relative(str(specification.get("target_directory", "")))
        target = (workspace / directory).resolve()
        if target.parent != workspace or target == source or target in source.parents or source in target.parents:
            raise CreationError(f"target is not a non-overlapping direct sibling: {target}")
        if target.exists():
            raise CreationError(f"target already exists: {target}")
        metadata_item = metadata.get("repositories", {}).get(component, {})
        if metadata_item.get("visibility") != expected_visibility:
            raise CreationError(f"expected {expected_visibility} visibility for {component}")
        for field in ("license_path", "notice_path"):
            raw = metadata_item.get(field)
            if not isinstance(raw, str) or not Path(raw).is_absolute() or not Path(raw).resolve().is_file():
                raise CreationError(f"{field} is required for {component}")
        targets[component] = target
    return targets


def create_repositories(*, source: Path, workspace: Path, layout_path: Path, metadata_path: Path,
                        execute: bool, confirmation: str | None = None) -> dict[str, Any]:
    source, workspace = source.resolve(), workspace.resolve()
    layout = json.loads(layout_path.resolve().read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.resolve().read_text(encoding="utf-8"))
    targets = _validate_inputs(source, workspace, layout, metadata)
    plans = {component: _copy_plan(source, layout["components"][component]) for component in COMPONENTS}
    if not execute:
        return {"executed": False, "targets": {key: str(value) for key, value in targets.items()},
                "file_counts": {key: len(value) for key, value in plans.items()}}
    if confirmation != CONFIRMATION:
        raise CreationError(f"execute requires exact confirmation: {CONFIRMATION}")
    staging_root = Path(tempfile.mkdtemp(prefix=".controlstudio-refactor-staging-", dir=workspace))
    moved: list[Path] = []
    try:
        staged: dict[str, Path] = {}
        for component in COMPONENTS:
            repo = staging_root / targets[component].name
            repo.mkdir()
            for item in plans[component]:
                destination = repo / item.destination
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item.source, destination)
            _copy_required_metadata(repo, component, metadata)
            _ensure_repository_ignore(repo)
            if component == "agent":
                _strip_uv_sources(repo / "pyproject.toml")
            _validate_local_paths(repo)
            # Check the exact copied files before Git can create any objects.
            # This leaves failed policy runs with only ordinary staged files,
            # which makes rollback reliable on Windows as well as Unix.
            _scan_partition(repo, component)
            if component == "platform":
                # This exact staged tree is what `git add` and the genesis commit consume.
                _scan_public_tree(repo)
            _initialize_genesis(
                repo,
                component,
                precommit=_scan_public_index if component == "platform" else None,
            )
            if component == "platform":
                _verify_public_history(repo)
            staged[component] = repo
        # Recheck immediately before exposing any target to close TOCTOU races.
        if any(target.exists() for target in targets.values()):
            raise CreationError("a sibling target appeared during staging")
        for component in COMPONENTS:
            target = targets[component]
            if target.exists():
                raise CreationError(f"target appeared before atomic move: {target}")
            # On the supported Windows release platform os.rename is atomic
            # and refuses an existing destination directory.
            os.rename(staged[component], target)
            moved.append(target)
        return {"executed": True, "targets": {key: str(value) for key, value in targets.items()},
                "commits": {key: _git(value, "rev-parse", "HEAD").stdout.strip() for key, value in targets.items()}}
    except BaseException:
        for target in reversed(moved):
            if target.exists() and target.parent == workspace:
                _remove_tree(target)
        raise
    finally:
        if staging_root.exists() and staging_root.parent == workspace:
            _remove_tree(staging_root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--layout", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirmation")
    args = parser.parse_args()
    try:
        result = create_repositories(
            source=Path(args.source), workspace=Path(args.workspace), layout_path=Path(args.layout),
            metadata_path=Path(args.metadata), execute=args.execute, confirmation=args.confirmation,
        )
    except (CreationError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}))
        return 1
    print(json.dumps({"passed": True, **result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
