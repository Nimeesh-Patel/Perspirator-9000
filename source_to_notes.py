#!/usr/bin/env python3
"""Stage source-grounded Problem Notes from an agent-authored grouping plan.

Source adapters recover facts; an agent supplies explanatory judgments in a
plan; this tool checks the mechanical boundary between them. It guarantees
coverage, prevents a source from being assigned twice, validates parent links,
renders ordinary `***` Problem Notes, and permits an explicit, guarded append
when an agent has judged that a source addresses an existing note's same
problem. Existing bytes are preserved; only exact source text and locator are
appended.

The source bundle may be a JSON list or an object whose `sources` or `records`
field is a list. Every source needs stable `id`, exact `text`, and a `locator` URI saying
where it lives — `https://...` for a web source, `readera://...` for a book
highlight. Records also state provenance, completeness, and withdrawal state.

Plan shape:
    {
      "notes": [{
        "title": "How can ...?",
        "problem": "How can ...?",
        "up": ["existing parent"],
        "category": "Default",
        "source_ids": ["source-1", "source-2"]
      }, {
        "file": "an existing problem.md",
        "existing": true,
        "problem": "The exact current problem side.",
        "same_problem": "Why these sources address this same problem.",
        "source_ids": ["source-3"]
      }]
    }

An existing-note plan is a semantic assertion made by the agent, not a score
threshold. The exact problem field is also a stale-write guard: staging or
writing stops if the target's current problem side differs.

Examples:
    python source_to_notes.py sources.json plan.json --stage scratch/notes
    python source_to_notes.py sources.json plan.json --vault "/vault" --write
    python source_to_notes.py sources.json plan.json --vault "/vault" --write --append-existing
"""

import argparse
import json
import re
import sys
from pathlib import Path

from note_chunks import MEMORY_FOLDER, NON_NOTE_FOLDERS
from problem_half import parse_note
from change_transaction import (apply_writes, file_state, sha256_bytes,
                                write_operation)
from contracts import validate_provider_result, validate_source_record


INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Excludes `memory` because this writes root vault Problem Notes and `memory/`
# belongs to Basic Memory. `Attachments` was missing here while every other
# tool excluded it — an unexplained difference, so a defect rather than a
# scoping choice.
EXCLUDED_PARTS = {*NON_NOTE_FOLDERS, MEMORY_FOLDER}
SEPARATOR = re.compile(r"^\*\*\*\s*$", re.MULTILINE)
def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON {path}: {exc}") from exc


def source_records(payload):
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        if "provider" in payload or "status" in payload:
            validate_provider_result(payload)
        records = payload.get("sources", payload.get("records"))
    else:
        records = None
    if not isinstance(records, list):
        raise ValueError("source bundle must be a list or contain sources/records")

    by_id = {}
    seen_locators = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"source {index} is not an object")
        try:
            record = validate_source_record(record)
        except ValueError as exc:
            raise ValueError(f"source {index}: {exc}") from exc
        source_id = record["id"].strip()
        locator = record["locator"].strip()
        if source_id in by_id:
            raise ValueError(f"duplicate source id in bundle: {source_id}")
        if locator in seen_locators:
            raise ValueError(
                f"sources {seen_locators[locator]} and {source_id} share "
                f"locator {locator}")
        seen_locators[locator] = source_id
        by_id[source_id] = dict(record, id=source_id,
                               text=record["text"].strip(), locator=locator)
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


def read_existing(path):
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read existing note {path}: {exc}") from exc
    separators = len(SEPARATOR.findall(text.replace("\r\n", "\n").replace("\r", "\n")))
    parsed = parse_note(text)
    problem = parsed["problem"]
    if not parsed["has_separator"] or separators != 1:
        raise ValueError(
            f"existing target is not one ordinary Problem Note: {path.name}")
    return raw, text, (problem or "").strip()


def existing_source_locations(vault, sources):
    """Source id -> root note names already carrying its exact locator."""
    locations = {source_id: [] for source_id in sources}
    for path in sorted(vault.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for source_id, source in sources.items():
            if source["locator"] in text:
                locations[source_id].append(path.name)
    return locations


def validate_plan(payload, sources, vault=None, allow_unassigned=False):
    notes = payload.get("notes") if isinstance(payload, dict) else None
    if not isinstance(notes, list) or not notes:
        raise ValueError("plan must contain a non-empty notes list")

    filenames = set()
    assigned = {}
    normalized = []
    known_parents = existing_stems(vault) if vault else None
    source_locations = existing_source_locations(vault, sources) if vault else {}

    for index, note in enumerate(notes):
        if not isinstance(note, dict):
            raise ValueError(f"planned note {index} is not an object")
        filename = filename_for(note)
        filename_key = filename.casefold()
        if filename_key in filenames:
            raise ValueError(f"duplicate planned filename: {filename}")
        filenames.add(filename_key)

        existing = note.get("existing", False)
        if not isinstance(existing, bool):
            raise ValueError(f"{filename}: existing must be true or false")

        problem = note.get("problem")
        if not isinstance(problem, str) or not problem.strip():
            raise ValueError(f"{filename}: problem is empty")
        if SEPARATOR.search(problem):
            raise ValueError(f"{filename}: problem contains the note separator")
        problem = problem.strip()

        expected_sha256 = None
        same_problem = None
        if existing:
            if vault is None:
                raise ValueError(f"{filename}: existing target validation requires --vault")
            if "up" in note or "category" in note:
                raise ValueError(
                    f"{filename}: existing append cannot change up or category")
            same_problem = note.get("same_problem")
            if not isinstance(same_problem, str) or not same_problem.strip():
                raise ValueError(
                    f"{filename}: existing append requires a same_problem explanation")
            same_problem = same_problem.strip()
            target = vault / filename
            if not target.is_file():
                raise ValueError(f"{filename}: existing target does not exist")
            raw, _, current_problem = read_existing(target)
            if current_problem != problem:
                raise ValueError(
                    f"{filename}: planned problem does not exactly match current problem side")
            expected_sha256 = sha256_bytes(raw)
            category = None
            parents = []
        else:
            category = note.get("category", "Default")
            if not isinstance(category, str) or not category.strip():
                raise ValueError(f"{filename}: category must be non-empty text")
            category = category.strip()

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
            if source_locations.get(source_id):
                raise ValueError(
                    f"source {source_id} already exists in root note(s): "
                    f"{', '.join(source_locations[source_id])}")
            assigned[source_id] = filename

        normalized.append({
            "filename": filename,
            "existing": existing,
            "problem": problem,
            "category": category,
            "up": parents,
            "same_problem": same_problem,
            "expected_sha256": expected_sha256,
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


def render_source_blocks(note, sources):
    blocks = []
    for source_id in note["source_ids"]:
        source = sources[source_id]
        blocks.append(f"{source['text']}\n\n{source['locator']}")
    return "\n\n---\n\n".join(blocks)


def render_note(note, sources):
    return (
        f"{render_frontmatter(note)}\n\n"
        f"{note['problem']}\n\n***\n\n"
        f"{render_source_blocks(note, sources)}\n"
    )


def append_suffix(raw, note, sources):
    """Bytes to append without altering any existing byte."""
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    source_text = render_source_blocks(note, sources)
    source_text = source_text.replace("\r\n", "\n").replace("\r", "\n")
    source_bytes = source_text.replace("\n", newline.decode("ascii")).encode("utf-8")
    if raw.endswith(newline * 2):
        lead = b""
    elif raw.endswith(newline):
        lead = newline
    else:
        lead = newline * 2
    return lead + b"---" + newline * 2 + source_bytes + newline


def write_notes(notes, sources, destination, vault=None, append_existing=False):
    destination = Path(destination)
    vault = Path(vault) if vault is not None else None
    live_write = vault is not None and destination.resolve() == vault.resolve()

    operations = []
    targets = []
    for note in notes:
        filename = note["filename"]
        if note.get("existing"):
            if vault is None:
                raise ValueError(f"{filename}: staging an existing note requires vault")
            source_path = vault / filename
            raw, _, _ = read_existing(source_path)
            digest = sha256_bytes(raw)
            if digest != note.get("expected_sha256"):
                raise ValueError(f"{filename}: existing target changed after validation")
            suffix = append_suffix(raw, note, sources)
            if live_write:
                if not append_existing:
                    raise ValueError(
                        "refusing existing-note append without --append-existing: "
                        + filename)
                target = source_path
                before = file_state(target, include_bytes=True)
                operations.append(write_operation(
                    target, raw + suffix, before=before,
                    reason="append exact sources to an approved same-problem note"))
            else:
                target = destination / filename
                if target.exists():
                    raise ValueError(f"refusing to overwrite: {target}")
                operations.append(write_operation(
                    target, raw + suffix,
                    reason="stage complete patched note for criticism"))
        else:
            target = destination / filename
            if target.exists():
                raise ValueError(f"refusing to overwrite: {target}")
            operations.append(write_operation(
                target, render_note(note, sources).encode("utf-8"),
                reason="create approved source-grounded Problem Note"))
        targets.append(target)

    outcome = apply_writes(operations)
    if outcome["status"] != "applied":
        retry = "; do not retry before inspecting state" if outcome.get("do_not_retry") else ""
        raise RuntimeError(f"filesystem transaction {outcome['status']}{retry}: {outcome}")
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
    parser.add_argument("--append-existing", action="store_true",
                        help="with --write, permit explicit existing:true appends")
    parser.add_argument("--allow-unassigned", action="store_true",
                        help="permit bundle sources omitted from the plan")
    return parser.parse_args()


def main():
    args = arguments()
    if args.write and not args.vault:
        raise SystemExit("error: --write requires --vault")
    if args.append_existing and not args.write:
        raise SystemExit("error: --append-existing is valid only with --write")
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
        targets = write_notes(
            notes, sources, destination, vault=vault,
            append_existing=args.append_existing)
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(f"error: {exc}") from None

    existing_count = sum(1 for note in notes if note["existing"])
    print(json.dumps({
        "sources": len(sources),
        "assigned_sources": len(sources) - len(unassigned),
        "unassigned_sources": unassigned,
        "new_notes": len(notes) - existing_count,
        "existing_notes": existing_count,
        "destination": str(destination),
        "targets": [str(path) for path in targets],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
