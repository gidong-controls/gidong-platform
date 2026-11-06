# ControlStudio Contracts

Public, versioned Python DTOs used at the Platform, Agent, and PLC MCP process
boundaries. The package contains schemas, validation and shared evidence
parsing/comparison helpers. Agent prompts, retrieval policy, MCP transport and
vendor execution remain outside this package.

The Platform-owned source of truth is `schemas/`.  Before changing a public
HTTP model, run from the Platform checkout:

```powershell
$env:PYTHONPATH='packages/python/controlstudio-contracts/src;host/src'
host/.venv/Scripts/python.exe scripts/contracts/contract_guard.py --write --write-digests
```

The normal check mode is fail-closed for OpenAPI snapshot drift, generated
TypeScript operation metadata, v1 schema digests, and web type re-export
drift.  A removed route or new required API field also requires an incremented
`schemas/http/v1/contract-version.json` major before snapshot regeneration.
