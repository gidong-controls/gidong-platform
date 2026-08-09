# Windows product installer, internal portable payload, and BOM

소비자 배포와 독립 컴포넌트 버전, 업데이트 및 rollback의 권위 정책은
[PRODUCT_DISTRIBUTION_AND_VERSIONING.md](PRODUCT_DISTRIBUTION_AND_VERSIONING.md)를
따른다. 특히 installer는 private Git 저장소나 source를 내려받지 않으며, private
release pipeline이 미리 빌드하고 검증한 immutable artifact만 설치한다.

The ordinary-user distribution is one file: `ControlStudio-Setup.exe`.
Installation exposes one Start Menu entry and one top-level application,
`ControlStudio.exe`. The Agent and PLC MCP remain separate supervised
processes for isolation, but are installed under the application's private
`runtime/` directory and are not presented as separately installed products.
An independently distributed MCP package is a separate, opt-in product for
users who deliberately want to run the MCP without ControlStudio Platform.

The release-engineering-only portable payload composes three pre-built immutable files and the public
`controlstudio-contracts` wheel: `controlstudio-platform.exe`,
`controlstudio-agent.exe`, `controlstudio-plc-mcp.exe`, and
`controlstudio_contracts-<version>-py3-none-any.whl`. A portable release
contains those files, `controlstudio.bom.json`, and `Start-ControlStudio.ps1`.
It is an internal assembly and black-box validation boundary, not the normal
consumer download. Neither form has a source checkout, Python, Node, or uv
runtime dependency.

## Build boundary

Build the public contracts wheel and Platform executable in a disposable
Windows build environment with pinned build dependencies:

```powershell
python packaging/scripts/build_contracts_wheel.py --output dist/contracts
uv run --directory launcher --with pyinstaller python ../packaging/scripts/build_standalone.py --output dist/platform --clean
```

The Platform executable is built from the launcher project environment. On
Windows that environment pins `pywinauto` for the launcher's owned UI handoff;
the standalone builder refuses to invoke PyInstaller when either `pywinauto`
or `pywinauto.keyboard` is unavailable, and explicitly bundles both runtime
modules. A build environment that cannot resolve those Windows-only modules
must be repaired before producing a release artifact.

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

Create the consumer installer only from that verified portable payload:

```powershell
python packaging/scripts/build_windows_installer.py --portable dist/release/portable --output dist/ControlStudio-Setup.exe
```

The installer re-verifies every payload hash before copying anything. It
renames the Platform artifact to the single public entry point
`ControlStudio.exe`, places Agent, MCP, the contracts wheel, and the BOM under
`runtime/`, and creates only `ControlStudio.lnk`. On every installed launch,
Platform validates its own hash and all internal runtime hashes and contract
versions before binding the verified Agent/MCP commands. Missing or changed
runtime artifacts fail closed.

Component repositories may advance their implementation versions independently,
but consumer releases never resolve a component named `latest`. Every change
creates a new product release whose exact Platform, Agent, MCP, contracts, hashes,
and signature metadata are pinned in the BOM. Updates and rollback operate on
that complete BOM-bound product release, not on one sidecar in isolation.

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
