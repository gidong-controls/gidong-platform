# ControlStudio scripts

Run the manifest-driven PLC engineering Agent acceptance harness with:

```powershell
..\host\.venv\Scripts\python.exe .\plc_agent_acceptance.py --deterministic-only
```

Its deterministic commands, fail-closed live gates, receipt contract, and the
fact that a deterministic-only exit code is not live completion are documented
in [../docs/PLC_AGENT_ACCEPTANCE.md](../docs/PLC_AGENT_ACCEPTANCE.md).
