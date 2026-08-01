from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
from pathlib import Path
from typing import Any

COMPONENT_IDS = ("platform", "agent", "plc-mcp")
REQUIRED_CONTRACTS = (
    "api_contract",
    "event_contract",
    "agent_contract",
    "mcp_product_contract",
    "workspace_contract",
    "audit_contract",
)
SUPPORTED_SIGNATURE_KINDS = {"unsigned", "development-hmac-sha256", "authenticode"}


class PackagingError(ValueError):
    """Raised for malformed, mismatched, or untrusted product releases."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PackagingError(f"{field} must be a non-empty string")
    return value


def _validate_contracts(manifest: dict[str, Any], expected: dict[str, str] | None = None) -> dict[str, str]:
    contracts = manifest.get("contracts")
    if not isinstance(contracts, dict):
        raise PackagingError("contracts must be an object")
    normalized = {name: _require_text(contracts.get(name), f"contracts.{name}") for name in REQUIRED_CONTRACTS}
    if expected and normalized != expected:
        raise PackagingError("component contract versions do not exactly match the BOM")
    return normalized


def _signature_payload(component: dict[str, Any]) -> dict[str, Any]:
    clone = dict(component)
    clone.pop("signature", None)
    return clone


def _validate_signature(component: dict[str, Any], *, require_production: bool) -> None:
    signature = component.get("signature")
    if not isinstance(signature, dict):
        raise PackagingError("signature metadata is required")
    kind = signature.get("kind")
    if kind not in SUPPORTED_SIGNATURE_KINDS:
        raise PackagingError("unsupported signature kind")
    if kind == "unsigned":
        if signature.get("status") != "not-for-production":
            raise PackagingError("unsigned artifacts must be labelled not-for-production")
        if require_production:
            raise PackagingError("production signing is required; unsigned artifact rejected")
        return
    if kind == "development-hmac-sha256":
        if signature.get("status") != "not-for-production":
            raise PackagingError("development signature must be labelled not-for-production")
        key = os.environ.get("CONTROLSTUDIO_PACKAGING_DEV_SIGNING_KEY")
        if not key:
            raise PackagingError("development signing key is required to verify this artifact")
        actual = hmac.new(key.encode("utf-8"), _canonical(_signature_payload(component)), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(actual, str(signature.get("value", ""))):
            raise PackagingError("development signature does not verify")
        if require_production:
            raise PackagingError("production signing is required; development signature rejected")
        return
    # Authenticode metadata is intentionally fail-closed here. A release pipeline
    # must attach the verified publisher/thumbprint assertion after WinVerifyTrust.
    if signature.get("status") != "verified" or not _require_text(signature.get("publisher"), "signature.publisher") or not _require_text(signature.get("thumbprint"), "signature.thumbprint"):
        raise PackagingError("invalid Authenticode signature metadata")


def _artifact_record(supplied: dict[str, Any], *, field: str) -> dict[str, str]:
    artifact = Path(_require_text(supplied.get("artifact"), f"{field}.artifact")).resolve()
    if not artifact.is_file():
        raise PackagingError(f"artifact does not exist: {artifact}")
    return {
        "artifact": artifact.name,
        "version": _require_text(supplied.get("version"), f"{field}.version"),
        "sha256": sha256_file(artifact),
    }


def _verify_artifact_record(record: Any, artifact_directory: Path, *, field: str) -> dict[str, str]:
    if not isinstance(record, dict):
        raise PackagingError(f"{field} must be an object")
    artifact_name = _require_text(record.get("artifact"), f"{field}.artifact")
    artifact = (artifact_directory / artifact_name).resolve()
    if artifact.parent != artifact_directory or not artifact.is_file():
        raise PackagingError(f"{field} path is missing or escapes artifact directory")
    if not hmac.compare_digest(sha256_file(artifact), _require_text(record.get("sha256"), f"{field}.sha256")):
        raise PackagingError(f"SHA-256 mismatch for {field}")
    _require_text(record.get("version"), f"{field}.version")
    return {"artifact": artifact_name, "version": str(record["version"]), "sha256": str(record["sha256"])}


def build_bom(
    *, product_release: str, components: dict[str, dict[str, Any]], contracts: dict[str, str],
    contract_artifact: dict[str, Any],
    signing: str = "unsigned", signing_key: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic BOM from immutable artifact files.

    `development-hmac-sha256` is only a local tamper-evidence mode. It is not
    and cannot be represented as a production publisher signature.
    """
    normalized_contracts = {name: _require_text(contracts.get(name), name) for name in REQUIRED_CONTRACTS}
    if set(components) != set(COMPONENT_IDS):
        raise PackagingError("exactly platform, agent, and plc-mcp components are required")
    result: list[dict[str, Any]] = []
    for component_id in COMPONENT_IDS:
        supplied = components[component_id]
        artifact_record = _artifact_record(supplied, field=component_id)
        component = {
            "id": component_id,
            "version": artifact_record["version"],
            "artifact": artifact_record["artifact"],
            "sha256": artifact_record["sha256"],
            "contracts": dict(normalized_contracts),
        }
        if signing == "unsigned":
            component["signature"] = {"kind": "unsigned", "status": "not-for-production"}
        elif signing == "development-hmac-sha256":
            if not signing_key:
                raise PackagingError("a development signing key is required")
            component["signature"] = {
                "kind": "development-hmac-sha256", "status": "not-for-production",
                "key_id": "local-development",
                "value": hmac.new(signing_key.encode("utf-8"), _canonical(component), hashlib.sha256).hexdigest(),
            }
        else:
            raise PackagingError("this tool cannot create a production Authenticode signature")
        result.append(component)
    return {
        "bom_schema": "controlstudio-bom/v1",
        "product_release": _require_text(product_release, "product_release"),
        "contracts": normalized_contracts,
        "contract_artifact": _artifact_record(contract_artifact, field="contract_artifact"),
        "components": result,
    }


def verify_bom(bom: dict[str, Any], artifact_directory: Path, *, require_production: bool = False) -> None:
    if bom.get("bom_schema") != "controlstudio-bom/v1":
        raise PackagingError("unsupported BOM schema")
    expected_contracts = _validate_contracts(bom)
    components = bom.get("components")
    if not isinstance(components, list) or [item.get("id") if isinstance(item, dict) else None for item in components] != list(COMPONENT_IDS):
        raise PackagingError("BOM components must be the complete ordered component set")
    root = artifact_directory.resolve()
    _verify_artifact_record(bom.get("contract_artifact"), root, field="contract_artifact")
    for component in components:
        artifact_name = _require_text(component.get("artifact"), "component.artifact")
        artifact = (root / artifact_name).resolve()
        if artifact.parent != root or not artifact.is_file():
            raise PackagingError("artifact path is missing or escapes artifact directory")
        if not hmac.compare_digest(sha256_file(artifact), _require_text(component.get("sha256"), "component.sha256")):
            raise PackagingError(f"SHA-256 mismatch for {component.get('id')}")
        _require_text(component.get("version"), "component.version")
        _validate_contracts(component, expected_contracts)
        _validate_signature(component, require_production=require_production)


def assemble(bom_path: Path, artifact_directory: Path, output_directory: Path, *, require_production: bool = False) -> Path:
    bom = json.loads(bom_path.read_text(encoding="utf-8"))
    verify_bom(bom, artifact_directory, require_production=require_production)
    if output_directory.exists():
        raise PackagingError(f"output already exists: {output_directory}")
    output_directory.mkdir(parents=True)
    shutil.copy2(bom_path, output_directory / "controlstudio.bom.json")
    contract_artifact = _verify_artifact_record(bom["contract_artifact"], artifact_directory.resolve(), field="contract_artifact")
    shutil.copy2(artifact_directory / contract_artifact["artifact"], output_directory / contract_artifact["artifact"])
    for component in bom["components"]:
        shutil.copy2(artifact_directory / component["artifact"], output_directory / component["artifact"])
    return output_directory
