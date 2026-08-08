#!/usr/bin/env python3
"""Reusable safety primitives for guarded filesystem changes.

Callers decide *what* a change means.  This module owns the invariant shared by
rename, source append, and highlight filing: record the exact pre-state, refuse
stale plans, expose the intended post-state, classify ambiguous outcomes, and
emit enough metadata to reverse the filesystem part without guessing.
"""

import hashlib
from pathlib import Path


STATUSES = {"planned", "applied", "partial", "indeterminate", "error"}


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


def write_operation(path, after, before=None, reason=None):
    """Describe one create/replace without applying it.

    `before` is a state returned by :func:`file_state`.  If omitted it is read
    now.  The caller may retain its bytes in a transaction packet; public
    metadata deliberately contains only hashes and sizes.
    """
    target = Path(path)
    payload = bytes(after)
    prior = before or file_state(target, include_bytes=True)
    return {
        "target": target,
        "before": prior,
        "after": payload,
        "after_sha256": sha256_bytes(payload),
        "reason": reason,
    }


def public_operation(operation):
    before = operation["before"]
    return {
        "target": str(operation["target"]),
        "action": "replace" if before["exists"] else "create",
        "before_sha256": before.get("sha256"),
        "after_sha256": operation["after_sha256"],
        "rollback": (
            {"action": "restore", "sha256": before.get("sha256")}
            if before["exists"] else {"action": "delete-if-hash-matches",
                                      "sha256": operation["after_sha256"]}
        ),
        **({"reason": operation["reason"]} if operation.get("reason") else {}),
    }


def verify_precondition(operation):
    observed = file_state(operation["target"])
    expected = operation["before"]
    if observed["exists"] != expected["exists"]:
        return False
    return not observed["exists"] or observed["sha256"] == expected["sha256"]


def classify_observed(operation):
    observed = file_state(operation["target"])
    if observed["exists"] and observed["sha256"] == operation["after_sha256"]:
        return "applied", observed
    before = operation["before"]
    if observed["exists"] == before["exists"] and (
            not observed["exists"] or observed["sha256"] == before["sha256"]):
        return "not-applied", observed
    return "indeterminate", observed


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


def apply_writes(operations):
    """Apply a bounded set of writes after checking every precondition.

    A failure after at least one write is `partial`.  An I/O error whose
    observed state is neither the declared before nor after state is
    `indeterminate` and must not be retried blindly.
    """
    operations = list(operations)
    stale = [str(op["target"]) for op in operations if not verify_precondition(op)]
    if stale:
        raise RuntimeError("transaction preconditions changed: " + ", ".join(stale))

    applied = []
    for operation in operations:
        target = operation["target"]
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if operation["before"]["exists"]:
                target.write_bytes(operation["after"])
            else:
                with target.open("xb") as handle:
                    handle.write(operation["after"])
            state, observed = classify_observed(operation)
            if state != "applied":
                status = ("indeterminate" if state == "indeterminate"
                          else ("partial" if applied else "error"))
                return {
                    "status": status,
                    "do_not_retry": state == "indeterminate",
                    "applied": [public_operation(op) for op in applied],
                    "failed": public_operation(operation),
                    "observed": observed,
                }
            applied.append(operation)
        except OSError as exc:
            state, observed = classify_observed(operation)
            if state == "applied":
                applied.append(operation)
            status = ("indeterminate" if state == "indeterminate"
                      else ("partial" if applied else "error"))
            return {
                "status": status,
                "do_not_retry": state == "indeterminate",
                "applied": [public_operation(op) for op in applied],
                "failed": (None if state == "applied"
                           else public_operation(operation)),
                "observed": observed,
                "error": str(exc),
            }
    return {"status": "applied",
            "operations": [public_operation(op) for op in operations]}
