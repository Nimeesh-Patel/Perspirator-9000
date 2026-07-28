#!/usr/bin/env python3
"""Expose the active policy selection surface without deciding relevance.

Policies own semantic instructions in Markdown. This tool owns only the
repeatable structural question: which active policy files are well formed, and
what problem does each say it solves?
"""

import argparse
import json
import sys
from pathlib import Path

from note_chunks import frontmatter_fields, read_note, section

POLICY_DIR = ("memory", "policies")


def active_policy_surface(vault):
    """Return active policy problems or raise on an ambiguous policy surface."""
    vault = Path(vault).expanduser().resolve(strict=False)
    directory = vault.joinpath(*POLICY_DIR)
    if not directory.is_dir():
        raise ValueError(f"policy directory does not exist: {directory}")

    records = []
    errors = []
    titles = {}
    for path in sorted(directory.glob("*.md"), key=lambda item: item.name.casefold()):
        text = read_note(path)
        if text is None:
            errors.append(f"unreadable: {path.name}")
            continue
        fields = frontmatter_fields(text)
        if fields.get("status") != "active":
            continue
        kind = fields.get("type")
        if kind == "configuration":
            continue
        if kind != "policy":
            errors.append(f"{path.name}: active policy file needs type: policy")
            continue
        title = fields.get("title", "").strip()
        problem = section(text, "## Problem").strip()
        conjecture = section(text, "## Conjecture").strip()
        missing = [name for name, value in (
            ("title", title), ("Problem", problem), ("Conjecture", conjecture)
        ) if not value]
        if missing:
            errors.append(f"{path.name}: missing {', '.join(missing)}")
            continue
        key = title.casefold()
        if key in titles:
            errors.append(f"duplicate policy title: {title!r} in "
                          f"{titles[key]} and {path.name}")
            continue
        titles[key] = path.name
        records.append({
            "title": title,
            "path": path.relative_to(vault).as_posix(),
            "problem": problem,
        })

    if errors:
        raise ValueError("; ".join(errors))
    if not records:
        raise ValueError(f"no active policies found in {directory}")
    return records


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", default=str(Path.home() / "nimeesh vault"))
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main():
    args = arguments()
    try:
        records = active_policy_surface(args.vault)
    except ValueError as exc:
        raise SystemExit(f"STOP: {exc}") from None
    if args.json:
        print(json.dumps(records, indent=2, ensure_ascii=False))
        return 0
    for record in records:
        print(f"# {record['title']}\n{record['path']}\n{record['problem']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
