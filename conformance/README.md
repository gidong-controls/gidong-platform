# ControlStudio contract conformance

The runner speaks only process stdin/stdout. It validates Agent Protocol v1
initialize/health/run proposal/result framing and MCP Product Contract v1
manifest/schema digest plus unapproved mutation rejection.

Run it against an installed component with JSON argv arrays:

```text
controlstudio-conformance --agent-command controlstudio-mock-agent --mcp-command controlstudio-mock-mcp
```

The test suite uses the public mocks but production Agent and MCP sidecars are
expected to pass the same black-box checks.
