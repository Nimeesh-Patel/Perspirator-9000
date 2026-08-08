#!/usr/bin/env python3
"""Ownership and retirement rules for generated Perspirator installations."""

import hashlib
import json
from pathlib import Path


MANIFEST_NAME = ".perspirator-install.json"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_manifest(directory):
    path = Path(directory) / MANIFEST_NAME
    if not path.exists():
        return {"version": 1, "files": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read install ownership manifest: {exc}") from exc
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict) or any(
            not isinstance(name, str) or not isinstance(value, str)
            for name, value in files.items()):
        raise ValueError("install ownership manifest needs a files hash map")
    return payload


def stale_owned_files(directory, desired):
    """Classify previously generated files no longer in the install surface."""
    directory = Path(directory)
    desired = set(desired)
    stale = []
    for name, expected in load_manifest(directory)["files"].items():
        if name in desired:
            continue
        path = directory / name
        if not path.exists():
            continue
        if not path.is_file():
            stale.append({"name": name, "state": "not-a-file"})
        else:
            actual = digest(path)
            stale.append({"name": name,
                          "state": "unchanged" if actual == expected else "modified",
                          "expected_sha256": expected,
                          "observed_sha256": actual})
    return stale


def retire_stale_owned_files(directory, desired):
    """Delete only unchanged retired outputs; refuse locally modified ones."""
    stale = stale_owned_files(directory, desired)
    unsafe = [item for item in stale if item["state"] != "unchanged"]
    if unsafe:
        names = ", ".join(f"{item['name']} ({item['state']})" for item in unsafe)
        raise RuntimeError(
            "retired managed files need criticism before deletion: " + names)
    directory = Path(directory)
    for item in stale:
        (directory / item["name"]).unlink()
    return [item["name"] for item in stale]


def write_manifest(directory, names):
    directory = Path(directory)
    payload = {
        "version": 1,
        "purpose": "ownership for safe retirement of generated Perspirator files",
        "files": {name: digest(directory / name) for name in sorted(names)},
    }
    path = directory / MANIFEST_NAME
    path.write_text(json.dumps(payload, indent=2) + "\n",
                    encoding="utf-8", newline="\n")
    return path


def manifest_problems(directory, desired):
    directory = Path(directory)
    path = directory / MANIFEST_NAME
    if not path.is_file():
        return [f"ownership manifest missing: {path}"]
    try:
        manifest = load_manifest(directory)
        stale = stale_owned_files(directory, desired)
    except ValueError as exc:
        return [str(exc)]
    desired = set(desired)
    recorded = set(manifest["files"])
    problems = [f"retired managed file remains: {item['name']} ({item['state']})"
                for item in stale]
    missing = desired - recorded
    if missing:
        problems.append("current generated files absent from manifest: "
                        + ", ".join(sorted(missing)))
    return problems
