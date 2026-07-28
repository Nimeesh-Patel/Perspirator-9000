#!/usr/bin/env python3
"""Stage source-grounded Problem Notes from an agent-authored grouping plan.

Source adapters recover facts; an agent supplies explanatory judgments in a
plan; this tool checks the mechanical boundary between them. It guarantees
coverage, prevents a source from being assigned twice, validates parent links,
renders ordinary `***` Problem Notes, and refuses to overwrite existing files.

The source bundle may be a JSON list or an object whose `sources` or `records`
field is a list. Every source needs `id`, `text`, and `url`.

Plan shape:
    {
      "notes": [{
        "title": "How can ...?",
        "problem": "How can ...?",
        "up": ["existing parent"],
        "category": "Default",
        "source_ids": ["source-1", "source-2"]
      }]
    }

Examples:
    python source_to_notes.py sources.json plan.json --stage scratch/notes
    python source_to_notes.py sources.json plan.json --vault "/vault" --write
"""

import argparse
import json
import re
import sys
from pathlib import Path


INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
EXCLUDED_PARTS = {".obsidian", ".trash", ".perspirator", "memory"}


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON {path}: {exc}") from exc


def source_records(payload):
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = payload.get("sources", payload.get("records"))
    else:
        records = None
    if not isinstance(records, list):
        raise ValueError("source bundle must be a list or contain sources/records")

    by_id = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"source {index} is not an object")
        source_id = str(record.get("id", "")).strip()
        text = record.get("text")
        url = record.get("url")
        if not source_id or not isinstance(text, str) or not text.strip():
            raise ValueError(f"source {index} needs non-empty id and text")
        if not isinstance(url, str) or not re.match(r"https?://", url):
            raise ValueError(f"source {source_id} needs an http(s) url")
        if source_id in by_id:
            raise ValueError(f"duplicate source id in bundle: {source_id}")
        by_id[source_id] = {"id": source_id, "text": text.strip(), "url": url}
    return by_id


def filename_for(note):
    raw = note.get("file") or note.get("title")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("every planned note needs a title or file")
    filename = raw.strip()
    if not filename.lower().endswith(".md"):
        filename += ".md"
    if filename in {".md", "..md"} or INVALID_FILENAME.search(filename):
        raise ValueError(f"unsafe note filename: {filename}")
    return filename


def existing_stems(vault):
    stems = set()
    for path in vault.rglob("*.md"):
        try:
            relative = path.relative_to(vault)
        except ValueError:
            continue
        if any(part.lower() in EXCLUDED_PARTS for part in relative.parts[:-1]):
            continue
        stems.add(path.stem.casefold())
    return stems


def validate_plan(payload, sources, vault=None, allow_unassigned=False):
    notes = payload.get("notes") if isinstance(payload, dict) else None
    if not isinstance(notes, list) or not notes:
        raise ValueError("plan must contain a non-empty notes list")

    filenames = set()
    assigned = {}
    normalized = []
    known_parents = existing_stems(vault) if vault else None

    for index, note in enumerate(notes):
        if not isinstance(note, dict):
            raise ValueError(f"planned note {index} is not an object")
        filename = filename_for(note)
        filename_key = filename.casefold()
        if filename_key in filenames:
            raise ValueError(f"duplicate planned filename: {filename}")
        filenames.add(filename_key)

        problem = note.get("problem")
        if not isinstance(problem, str) or not problem.strip():
            raise ValueError(f"{filename}: problem is empty")
        if re.search(r"^\*\*\*\s*$", problem, re.MULTILINE):
            raise ValueError(f"{filename}: problem contains the note separator")

        category = note.get("category", "Default")
        if not isinstance(category, str) or not category.strip():
            raise ValueError(f"{filename}: category must be non-empty text")

        parents = note.get("up", [])
        if parents is None:
            parents = []
        if not isinstance(parents, list) or not all(
                isinstance(parent, str) and parent.strip() for parent in parents):
            raise ValueError(f"{filename}: up must be a list of note names")
        parents = [parent.strip() for parent in parents]
        if known_parents is not None:
            missing = [parent for parent in parents
                       if parent.casefold() not in known_parents]
            if missing:
                raise ValueError(
                    f"{filename}: unresolved up link(s): {', '.join(missing)}")

        source_ids = note.get("source_ids")
        if not isinstance(source_ids, list) or not source_ids:
            raise ValueError(f"{filename}: source_ids must be a non-empty list")
        source_ids = [str(source_id).strip() for source_id in source_ids]
        for source_id in source_ids:
            if source_id not in sources:
                raise ValueError(f"{filename}: unknown source id {source_id}")
            if source_id in assigned:
                raise ValueError(
                    f"source {source_id} assigned to both "
                    f"{assigned[source_id]} and {filename}")
            assigned[source_id] = filename

        normalized.append({
            "filename": filename,
            "problem": problem.strip(),
            "category": category.strip(),
            "up": parents,
            "source_ids": source_ids,
        })

    unassigned = [source_id for source_id in sources if source_id not in assigned]
    if unassigned and not allow_unassigned:
        raise ValueError("unassigned source ids: " + ", ".join(unassigned))
    return normalized, unassigned


def render_frontmatter(note):
    if note["up"]:
        up = "up:\n" + "\n".join(
            f"- {json.dumps(f'[[{parent}]]', ensure_ascii=False)}"
            for parent in note["up"])
    else:
        up = "up: null"
    category = json.dumps(note["category"], ensure_ascii=False)
    return f"---\n{up}\ncategory: {category}\n---"


def render_note(note, sources):
    blocks = []
    for source_id in note["source_ids"]:
        source = sources[source_id]
        blocks.append(f"{source['text']}\n\n{source['url']}")
    idea = "\n\n---\n\n".join(blocks)
    return (
        f"{render_frontmatter(note)}\n\n"
        f"{note['problem']}\n\n***\n\n{idea}\n"
    )


def write_notes(notes, sources, destination):
    destination.mkdir(parents=True, exist_ok=True)
    targets = [destination / note["filename"] for note in notes]
    collisions = [str(path) for path in targets if path.exists()]
    if collisions:
        raise ValueError("refusing to overwrite: " + ", ".join(collisions))
    for note, target in zip(notes, targets):
        target.write_text(
            render_note(note, sources), encoding="utf-8", newline="\n")
    return targets


def arguments():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sources", help="JSON source bundle")
    parser.add_argument("plan", help="agent-authored JSON grouping plan")
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--stage", help="stage rendered notes here")
    destination.add_argument("--write", action="store_true",
                             help="write to the root of --vault")
    parser.add_argument("--vault",
                        help="vault used to validate up links and write targets")
    parser.add_argument("--allow-unassigned", action="store_true",
                        help="permit bundle sources omitted from the plan")
    return parser.parse_args()


def main():
    args = arguments()
    if args.write and not args.vault:
        raise SystemExit("error: --write requires --vault")
    try:
        sources = source_records(read_json(args.sources))
        plan = read_json(args.plan)
        vault = Path(args.vault).expanduser().resolve(strict=False) if args.vault else None
        if vault is not None and not vault.is_dir():
            raise ValueError(f"vault does not exist: {vault}")
        notes, unassigned = validate_plan(
            plan, sources, vault=vault,
            allow_unassigned=args.allow_unassigned)
        destination = vault if args.write else Path(args.stage).expanduser().resolve(
            strict=False)
        targets = write_notes(notes, sources, destination)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from None

    print(json.dumps({
        "sources": len(sources),
        "assigned_sources": len(sources) - len(unassigned),
        "unassigned_sources": unassigned,
        "notes": len(notes),
        "destination": str(destination),
        "targets": [str(path) for path in targets],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
