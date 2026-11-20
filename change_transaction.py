#!/usr/bin/env python3
"""Reusable safety primitives for guarded filesystem changes.

Callers decide *what* a change means.  This module owns the invariant a guarded
rename needs: record the exact pre-state, refuse a stale plan, classify an
ambiguous outcome, and emit enough metadata to reverse the move without
guessing.  ``file_state`` also serves read-only lifecycle observation.
"""

import hashlib
from pathlib import Path


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def file_state(path, include_bytes=False):
    path = Path(path)
    if not path.exists():
        return {"exists": False, "sha256": None, "bytes": None}
    if not path.is_file():
        raise ValueError(f"transaction target is not a file: {path}")
    payload = path.read_bytes()
    state = {"exists": True, "sha256": sha256_bytes(payload),
             "size": len(payload)}
    if include_bytes:
        state["bytes"] = payload
    return state


def identity_operation(old_path, new_path, *, reason=None):
    """Describe a move/rename whose mutation is performed by an adapter."""
    old_path, new_path = Path(old_path), Path(new_path)
    return {
        "old": old_path,
        "new": new_path,
        "before": file_state(old_path),
        "destination_before": file_state(new_path),
        "reason": reason,
    }


def verify_identity_precondition(operation):
    before = operation["before"]
    observed = file_state(operation["old"])
    destination = file_state(operation["new"])
    return (before["exists"] and observed["exists"]
            and observed["sha256"] == before["sha256"]
            and not operation["destination_before"]["exists"]
            and not destination["exists"])


def observe_identity(operation):
    old = file_state(operation["old"])
    new = file_state(operation["new"])
    before_hash = operation["before"].get("sha256")
    if not old["exists"] and new["exists"] and new["sha256"] == before_hash:
        status = "applied"
    elif old["exists"] and old["sha256"] == before_hash and not new["exists"]:
        status = "not-applied"
    else:
        status = "indeterminate"
    return status, {"old": old, "new": new}


def public_identity(operation):
    return {
        "action": "move",
        "old": str(operation["old"]),
        "new": str(operation["new"]),
        "before_sha256": operation["before"].get("sha256"),
        "rollback": {"action": "move", "old": str(operation["new"]),
                     "new": str(operation["old"]),
                     "precondition_sha256": operation["before"].get("sha256")},
        **({"reason": operation["reason"]} if operation.get("reason") else {}),
    }
