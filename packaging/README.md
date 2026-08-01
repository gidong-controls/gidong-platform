# ControlStudio portable packaging

This directory composes three immutable, already-built Windows artifacts.  It
does not install from source paths at runtime and it intentionally does not
claim a production signature.  See `../docs/refactor/PACKAGING.md` for the
release procedure and its security limits.

`python packaging/scripts/build_bom.py` creates a canonical BOM; `verify_bom.py`
verifies it; and `assemble_portable.py` creates the portable distribution.
