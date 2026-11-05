# ControlStudio product packaging

The normal user downloads `ControlStudio-Setup.exe`, installs one application,
and launches `ControlStudio.exe`. Agent, PLC MCP, and the XG5000 CLI are private
runtime processes. The four-artifact
portable directory exists only as the immutable release assembly and
black-box validation input. This packaging does not install from source paths
at runtime and intentionally does not claim a production signature. See
`../docs/refactor/PACKAGING.md` for the release procedure and security limits.

`python packaging/scripts/build_bom.py` creates a canonical BOM; `verify_bom.py`
verifies it; and `assemble_portable.py` creates the portable distribution.
`build_windows_installer.py` turns that verified payload into the single
consumer setup executable.
