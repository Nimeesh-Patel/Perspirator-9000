#!/usr/bin/env python3
"""Validate editable lifecycle declarations for generated/temporary artifacts."""

import json
from pathlib import Path

from note_chunks import frontmatter_fields


DECLARATION = Path("memory/perspirator/artifact-lifecycle.json")


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
        if not isinstance(validation, str) or not validation.strip():
            raise ValueError(f"{name}: validation must be non-empty text")
        if not isinstance(entry.get("retire_when"), str) or not entry["retire_when"].strip():
            raise ValueError(f"{name}: missing explanatory retire_when")
        relative = Path(str(entry.get("path", "")))
        if not str(relative) or relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"{name}: path must be vault-relative")
    return entries


def lifecycle_problems(vault, entries):
    """Return mechanically decidable lifecycle violations.

    `unique-evidence` and `rollback-value` remain semantic review conditions;
    the declaration exposes them but does not pretend a filename can decide
    whether knowledge is still unique.
    """
    vault = Path(vault)
    problems = []
    for entry in entries:
        if entry["validation"] != "frontmatter-state":
            continue
        directory = vault / entry["path"]
        if not directory.exists() and entry.get("optional", False):
            continue
        if not directory.is_dir():
            problems.append(f"{entry['name']}: directory missing: {entry['path']}")
            continue
        pattern = entry.get("pattern", "*.md")
        exempt = {str(value).casefold() for value in entry.get("exempt", [])}
        forbidden = {str(value).casefold()
                     for value in entry.get("forbidden_states", [])}
        field = entry.get("state_field", "status")
        for path in sorted(directory.glob(pattern)):
            if path.name.casefold() in exempt:
                continue
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            state = str(frontmatter_fields(text).get(field, "")).casefold()
            if state in forbidden:
                problems.append(
                    f"{entry['name']}: {path.name} is terminal ({field}: {state})")
    return problems
