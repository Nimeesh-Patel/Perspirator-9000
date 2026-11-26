#!/usr/bin/env python3
"""Ownership and retirement rules for generated Perspirator installations."""

import hashlib
import json
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile


MANIFEST_NAME = ".perspirator-install.json"


def replace_text(path, text):
    """Replace one generated file without exposing a partial write."""
    path = Path(path)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_manifest(directory):
    path = Path(directory) / MANIFEST_NAME
    if path.is_symlink():
        raise ValueError(f"install ownership manifest is a symlink: {path}")
    if not path.exists():
        return {"version": 1, "files": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read install ownership manifest: {exc}") from exc
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict) or any(
            not name or "\\" in name or ":" in name
            or PurePosixPath(name).is_absolute()
            or any(part in ("", ".", "..") for part in name.split("/"))
            or not isinstance(value, str)
            for name, value in files.items()):
        raise ValueError("install ownership manifest needs safe relative paths and hashes")
    return payload


def stale_owned_files(directory, desired):
    """Classify previously generated files no longer in the install surface."""
    directory = Path(directory).resolve()
    desired = set(desired)
    stale = []
    for name, expected in load_manifest(directory)["files"].items():
        if name in desired:
            continue
        path = directory / name
        if path.is_symlink() or path.resolve() != path:
            stale.append({"name": name, "state": "redirected"})
        elif not path.exists():
            continue
        elif not path.is_file():
            stale.append({"name": name, "state": "not-a-file"})
        else:
            actual = digest(path)
            stale.append({"name": name,
                          "state": "unchanged" if actual == expected else "modified",
                          "expected_sha256": expected,
                          "observed_sha256": actual})
    return stale


def retirement_plan(directory, desired):
    stale = stale_owned_files(directory, desired)
    unsafe = [item for item in stale if item["state"] != "unchanged"]
    if unsafe:
        names = ", ".join(f"{item['name']} ({item['state']})" for item in unsafe)
        raise RuntimeError(
            "retired managed files need criticism before deletion: " + names)
    return stale


def retire_stale_owned_files(directory, desired, *, planned=None):
    """Delete only unchanged retired outputs inside the adapter directory."""
    stale = retirement_plan(directory, desired)
    if planned is not None and stale != planned:
        raise RuntimeError("retired managed files changed after installation preflight")
    directory = Path(directory).resolve()
    for item in stale:
        path = directory / item["name"]
        if path.is_symlink() or path.resolve() != path or digest(path) != item["expected_sha256"]:
            raise RuntimeError(f"retired managed file changed before deletion: {path}")
        path.unlink()
    return [item["name"] for item in stale]


def write_manifest(directory, names):
    directory = Path(directory)
    payload = {
        "version": 1,
        "purpose": "ownership for safe retirement of generated Perspirator files",
        "files": {name: digest(directory / name) for name in sorted(names)},
    }
    path = directory / MANIFEST_NAME
    replace_text(path, json.dumps(payload, indent=2) + "\n")
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
