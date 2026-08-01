"""Fail-closed BOM primitives used by the portable Windows packager."""

from .bom import PackagingError, assemble, build_bom, verify_bom

__all__ = ["PackagingError", "assemble", "build_bom", "verify_bom"]
