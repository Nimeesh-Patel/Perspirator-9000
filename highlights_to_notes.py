#!/usr/bin/env python3
"""File reading highlights into the vault note for the book they came from.

Consumes a `readera_highlights.py` bundle and appends each new highlight to its
book's `collection: Books` note, creating the note when the book has none. It
never rewrites an existing byte: a highlight already anchored in the vault is
skipped, and new blocks are appended inside the highlights section.

Book identity is resolved mechanically and reported, never guessed:

    1. anchor    an existing note already carries one of this book's `^re` ids
    2. filename  a note's name matches the book title exactly
    3. prefix    exactly one note name shares a prefix with the title; either
                 side may be the shortened one, since the older importer
                 truncated filenames and ReadEra metadata is often terse
    4. author    several titles match but exactly one note links this author
    5. create    no note exists for this book

An ambiguous title is refused rather than resolved, because deciding that two
titles denote one book is a semantic judgment. A passage already typed into a
note by hand is detected by its text and not imported a second time.

Examples:
    python highlights_to_notes.py sources.json --vault "/vault" --stage scratch
    python highlights_to_notes.py sources.json --vault "/vault" --write
"""

import argparse
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path

SECTION = "## Highlights (ReadEra)"
ANCHOR = "^re{}"
COLLECTION_LINE = re.compile(r"^collection:\s*Books\s*$", re.MULTILINE)
FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?", re.S)
HEADING = re.compile(r"^## .*$", re.MULTILINE)
ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
PUNCT = re.compile(r"[^a-z0-9]+")
WHITESPACE = re.compile(r"\s+")
# Below this length a verbatim match is as likely to be a common phrase as a
# transcription of the same passage.
TRANSCRIPTION_FLOOR = 60


def quoted(record):
    """The record's passage with whitespace flattened, or "" if too short to test."""
    text = WHITESPACE.sub(" ", record.get("quote", "")).strip()
    return text if len(text) >= TRANSCRIPTION_FLOOR else ""


def read_bundle(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read bundle {path}: {exc}") from exc
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list) or not records:
        raise ValueError("bundle has no records")
    return records


def book_notes(vault):
    """Vault-root notes that are book entities, as {stem: text}."""
    notes = OrderedDict()
    for path in sorted(vault.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        head = FRONTMATTER.match(text)
        if head and COLLECTION_LINE.search(head.group(1)):
            notes[path.stem] = text
    return notes


def sanitise(title):
    """A vault filename for a book title. Colons become underscores, as before."""
    name = title.replace(":", "_").strip()
    name = ILLEGAL.sub("", name).rstrip(". ")
    return name or "Untitled"


def comparable(text):
    return PUNCT.sub("", text.casefold())


def shares_a_prefix(title, stem):
    """Either name may be the shortened one.

    The older importer truncated filenames at 53 characters, so a note name can
    be a prefix of the book title. ReadEra metadata is also often the shorter
    form — a document titled "The Enlightenment" is the book the vault records
    as "The Enlightenment - The Pursuit of Happiness" — so the test runs both
    ways.
    """
    a, b = comparable(title), comparable(stem)
    return bool(a) and bool(b) and (a.startswith(b) or b.startswith(a))


def resolve(title, ids, authors, notes):
    """Return (stem, how) or (None, how) when the book has no note yet."""
    for stem, text in notes.items():
        if any(ANCHOR.format(i) in text for i in ids):
            return stem, "anchor"

    wanted = sanitise(title)
    for stem in notes:
        if stem.casefold() == wanted.casefold():
            return stem, "filename"

    candidates = [stem for stem in notes if shares_a_prefix(wanted, stem)]
    if len(candidates) == 1:
        return candidates[0], "prefix"
    if len(candidates) > 1:
        # The author decides between books whose titles alone are ambiguous.
        # A linked author is looked for anywhere in the note, because it may be
        # frontmatter or a body wikilink.
        links = [f"[[{a}]]" for a in authors]
        by_author = [stem for stem in candidates
                     if links and any(link in notes[stem] for link in links)]
        if len(by_author) == 1:
            return by_author[0], "author"
        raise ValueError(
            f"{title!r} matches several notes: {', '.join(sorted(candidates))}")
    return None, "create"


def render_block(record):
    parts = [record["text"].rstrip(), "", ANCHOR.format(record["id"])]
    stamp = []
    if record.get("page") is not None:
        stamp.append(f"Page {record['page']}")
    if record.get("created"):
        stamp.append(f"Added: {record['created'][:10]}")
    if stamp:
        parts.append(" · ".join(stamp))
    return "\n".join(parts)


def render_blocks(records):
    return "\n\n---\n\n".join(render_block(r) for r in records)


def new_note(title, records):
    authors = records[0]["book"].get("authors") or []
    links = ", ".join(f"[[{a}]]" for a in authors)
    lines = ["---", "collection: Books"]
    if authors:
        lines.append("authors:")
        lines += [f"- {json.dumps(f'[[{a}]]', ensure_ascii=False)}" for a in authors]
    lines += ["---", "", f"# {title}", ""]
    if links:
        lines += [f"*{links}*", ""]
    lines += [SECTION, "", render_blocks(records), ""]
    return "\n".join(lines)


def append_into_section(text, records):
    """Insert blocks at the end of the highlights section, altering nothing else."""
    blocks = render_blocks(records)
    start = text.find(SECTION)
    if start == -1:
        joiner = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return f"{text}{joiner}{SECTION}\n\n{blocks}\n"

    following = [m.start() for m in HEADING.finditer(text)
                 if m.start() > start]
    end = following[0] if following else len(text)
    head, tail = text[:end], text[end:]
    head = head.rstrip("\n")
    return f"{head}\n\n---\n\n{blocks}\n" + ("\n" + tail if tail else "")


def plan(records, vault):
    notes = book_notes(vault)
    by_book = OrderedDict()
    for record in records:
        by_book.setdefault(record["book"]["title"], []).append(record)

    actions, problems = [], []
    for title, group in by_book.items():
        ids = [r["id"] for r in group]
        try:
            stem, how = resolve(title, ids, group[0]["book"].get("authors") or [], notes)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        existing = notes.get(stem, "") if stem else ""
        flowed = WHITESPACE.sub(" ", existing)
        filename = (stem or sanitise(title)) + ".md"
        if how == "create" and (vault / filename).exists():
            # A note of this name exists but is not a book entity. It may be a
            # Problem Note, possibly with a live Anki card. Creating over it
            # would destroy authored prose, so the clash is reported instead.
            problems.append(
                f"{title!r} would create {filename}, which already exists and "
                "is not a book note")
            continue
        anchored = [r for r in group if ANCHOR.format(r["id"]) in existing]
        rest = [r for r in group if ANCHOR.format(r["id"]) not in existing]
        # A passage typed into a note by hand carries no anchor, so the anchor
        # test alone would import a second copy of something already written
        # out. Compare the text itself, with whitespace flattened, since a
        # transcription is usually reflowed.
        transcribed = [r for r in rest if quoted(r) and quoted(r) in flowed]
        fresh = [r for r in rest if r not in transcribed]
        actions.append({
            "title": title,
            "note": filename,
            "how": how,
            "new": fresh,
            "skipped": len(anchored),
            "transcribed": [{"id": r["id"], "page": r.get("page"),
                             "quote": r["quote"][:120]} for r in transcribed],
        })
    return actions, problems


def apply(actions, vault, destination):
    written = []
    for action in actions:
        if not action["new"]:
            continue
        source = vault / action["note"]
        target = destination / action["note"]
        if action["how"] == "create":
            # A create never replaces a file, in the vault or in a stage.
            if source.exists() or target.exists():
                raise ValueError(f"refusing to overwrite: {action['note']}")
            body = new_note(action["title"], action["new"])
        else:
            if not source.is_file():
                raise ValueError(f"resolved note vanished: {action['note']}")
            body = append_into_section(
                source.read_text(encoding="utf-8-sig"), action["new"])
            if destination != vault and target.exists():
                raise ValueError(f"refusing to overwrite staged note: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8", newline="\n")
        written.append(str(target))
    return written


def arguments():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bundle", help="JSON bundle from readera_highlights.py")
    parser.add_argument("--vault", required=True, help="vault root")
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--stage", help="write proposed notes here instead")
    destination.add_argument("--write", action="store_true",
                             help="write into the vault")
    return parser.parse_args()


def main():
    args = arguments()
    try:
        vault = Path(args.vault).expanduser().resolve(strict=False)
        if not vault.is_dir():
            raise ValueError(f"vault does not exist: {vault}")
        records = read_bundle(args.bundle)
        actions, problems = plan(records, vault)
        destination = vault if args.write else Path(args.stage).expanduser().resolve(
            strict=False)
        written = apply(actions, vault, destination)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from None

    print(json.dumps({
        "books": len(actions),
        "new_highlights": sum(len(a["new"]) for a in actions),
        "already_present": sum(a["skipped"] for a in actions),
        "already_transcribed": [t for a in actions for t in a["transcribed"]],
        "created_notes": [a["note"] for a in actions
                          if a["how"] == "create" and a["new"]],
        "resolved_by": {h: sum(1 for a in actions if a["how"] == h)
                        for h in ("anchor", "filename", "prefix", "author", "create")},
        "touched": written,
        "ambiguous": problems,
        "destination": str(destination),
    }, indent=2, ensure_ascii=False))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
