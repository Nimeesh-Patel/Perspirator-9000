#!/usr/bin/env python3
"""Validate editable lifecycle declarations for generated/temporary artifacts.

Semantic lifecycle classes remain declarations for criticism. Independent
path/type/pattern fields express only mechanically decidable constraints.
"""

import json
import argparse
import sys
from pathlib import Path, PureWindowsPath

from adapters import add_vault_argument
from change_transaction import file_state
from directory_audit import _is_reparse, scan_tree
from note_chunks import frontmatter_fields, read_note


DECLARATION = Path("memory/perspirator/artifact-lifecycle.json")
VALIDATION_CLASSES = {
    "current-explanatory-role",
    "frontmatter-state",
    "rebuildable",
    "rollback-value",
    "unique-evidence",
}
PATH_TYPES = {"directory", "file"}


def load_lifecycle(vault):
    path = Path(vault) / DECLARATION
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read artifact lifecycle declaration: {exc}") from exc
    entries = payload.get("artifacts") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError("artifact lifecycle needs a non-empty artifacts list")
    names = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"artifact lifecycle entry {index} is not an object")
        name, role = entry.get("name"), entry.get("role")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError(f"artifact lifecycle entry {index} has invalid name")
        names.add(name)
        if not isinstance(role, str) or not role.strip():
            raise ValueError(f"{name}: role must be non-empty text")
        validation = entry.get("validation")
        if validation not in VALIDATION_CLASSES:
            raise ValueError(
                f"{name}: unknown validation class: {validation!r}")
        if not isinstance(entry.get("retire_when"), str) or not entry["retire_when"].strip():
            raise ValueError(f"{name}: missing explanatory retire_when")
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(f"{name}: path must be vault-relative")
        relative = Path(raw_path)
        if (relative.anchor or PureWindowsPath(raw_path).anchor
                or relative == Path(".") or ".." in relative.parts
                or ".." in PureWindowsPath(raw_path).parts):
            raise ValueError(f"{name}: path must be vault-relative")
        path_type = entry.get("path_type")
        if path_type is not None and path_type not in PATH_TYPES:
            raise ValueError(f"{name}: path_type must be file or directory")
        if "optional" in entry and not isinstance(entry["optional"], bool):
            raise ValueError(f"{name}: optional must be true or false")

        forbidden_patterns = entry.get("forbidden_patterns", [])
        if (not isinstance(forbidden_patterns, list)
                or any(not isinstance(value, str) or not value.strip()
                       for value in forbidden_patterns)):
            raise ValueError(f"{name}: forbidden_patterns must be a list of globs")
        for pattern in forbidden_patterns:
            pattern_path = Path(pattern)
            if pattern_path.is_absolute() or ".." in pattern_path.parts:
                raise ValueError(
                    f"{name}: forbidden pattern must stay under the artifact path")
        if forbidden_patterns and path_type != "directory":
            raise ValueError(
                f"{name}: forbidden_patterns requires path_type: directory")
        if validation == "frontmatter-state" and path_type not in (None, "directory"):
            raise ValueError(
                f"{name}: frontmatter-state requires a directory artifact")
    return entries


def lifecycle_problems(vault, entries):
    """Return mechanically decidable lifecycle violations.

    Semantic validation classes remain review conditions; this function does
    not pretend a filename can decide whether evidence is still unique or a
    rollback still has value. Path presence/type, forbidden surface patterns,
    and explicit frontmatter states are independently mechanical.
    """
    vault = Path(vault)
    problems = []
    for entry in entries:
        target = vault / entry["path"]
        if not target.exists() and entry.get("optional", False):
            continue
        if not target.exists():
            problems.append(f"{entry['name']}: artifact missing: {entry['path']}")
            continue

        path_type = entry.get("path_type")
        if path_type == "directory" and not target.is_dir():
            problems.append(f"{entry['name']}: expected directory: {entry['path']}")
            continue
        if path_type == "file" and not target.is_file():
            problems.append(f"{entry['name']}: expected file: {entry['path']}")
            continue

        for pattern in entry.get("forbidden_patterns", []):
            for path in sorted(target.glob(pattern)):
                problems.append(
                    f"{entry['name']}: forbidden artifact matches {pattern}: "
                    f"{path.relative_to(target).as_posix()}")

        if entry["validation"] != "frontmatter-state":
            continue
        if not target.is_dir():
            problems.append(
                f"{entry['name']}: frontmatter-state artifact is not a directory: "
                f"{entry['path']}")
            continue
        pattern = entry.get("pattern", "*.md")
        exempt = {str(value).casefold() for value in entry.get("exempt", [])}
        forbidden = {str(value).casefold()
                     for value in entry.get("forbidden_states", [])}
        field = entry.get("state_field", "status")
        for path in sorted(target.glob(pattern)):
            if path.name.casefold() in exempt:
                continue
            try:
                text = read_note(path)
            except ValueError as exc:
                problems.append(f"{entry['name']}: {path.name} is unreadable: {exc}")
                continue
            if text is None:
                problems.append(f"{entry['name']}: {path.name} is unreadable")
                continue
            state = str(frontmatter_fields(text).get(field, "")).casefold()
            if state in forbidden:
                problems.append(
                    f"{entry['name']}: {path.name} is terminal ({field}: {state})")
    return problems


def inventory(vault, entries, names):
    """Observe explicitly selected artifact trees using the shared safe scanner.

    Include every regular file except declared exemptions, not just files whose
    status happens to trigger a validation rule. Metadata is untrusted content;
    neither a terminal state nor a missing retention field authorises removal.
    """
    vault = Path(vault).expanduser().resolve(strict=True)
    requested = set(names)
    known = {entry["name"] for entry in entries}
    if not requested or requested - known:
        raise ValueError("select declared artifacts by name; unknown: "
                         + ", ".join(sorted(requested - known)))
    results = []
    for entry in entries:
        if entry["name"] not in requested:
            continue
        result = {"name": entry["name"], "path": entry["path"],
                  "role": entry["role"], "validation": entry["validation"],
                  "retire_when": entry["retire_when"], "files": [],
                  "boundaries": [], "status": "complete"}
        results.append(result)
        target = vault / entry["path"]
        try:
            # scan_tree refuses child reparses. Check the declared root and
            # its ancestors too, before that scanner resolves its root.
            current = vault
            for part in Path(entry["path"]).parts:
                current /= part
                if _is_reparse(current):
                    raise ValueError(f"reparse boundary: {current.relative_to(vault)}")
            if target.is_dir():
                scanned, boundaries, _ = scan_tree(target)
                paths = [record.path for record in scanned]
                result["boundaries"] = boundaries
            elif target.is_file():
                paths = [target]
            else:
                raise ValueError("artifact is not a regular file or directory")
        except FileNotFoundError:
            result["status"] = "missing-optional" if entry.get("optional") else "unavailable"
            continue
        except (OSError, ValueError) as exc:
            result.update(status="unavailable", error=str(exc))
            continue
        exempt = {str(value).casefold() for value in entry.get("exempt", [])}
        for path in paths:
            if path.name.casefold() in exempt:
                continue
            observed = {"path": path.relative_to(vault).as_posix()}
            result["files"].append(observed)
            try:
                if _is_reparse(path):
                    raise ValueError("file became a reparse boundary")
                before = path.stat()
                state = file_state(path, include_bytes=True)
                if not state["exists"]:
                    raise ValueError("file disappeared during observation")
                observed.update(sha256=state["sha256"], size=state["size"], status="complete")
                if path.suffix.casefold() == ".md":
                    observed["frontmatter"] = frontmatter_fields(
                        state["bytes"].decode("utf-8-sig"))
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    observed.update(status="indeterminate", error="file changed during observation")
                    result["status"] = "partial"
            except (OSError, UnicodeError, ValueError) as exc:
                observed.update(status="unavailable", error=str(exc))
                result["status"] = "partial"
        if result["boundaries"]:
            result["status"] = "partial"
    return {"status": "partial" if any(r["status"] in ("partial", "unavailable")
                                         for r in results) else "complete",
            "scope": "selected declared trees; regular files; no reparse traversal",
            "artifacts": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_vault_argument(parser)
    parser.add_argument("--artifact", action="append", required=True, metavar="NAME",
                        help="inventory this declared artifact; repeat for another tree")
    args = parser.parse_args(argv)
    try:
        payload = inventory(args.vault, load_lifecycle(args.vault), args.artifact)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "complete" else 2


if __name__ == "__main__":
    sys.exit(main())
