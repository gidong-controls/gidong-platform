# ControlStudio public mocks

`controlstudio_mocks.agent` is an NDJSON Agent Protocol v1 process. It has no
target identity, approval secret, MCP connection, model credential, or private
dependency. It only emits a deterministic read-only `workspace.inspect` tool
proposal and accepts a Platform-returned tool result.

`controlstudio_mocks.mcp` is a JSON-RPC/MCP stdio process. Its
`controlstudio.contract_manifest` response contains a canonical SHA-256
manifest digest and the two tool schema digests. `workspace.inspect` is safe;
`project.apply_mock` fails closed without an approval and never changes data.

Both programs write protocol data to stdout only. Diagnostics are deliberately
omitted so they cannot corrupt the NDJSON/JSON-RPC stream.
