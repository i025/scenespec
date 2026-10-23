"""Detect accidental changes to the original evaluation contract.

This is a reproducibility check, not a security boundary. The controller and
this manifest must be stored outside any future mutable agent environment.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def check_original_contract(root: Path | None = None) -> dict:
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "original_contract.json").read_text(encoding="utf-8"))
    actual = {}
    mismatches = []
    for name, expected in manifest["sha256"].items():
        path = root / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        actual[name] = digest
        if digest != expected or path.is_symlink():
            mismatches.append(name)
    return {"valid": not mismatches, "mismatches": mismatches,
            "sha256": actual, "origin": manifest["origin"]}


def require_original_contract() -> dict:
    result = check_original_contract()
    if not result["valid"]:
        raise ValueError("original evaluation contract changed: " + ", ".join(result["mismatches"]))
    return result
