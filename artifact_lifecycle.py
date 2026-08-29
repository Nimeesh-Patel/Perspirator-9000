#!/usr/bin/env python3
"""Validate editable lifecycle declarations for generated/temporary artifacts.

Semantic lifecycle classes remain declarations for criticism. Independent
path/type/pattern fields express only mechanically decidable constraints.
"""

import json
from pathlib import Path

from note_chunks import frontmatter_fields


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
    except (OSError, json.JSONDecodeError) as exc:
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
        relative = Path(str(entry.get("path", "")))
        if not str(relative) or relative.is_absolute() or ".." in relative.parts:
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
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            state = str(frontmatter_fields(text).get(field, "")).casefold()
            if state in forbidden:
                problems.append(
                    f"{entry['name']}: {path.name} is terminal ({field}: {state})")
    return problems
