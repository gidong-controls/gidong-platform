# Portable Windows packaging and BOM

`packaging/` composes three pre-built immutable files and the public
`controlstudio-contracts` wheel: `controlstudio-platform.exe`,
`controlstudio-agent.exe`, `controlstudio-plc-mcp.exe`, and
`controlstudio_contracts-<version>-py3-none-any.whl`. A portable release
contains those files, `controlstudio.bom.json`, and `Start-ControlStudio.ps1`;
it has no source checkout, Python, Node, or uv runtime dependency.

## Build boundary

Build the public contracts wheel and Platform executable in a disposable
Windows build environment with pinned build dependencies:

```powershell
python packaging/scripts/build_contracts_wheel.py --output dist/contracts
uv run --directory host --with pyinstaller python ../packaging/scripts/build_standalone.py --output dist/platform --clean
```

Agent build/test tooling takes an explicit public wheel and a caller-owned
environment. It never discovers sibling checkout paths or checked-in binary
artifacts:

```powershell
python agent/scripts/with_contract_artifact.py --contracts-wheel dist/contracts/controlstudio_contracts-0.1.0-py3-none-any.whl --venv .contract-build-env -- python scripts/build_standalone.py --contracts-wheel dist/contracts/controlstudio_contracts-0.1.0-py3-none-any.whl --output dist/agent --pyinstaller .contract-build-env/Scripts/pyinstaller.exe --clean
```

Build the MCP artifact in its isolated build environment. Release composition
then consumes only the immutable command-line inputs:

```powershell
uv run --directory mcp --with pyinstaller python mcp/scripts/build_standalone.py --output dist/plc-mcp --clean
```

Compose the release by passing every artifact and every component version
explicitly. The assembler refuses a pre-existing output directory, duplicate
names, missing/tampered inputs, and a wheel whose embedded version differs
from the version recorded in the BOM:

```powershell
python packaging/scripts/assemble_release.py --output dist/release --platform-artifact dist/platform/controlstudio-platform.exe --platform-version 0.1.0 --agent-artifact dist/agent/controlstudio-agent.exe --agent-version 0.1.0 --mcp-artifact dist/plc-mcp/controlstudio-plc-mcp.exe --mcp-version 0.1.0 --contracts-wheel dist/contracts/controlstudio_contracts-0.1.0-py3-none-any.whl --contracts-version 0.1.0 --product-release 0.1.0 --contracts packaging/contracts-v1.json
```

`Start-ControlStudio.ps1` validates the BOM schema, component identity, public
contracts-wheel hash, all six contract versions, safe artifact names, and
SHA-256 before it starts Platform. It passes immutable sidecar paths to
Platform, which retains supervision and creates the child transports.

## Signing policy

`unsigned` and `development-hmac-sha256` are explicitly
`not-for-production`. HMAC is local tamper detection only and requires a
separately held `CONTROLSTUDIO_PACKAGING_DEV_SIGNING_KEY`; it is not publisher
identity. `--RequireProductionSignature` rejects both. Production release must
independently verify Authenticode and record exact publisher/thumbprint
metadata in the BOM. No publisher certificate is present here, so this work
does **not** claim production signing.

```powershell
python -m unittest discover -s packaging/tests -v
```
