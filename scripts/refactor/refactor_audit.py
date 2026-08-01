#!/usr/bin/env python3
"""Evidence-first guards for the ControlStudio three-repository refactor.

All commands are read-only with respect to a candidate repository.  They may
write a JSON evidence file only when ``--evidence`` is explicitly supplied.
In particular, ``sibling-plan`` *never* copies files, runs git init, adds a
remote, or pushes.  The eventual creation operation must remain a separately
reviewed, human-invoked release step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TEXT_EXTENSIONS = {
    ".c", ".cmd", ".cs", ".css", ".env", ".html", ".ini", ".js", ".json",
    ".md", ".ps1", ".py", ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
}
SECRET_PATTERNS = {
    "private-key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "openai-key": r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b",
    "github-token": r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b",
    "aws-access-key": r"\bAKIA[0-9A-Z]{16}\b",
    "assignment": r"(?i)\b(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"]{8,}",
}
PUBLIC_PRIVATE_MARKERS = {
    # Architecture and release documentation may identify private components.
    # History scanning must reject their implementation or user data, rather
    # than flagging a public statement of the supported process boundary.
    "private-python-import": r"(?m)^\s*(?:from|import)\s+(?:pydantic_ai|xg5000_control|controlstudio_agent)\b",
    "private-source-path": r"(?i)(?:^|[\"'])\.\.?/(?:agent|mcp)/(?:src|tests|profiles|fixtures)(?:/|[\"'])",
    "local-user-path": r"(?i)(?:[A-Z]:\\Users\\[^\\\r\n]+|/(?:home|Users)/[^/\s]+/)",
}


@dataclass
class Finding:
    kind: str
    location: str
    detail: str


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def command(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return command(["git", *args], repo)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_files(root: Path, excluded_directories: list[Path] | None = None) -> Iterable[Path]:
    ignored = {".git", ".venv", "node_modules", "dist", "build", ".pytest_cache", "__pycache__",
               ".mypy_cache", ".ruff_cache", ".tox", ".coverage", "coverage", ".next", "artifacts"}
    for directory, directories, files in os.walk(root):
        pruned = [
            name
            for name in directories
            if name in ignored
            or name.startswith(".pytest-")
            or name.endswith(".egg-info")
            or (Path(directory) / name / "pyvenv.cfg").is_file()
        ]
        if excluded_directories is not None:
            excluded_directories.extend(Path(directory) / name for name in pruned)
        directories[:] = [name for name in directories if name not in pruned]
        for name in files:
            path = Path(directory) / name
            if path.is_file():
                yield path


def scan_file(path: Path, root: Path, patterns: dict[str, str]) -> list[Finding]:
    if path.suffix.lower() not in TEXT_EXTENSIONS or path.stat().st_size > 2_000_000:
        return []
    try:
        contents = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    findings: list[Finding] = []
    for kind, expression in patterns.items():
        if re.search(expression, contents):
            findings.append(Finding(kind, path.relative_to(root).as_posix(), "matched policy pattern"))
    return findings


def write_evidence(path: str | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    evidence = Path(path).resolve()
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def finish(name: str, findings: list[Finding], evidence: str | None, **extra: Any) -> int:
    payload = {"check": name, "generated_at": now(), "passed": not findings,
               "findings": [asdict(finding) for finding in findings], **extra}
    write_evidence(evidence, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not findings else 1


def baseline(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    findings: list[Finding] = []
    if not (repo / ".git").exists():
        findings.append(Finding("not-git-repository", str(repo), "missing .git"))
    status = git(repo, "status", "--porcelain=v1") if not findings else None
    refs = git(repo, "show-ref", "--head") if not findings else None
    head = git(repo, "rev-parse", "HEAD") if not findings else None
    if status and status.stdout.strip():
        findings.append(Finding("dirty-worktree", ".", "baseline must record and preserve uncommitted changes"))
    if refs and refs.returncode:
        findings.append(Finding("missing-refs", ".git", refs.stderr.strip()))
    return finish("baseline", findings, args.evidence, repo=str(repo), head=head.stdout.strip() if head else None,
                  status=status.stdout.splitlines() if status else [], refs=refs.stdout.splitlines() if refs else [])


def archive_verify(args: argparse.Namespace) -> int:
    """Verify, without modifying it, the source bundle and independent mirror."""
    source, bundle, mirror = Path(args.source).resolve(), Path(args.bundle).resolve(), Path(args.mirror).resolve()
    findings: list[Finding] = []
    if not bundle.is_file():
        findings.append(Finding("missing-bundle", str(bundle), "stage 0 requires a verified Git bundle"))
    else:
        verified = git(source, "bundle", "verify", str(bundle))
        if verified.returncode:
            findings.append(Finding("bundle-invalid", str(bundle), verified.stderr.strip()))
    bare = git(mirror, "rev-parse", "--is-bare-repository") if mirror.exists() else None
    if not bare or bare.returncode or bare.stdout.strip() != "true":
        findings.append(Finding("mirror-not-bare", str(mirror), "stage 0 requires an independent bare mirror"))
    source_refs = git(source, "for-each-ref", "--format=%(objectname) %(refname)")
    mirror_refs = git(mirror, "for-each-ref", "--format=%(objectname) %(refname)") if bare and bare.returncode == 0 else None
    source_set = set(source_refs.stdout.splitlines())
    mirror_set = set(mirror_refs.stdout.splitlines()) if mirror_refs else set()
    missing = sorted(source_set - mirror_set)
    if missing:
        findings.append(Finding("mirror-missing-refs", str(mirror), ", ".join(missing[:20])))
    return finish("archive-verify", findings, args.evidence, source=str(source), bundle=str(bundle), mirror=str(mirror),
                  source_ref_count=len(source_set), mirror_ref_count=len(mirror_set))


def sibling_plan(args: argparse.Namespace) -> int:
    source, workspace = Path(args.source).resolve(), Path(args.workspace).resolve()
    findings: list[Finding] = []
    if not (source / ".git").exists():
        findings.append(Finding("source-not-git", str(source), "source must be the preserved monorepo"))
    if (workspace / ".git").exists():
        findings.append(Finding("workspace-is-git", str(workspace), "workspace parent must not be a Git repository"))
    targets = {name: (workspace / f"controlstudio-{name}").resolve() for name in ("platform", "agent", "plc-mcp")}
    for target in targets.values():
        if target.parent != workspace:
            findings.append(Finding("not-direct-sibling", str(target), "target escaped workspace"))
        if target == source or source in target.parents:
            findings.append(Finding("source-overwrite-risk", str(target), "target overlaps preserved source"))
        if target.exists():
            findings.append(Finding("target-exists", str(target), "refusing a future create plan over an existing path"))
    actions = [
        "PRECONDITION: all logical-boundary and integration gates must pass.",
        "COPY: use a verified snapshot into each empty direct sibling.",
        "INIT: create a clean genesis commit in each sibling only after public-history scan passes.",
        "PROHIBITED: add remote, push, nested repository, submodule, source sibling path dependency.",
    ]
    return finish("sibling-plan-dry-run", findings, args.evidence, source=str(source), workspace=str(workspace),
                  targets={key: str(value) for key, value in targets.items()}, actions=actions,
                  mutation_guarantee="No filesystem or git mutation was performed.")


def safe_relative(value: str) -> Path | None:
    """Return a safe snapshot-relative path, allowing `.` as the repo root."""
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


def copied_files(source: Path, source_rel: Path, destination_rel: Path,
                 excluded_directories: list[Path]) -> Iterable[tuple[Path, Path]]:
    origin = source / source_rel
    if origin.is_file():
        yield origin, destination_rel
        return
    for file in relative_files(origin, excluded_directories):
        yield file, destination_rel / file.relative_to(origin)


LOCAL_PATH_REFERENCE = re.compile(r"(?im)\bpath\s*=\s*[\"']([^\"']+)|[\"'](?:file|link):([^\"']+)")
UV_SOURCES_TABLE = re.compile(
    r"(?ms)^\s*\[tool\.uv\.sources\]\s*$.*?(?=^\s*\[|\Z)"
)


def local_path_is_internal(config_destination: Path, value: str, destinations: set[str]) -> bool:
    """A relative package path is valid only when its target is also mapped."""
    if Path(value).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", value):
        return False
    target = posixpath.normpath(posixpath.join(config_destination.parent.as_posix(), value.replace("\\", "/")))
    if target == ".." or target.startswith("../"):
        return False
    return any(destination == target or destination.startswith(target.rstrip("/") + "/") for destination in destinations)


def release_config_content(component: str, source_file: Path, content: str) -> str:
    """Model the one deliberate candidate transformation before dependency audit.

    The physical creator strips Agent's monorepo-only ``tool.uv.sources``
    table from the staged candidate.  The dry-run must inspect that exact
    release metadata while continuing to reject every other sibling path.
    """
    if component == "agent" and source_file.name == "pyproject.toml":
        return UV_SOURCES_TABLE.sub("", content)
    return content


def snapshot_plan(args: argparse.Namespace) -> int:
    """Validate the exact copy/flatten mapping without creating any target."""
    source, workspace, layout_path = Path(args.source).resolve(), Path(args.workspace).resolve(), Path(args.layout).resolve()
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    findings: list[Finding] = []
    if not (source / ".git").exists():
        findings.append(Finding("source-not-git", str(source), "preserved monorepo is required"))
    if (workspace / ".git").exists():
        findings.append(Finding("workspace-is-git", str(workspace), "workspace parent must not be Git"))
    plans: dict[str, Any] = {}
    for component, specification in layout.get("components", {}).items():
        target = (workspace / specification["target_directory"]).resolve()
        if target.parent != workspace or target == source or source in target.parents:
            findings.append(Finding("invalid-sibling-target", component, str(target)))
        if target.exists():
            findings.append(Finding("target-exists", component, str(target)))
        destinations: set[str] = set()
        planned: list[str] = []
        config_files: list[tuple[Path, Path]] = []
        excluded_generated: list[Path] = []
        for mapping in specification.get("mappings", []):
            source_rel, destination_rel = safe_relative(mapping["from"]), safe_relative(mapping["to"])
            if source_rel is None or destination_rel is None:
                findings.append(Finding("unsafe-mapping", component, json.dumps(mapping, sort_keys=True)))
                continue
            origin = source / source_rel
            if not origin.exists():
                findings.append(Finding("missing-mapping-source", component, source_rel.as_posix()))
                continue
            if origin == source / ".git" or ".git" in source_rel.parts:
                findings.append(Finding("git-in-snapshot", component, source_rel.as_posix()))
                continue
            for file, destination in copied_files(source, source_rel, destination_rel, excluded_generated):
                relative_destination = destination.as_posix()
                if relative_destination in destinations:
                    findings.append(Finding("snapshot-collision", component, relative_destination))
                    continue
                destinations.add(relative_destination)
                planned.append(relative_destination)
                if file.suffix.lower() in {".toml", ".json", ".yaml", ".yml", ".lock"}:
                    config_files.append((file, destination))
        for required in specification.get("required_paths", []):
            if required not in destinations:
                findings.append(Finding("missing-package-root-or-contract", component, required))
        for file, destination in config_files:
            content = release_config_content(
                component, file, file.read_text(encoding="utf-8", errors="replace")
            )
            for match in LOCAL_PATH_REFERENCE.finditer(content):
                local_path = match.group(1) or match.group(2)
                if not local_path_is_internal(destination, local_path, destinations):
                    findings.append(Finding("source-sibling-path-dependency", component,
                                            f"{file.relative_to(source).as_posix()}: {local_path}"))
        plans[component] = {"target": str(target), "mappings": specification.get("mappings", []),
                            "file_count": len(planned), "files_preview": sorted(planned)[:100],
                            "excluded_generated_directory_count": len(excluded_generated),
                            "excluded_generated_directories_preview": [path.relative_to(source).as_posix() for path in excluded_generated[:50]]}
    expected_components = {"platform", "agent", "plc-mcp"}
    if set(plans) != expected_components:
        findings.append(Finding("component-set", "layout.components", f"expected {sorted(expected_components)}"))
    return finish("snapshot-plan-dry-run", findings, args.evidence, source=str(source), workspace=str(workspace),
                  layout=str(layout_path), plans=plans,
                  forbidden_git_actions=["git init", "git remote add", "git push", "git submodule add"],
                  future_repository_policy={"remote": "none", "history": "clean-genesis-only", "submodules": "forbidden"},
                  mutation_guarantee="Mapping was enumerated in memory; no copy, mkdir, git init, remote, or push occurred.")


def partition(args: argparse.Namespace) -> int:
    candidate = Path(args.candidate).resolve()
    policy = json.loads(Path(args.policy).read_text(encoding="utf-8"))
    findings: list[Finding] = []
    if not candidate.is_dir():
        findings.append(Finding("missing-candidate", str(candidate), "candidate repository directory not found"))
        return finish("partition", findings, args.evidence)
    repository = policy["repositories"][args.component]
    excluded = tuple(repository.get("forbidden_path_prefixes", []))
    content_patterns = repository.get("forbidden_content_patterns", {})
    allowed_roots = tuple(repository.get("allowed_top_level", []))
    for file in relative_files(candidate):
        rel = file.relative_to(candidate).as_posix()
        top = rel.split("/", 1)[0]
        if allowed_roots and top not in allowed_roots and not top.startswith("."):
            findings.append(Finding("not-allowlisted", rel, "top-level path is not owned by this component"))
        if any(rel == prefix or rel.startswith(prefix.rstrip("/") + "/") for prefix in excluded):
            findings.append(Finding("forbidden-path", rel, "path belongs to another component"))
        findings.extend(scan_file(file, candidate, content_patterns))
    return finish("partition", findings, args.evidence, component=args.component, candidate=str(candidate),
                  policy=str(Path(args.policy).resolve()))


def reachable_blobs(repo: Path) -> Iterable[tuple[str, str]]:
    listing = git(repo, "rev-list", "--objects", "--all")
    if listing.returncode:
        raise RuntimeError(listing.stderr.strip())
    seen: set[str] = set()
    for line in listing.stdout.splitlines():
        oid, _, path = line.partition(" ")
        if oid in seen:
            continue
        seen.add(oid)
        object_type = git(repo, "cat-file", "-t", oid)
        if object_type.stdout.strip() == "blob":
            yield oid, path or "<historical blob>"


def public_history(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    findings: list[Finding] = []
    patterns = {**SECRET_PATTERNS, **PUBLIC_PRIVATE_MARKERS}
    try:
        blobs = list(reachable_blobs(repo))
    except RuntimeError as error:
        return finish("public-history", [Finding("git-error", str(repo), str(error))], args.evidence)
    for oid, hint in blobs:
        content = git(repo, "cat-file", "blob", oid)
        if content.returncode or "\x00" in content.stdout:
            continue
        for kind, expression in patterns.items():
            if re.search(expression, content.stdout):
                findings.append(Finding(kind, f"{oid[:12]}:{hint}", "matched reachable-history policy pattern"))
    remote_refs = git(repo, "for-each-ref", "refs/remotes", "--format=%(refname)")
    if remote_refs.stdout.strip():
        findings.append(Finding("remote-tracking-refs", "refs/remotes", "public genesis must not retain old remote history"))
    configured_remotes = git(repo, "remote")
    if configured_remotes.stdout.strip():
        findings.append(Finding("configured-remotes", ".git/config", "public local genesis must not configure a remote before review"))
    return finish("public-history", findings, args.evidence, repo=str(repo), scanned_blobs=len(blobs),
                  scope="all reachable blobs from --all, including non-HEAD branches and tags")


def verify_bom(args: argparse.Namespace) -> int:
    bom_path = Path(args.bom).resolve()
    bom = json.loads(bom_path.read_text(encoding="utf-8"))
    findings: list[Finding] = []
    required = {"bom_schema", "product_release", "contracts", "contract_artifact", "components"}
    for key in sorted(required - bom.keys()):
        findings.append(Finding("missing-bom-field", key, "required by stage 6"))
    if bom.get("bom_schema") != "controlstudio-bom/v1":
        findings.append(Finding("bom-schema", "bom_schema", "expected controlstudio-bom/v1"))
    required_contracts = {
        "api_contract", "event_contract", "agent_contract",
        "mcp_product_contract", "workspace_contract", "audit_contract",
    }
    contracts = bom.get("contracts")
    if not isinstance(contracts, dict):
        findings.append(Finding("missing-bom-field", "contracts", "must be an object"))
        contracts = {}
    for key in sorted(required_contracts - contracts.keys()):
        findings.append(Finding("missing-contract", f"contracts.{key}", "required by BOM v1"))
    seen: set[str] = set()
    artifact_root = Path(args.artifact_root).resolve() if args.artifact_root else bom_path.parent
    contract_artifact = bom.get("contract_artifact")
    if not isinstance(contract_artifact, dict):
        findings.append(Finding("missing-contract-artifact", "contract_artifact", "must be an object"))
    else:
        for key in ("artifact", "version", "sha256"):
            if key not in contract_artifact:
                findings.append(Finding("missing-contract-artifact-field", "contract_artifact", key))
        artifact = (artifact_root / contract_artifact.get("artifact", "")).resolve()
        if artifact_root not in artifact.parents and artifact != artifact_root:
            findings.append(Finding("artifact-path-escape", "contract_artifact", str(artifact)))
        elif not artifact.is_file():
            findings.append(Finding("missing-artifact", "contract_artifact", str(artifact)))
        elif contract_artifact.get("sha256") != sha256(artifact):
            findings.append(Finding("sha256-mismatch", "contract_artifact", str(artifact)))
    for component in bom.get("components", []):
        component_id = component.get("id", "<missing-id>")
        if component_id in seen:
            findings.append(Finding("duplicate-component", component_id, "BOM component ids must be unique"))
        seen.add(component_id)
        for key in ("id", "version", "sha256", "artifact", "contracts", "signature"):
            if key not in component:
                findings.append(Finding("missing-component-field", component_id, key))
        if component.get("contracts") != contracts:
            findings.append(Finding("contract-mismatch", component_id, "component contracts must exactly match BOM contracts"))
        artifact = (artifact_root / component.get("artifact", "")).resolve()
        if artifact_root not in artifact.parents and artifact != artifact_root:
            findings.append(Finding("artifact-path-escape", component_id, str(artifact)))
        elif not artifact.is_file():
            findings.append(Finding("missing-artifact", component_id, str(artifact)))
        elif component.get("sha256") != sha256(artifact):
            findings.append(Finding("sha256-mismatch", component_id, str(artifact)))
    expected = {"platform", "agent", "plc-mcp"}
    if seen != expected:
        findings.append(Finding("component-set", "components", f"expected {sorted(expected)}, got {sorted(seen)}"))
    return finish("bom", findings, args.evidence, bom=str(bom_path), artifact_root=str(artifact_root))


def run_manifest(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    findings: list[Finding] = []
    executions: list[dict[str, Any]] = []
    for item in manifest.get("repositories", []):
        repo = Path(item["path"]).resolve()
        for phase in ("build", "test"):
            command_line = item.get(phase)
            if not command_line:
                findings.append(Finding("missing-command", item.get("id", str(repo)), phase))
                continue
            result = command(command_line, repo)
            executions.append({"id": item.get("id"), "phase": phase, "command": command_line,
                               "returncode": result.returncode, "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:]})
            if result.returncode:
                findings.append(Finding("command-failed", item.get("id", str(repo)), f"{phase}: exit {result.returncode}"))
    e2e = manifest.get("packaged_e2e")
    if not e2e:
        findings.append(Finding("missing-packaged-e2e", "packaged_e2e", "artifact-only E2E command is required"))
    else:
        if e2e.get("artifact_only") is not True:
            findings.append(Finding("e2e-not-artifact-only", "packaged_e2e.artifact_only", "must be explicitly true"))
        for artifact in e2e.get("artifacts", []):
            if not Path(artifact).resolve().is_file():
                findings.append(Finding("missing-e2e-artifact", "packaged_e2e.artifacts", artifact))
        result = command(e2e["command"], Path(e2e["cwd"]).resolve())
        executions.append({"id": "packaged-e2e", "phase": "e2e", "command": e2e["command"], "returncode": result.returncode,
                           "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:]})
        if result.returncode:
            findings.append(Finding("command-failed", "packaged-e2e", f"exit {result.returncode}"))
    mock = manifest.get("platform_mock")
    if not mock:
        findings.append(Finding("missing-platform-mock", "platform_mock", "public Platform mock execution is required"))
    else:
        result = command(mock["command"], Path(mock["cwd"]).resolve())
        executions.append({"id": "platform-mock", "phase": "mock", "command": mock["command"], "returncode": result.returncode,
                           "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:]})
        if result.returncode:
            findings.append(Finding("command-failed", "platform-mock", f"exit {result.returncode}"))
    return finish("independent-build-and-e2e", findings, args.evidence, manifest=str(manifest_path), executions=executions)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    def add_evidence(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--evidence", help="optional JSON evidence output path")
    sub = commands.add_parser("baseline"); sub.add_argument("--repo", required=True); add_evidence(sub); sub.set_defaults(func=baseline)
    sub = commands.add_parser("archive-verify"); sub.add_argument("--source", required=True); sub.add_argument("--bundle", required=True); sub.add_argument("--mirror", required=True); add_evidence(sub); sub.set_defaults(func=archive_verify)
    sub = commands.add_parser("sibling-plan"); sub.add_argument("--source", required=True); sub.add_argument("--workspace", required=True); add_evidence(sub); sub.set_defaults(func=sibling_plan)
    sub = commands.add_parser("snapshot-plan"); sub.add_argument("--source", required=True); sub.add_argument("--workspace", required=True); sub.add_argument("--layout", required=True); add_evidence(sub); sub.set_defaults(func=snapshot_plan)
    sub = commands.add_parser("partition"); sub.add_argument("--candidate", required=True); sub.add_argument("--component", choices=("platform", "agent", "plc-mcp"), required=True); sub.add_argument("--policy", required=True); add_evidence(sub); sub.set_defaults(func=partition)
    sub = commands.add_parser("public-history"); sub.add_argument("--repo", required=True); add_evidence(sub); sub.set_defaults(func=public_history)
    sub = commands.add_parser("verify-bom"); sub.add_argument("--bom", required=True); sub.add_argument("--artifact-root"); add_evidence(sub); sub.set_defaults(func=verify_bom)
    sub = commands.add_parser("run-manifest"); sub.add_argument("--manifest", required=True); add_evidence(sub); sub.set_defaults(func=run_manifest)
    return result


if __name__ == "__main__":
    parsed = parser().parse_args()
    sys.exit(parsed.func(parsed))
