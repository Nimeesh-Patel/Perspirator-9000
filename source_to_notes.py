#!/usr/bin/env python3
"""Stage exact source attachments to existing Problem Notes.

Source adapters recover facts; an agent supplies explanatory judgments in a
plan; this tool checks the mechanical boundary between them. It guarantees
coverage, prevents a source from being assigned twice, and permits an explicit,
guarded append when a source addresses an existing note's same problem.
Existing bytes are preserved; only exact source text and locator are appended.
Plans that create new notes are refused.

The source bundle may be a JSON list or an object whose `sources` or `records`
field is a list. Every source needs stable `id`, exact `text`, and a `locator` URI saying
where it lives — `https://...` for a web source, `readera://...` for a book
highlight. Records also state provenance, completeness, and withdrawal state.

Plan shape:
    {
      "notes": [{
        "file": "an existing problem.md",
        "existing": true,
        "problem": "The exact current problem side.",
        "same_problem": "Why these sources address this same problem.",
        "source_ids": ["source-1", "source-2"]
      }]
    }

An existing-note plan is a semantic assertion made by the agent, not a score
threshold. The exact problem field is also a stale-write guard: staging or
writing stops if the target's current problem side differs.

Examples:
    python source_to_notes.py sources.json plan.json --vault "/vault" --stage scratch/notes
    python source_to_notes.py sources.json plan.json --vault "/vault" --write --append-existing
"""

import argparse
import json
import re
import sys
from pathlib import Path

from note_chunks import read_note
from problem_half import parse_note
from change_transaction import (apply_writes, file_state, sha256_bytes,
                                write_operation)
from contracts import validate_provider_result, validate_source_record


INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
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
        locator = record["locator"]
        if source_id in by_id:
            raise ValueError(f"duplicate source id in bundle: {source_id}")
        if locator in seen_locators:
            raise ValueError(
                f"sources {seen_locators[locator]} and {source_id} share "
                f"locator {locator}")
        seen_locators[locator] = source_id
        by_id[source_id] = dict(record, id=source_id, locator=locator)
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
        text = read_note(path)
        if text is None:
            raise ValueError(f"cannot read source note while locating existing sources: {path}")
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
    source_locations = existing_source_locations(vault, sources) if vault else {}

    for index, note in enumerate(notes):
        if not isinstance(note, dict):
            raise ValueError(f"planned note {index} is not an object")
        filename = filename_for(note)
        filename_key = filename.casefold()
        if filename_key in filenames:
            raise ValueError(f"duplicate planned filename: {filename}")
        filenames.add(filename_key)

        if note.get("existing") is not True:
            raise ValueError(
                f"{filename}: creating new notes is retired; "
                "source attachments require existing:true")

        problem = note.get("problem")
        if not isinstance(problem, str) or not problem.strip():
            raise ValueError(f"{filename}: problem is empty")
        if SEPARATOR.search(problem):
            raise ValueError(f"{filename}: problem contains the note separator")
        problem = problem.strip()

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
            "existing": True,
            "problem": problem,
            "same_problem": same_problem,
            "expected_sha256": expected_sha256,
            "source_ids": source_ids,
        })

    unassigned = [source_id for source_id in sources if source_id not in assigned]
    if unassigned and not allow_unassigned:
        raise ValueError("unassigned source ids: " + ", ".join(unassigned))
    return normalized, unassigned


def render_source_blocks(note, sources):
    blocks = []
    for source_id in note["source_ids"]:
        source = sources[source_id]
        blocks.append(f"{source['text']}\n\n{source['locator']}")
    return "\n\n---\n\n".join(blocks)


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
        if note.get("existing") is not True:
            raise ValueError(f"{filename}: creating new notes is retired")
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
            if before.get("bytes") != raw:
                raise ValueError(f"{filename}: existing target changed after validation")
            operations.append(write_operation(
                target, raw + suffix, before=before,
                reason="append exact sources to an approved same-problem note"))
        else:
            target = destination / filename
            before = file_state(target)
            if before["exists"]:
                raise ValueError(f"refusing to overwrite: {target}")
            operations.append(write_operation(
                target, raw + suffix, before=before,
                reason="stage complete patched note for criticism"))
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
    parser.add_argument("plan", help="JSON attachment plan for existing notes")
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--stage", help="stage complete patched copies here")
    destination.add_argument("--write", action="store_true",
                             help="append to existing root notes in --vault")
    parser.add_argument("--vault",
                        help="vault containing the existing source targets")
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

    print(json.dumps({
        "sources": len(sources),
        "assigned_sources": len(sources) - len(unassigned),
        "unassigned_sources": unassigned,
        "new_notes": 0,
        "existing_notes": len(notes),
        "destination": str(destination),
        "targets": [str(path) for path in targets],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
