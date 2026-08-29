#!/usr/bin/env python3
"""Recover complete highlights and reader annotations from ReadEra backups.

This tool establishes source facts only. It opens a ReadEra `.bak` archive,
reads its library, and adapts every citation into the source-neutral
`{id, text, locator}` contract. It merges several backups by citation identity,
preserves the reader's own annotation alongside the quoted passage, and refuses
records it cannot represent faithfully rather than dropping them silently. It
does not decide which highlights share a problem or write Problem Notes.

A ReadEra backup is a full snapshot, not a delta, so the newest archive always
contains every surviving highlight and re-reading one is idempotent.

Backups are written on-device to:
    /storage/emulated/0/ReadEra/Backups/ReadEra-Premium_[yyyy-mm-dd_hh.mm].bak

Examples:
    python readera_highlights.py backup.bak
    python readera_highlights.py *.bak --out sources.json
    python readera_highlights.py backup.bak --book "Beginning of Infinity"
    python readera_highlights.py backup.bak --annotated-only
"""

import argparse
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from contracts import provider_result, source_record, status_for

LIBRARY_ENTRY = "library.json"
META_ENTRY = "meta.json"
SELECTED_PASSAGE = 3
SCHEME = "readera"


class BackupError(Exception):
    """The archive cannot be read as a ReadEra backup."""


def read_backup(path):
    """Return (library, meta) or refuse with the entries actually present."""
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BackupError(f"{path}: not a readable zip archive: {exc}") from exc

    with archive:
        names = archive.namelist()
        if LIBRARY_ENTRY not in names:
            raise BackupError(
                f"{path}: no {LIBRARY_ENTRY}; archive contains {', '.join(names) or 'nothing'}")
        try:
            library = json.loads(archive.read(LIBRARY_ENTRY).decode("utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BackupError(f"{path}: unreadable {LIBRARY_ENTRY}: {exc}") from exc
        meta = {}
        if META_ENTRY in names:
            try:
                meta = json.loads(archive.read(META_ENTRY).decode("utf-8-sig"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                meta = {}

    if not isinstance(library, dict) or not isinstance(library.get("docs"), list):
        raise BackupError(f"{path}: {LIBRARY_ENTRY} has no docs list")
    return library, meta


def split_authors(raw):
    """Split ReadEra's author string without reordering or normalising names.

    ReadEra stores author order inconsistently ("Karl Popper" and "Popper
    Karl" both occur). Deciding that two spellings denote one person is a
    semantic judgment and is deliberately left to the agent.
    """
    if not isinstance(raw, str):
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def book_identity(data, path):
    """Return book facts, recovering a title from the filename when metadata lacks one.

    A document whose embedded metadata has no title still carries
    `doc_file_name_title`. Skipping such documents discards real highlights,
    so the fallback is used and recorded rather than the document dropped.
    """
    sha1 = (data.get("doc_sha1") or "").strip()
    if not sha1:
        raise BackupError("document has no doc_sha1 and cannot be identified")
    title = (data.get("doc_title") or "").strip()
    title_source = "metadata"
    if not title:
        title = (data.get("doc_file_name_title") or "").strip()
        title_source = "filename"
    if not title:
        raise BackupError(f"document {sha1} has neither a title nor a file name")
    return {
        "doc_sha1": sha1,
        "title": title,
        "title_source": title_source,
        "authors": split_authors(data.get("doc_authors")),
        "authors_raw": (data.get("doc_authors") or "").strip(),
        "format": data.get("doc_format"),
        "backup": str(path),
    }


def quote_block(text):
    """Render the book's own words as a Markdown blockquote."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").strip().split("\n")
    return "\n".join(f"> {line}" if line.strip() else ">" for line in lines)


def source_text(body, annotation):
    """Keep the quoted passage and the reader's annotation distinguishable.

    Both are source material, but one is the book's sentence and the other is
    the researcher's conjecture about it. Flattening them into one stream
    produces a record that cannot say whose claim it is.
    """
    block = quote_block(body)
    if annotation:
        return f"{block}\n\n{annotation.strip()}"
    return block


def timestamp(millis):
    if not isinstance(millis, (int, float)) or millis <= 0:
        return None
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat()


def compact_citation(citation, book):
    """Adapt one citation, refusing anything it cannot represent faithfully."""
    uri = (citation.get("note_uri") or "").strip()
    if not uri:
        raise BackupError("citation has no note_uri")

    note_type = citation.get("note_type")
    if note_type != SELECTED_PASSAGE:
        raise BackupError(
            f"citation {uri}: unsupported note_type {note_type!r}; "
            "refusing rather than guessing what it holds")

    body = citation.get("note_body")
    if not isinstance(body, str) or not body.strip():
        raise BackupError(f"citation {uri}: empty note_body")

    annotation = citation.get("note_extra")
    annotation = annotation.strip() if isinstance(annotation, str) else ""

    return source_record(
        uri, source_text(body, annotation),
        f"{SCHEME}://{book['doc_sha1']}/{uri}", provider="readera",
        provenance={"provider": "readera", "backup": book["backup"]},
        quote=body.strip(), annotation=annotation,
        has_annotation=bool(annotation),
        book={k: v for k, v in book.items() if k != "backup"},
        page=citation.get("note_page"), position=citation.get("note_index"),
        mark=citation.get("note_mark"),
        created=timestamp(citation.get("note_insert_time")),
        modified=timestamp(citation.get("note_modified_time")),
        backup=book["backup"],
    )


def collect(paths):
    """Merge backups by citation identity; a later snapshot supersedes an earlier one.

    Each archive is a snapshot of one moment, so a union across archives answers
    "what has ever existed" while the newest answers "what exists now". They
    differ whenever a highlight or a whole book was deleted in ReadEra, and the
    difference is reported rather than silently resolved.
    """
    records = {}
    errors = []
    backups = []
    seen_in = {}

    for index, path in enumerate(paths):
        try:
            library, meta = read_backup(path)
        except BackupError as exc:
            errors.append({"backup": str(path), "error": str(exc)})
            continue

        try:
            modified = Path(path).stat().st_mtime
        except OSError:
            modified = 0.0
        backups.append({
            "path": str(path),
            "created": timestamp(meta.get("date")),
            "app": meta.get("appname"),
            "device": meta.get("device"),
            "documents": len(library["docs"]),
            "_order": (timestamp(meta.get("date")) or "", modified, index),
        })

        for document in library["docs"]:
            data = document.get("data") or {}
            citations = document.get("citations") or []
            if not citations:
                continue
            if data.get("doc_delete_time"):
                continue
            try:
                book = book_identity(data, path)
            except BackupError as exc:
                errors.append({"backup": str(path), "error": str(exc)})
                continue
            for citation in citations:
                try:
                    record = compact_citation(citation, book)
                except BackupError as exc:
                    errors.append({"backup": str(path), "book": book["title"],
                                   "error": str(exc)})
                    continue
                seen_in.setdefault(record["id"], set()).add(str(path))
                previous = records.get(record["id"])
                if previous is None or (record["modified"] or "") >= (previous["modified"] or ""):
                    records[record["id"]] = record

    # The snapshot moment decides which archive is current; file mtime and then
    # argument order only break a tie between archives claiming the same moment.
    newest = max(backups, key=lambda b: b["_order"], default=None)
    for backup in backups:
        del backup["_order"]
    for record in records.values():
        withdrawn = bool(
            newest and newest["path"] not in seen_in.get(record["id"], set()))
        record["withdrawal_state"] = "withdrawn" if withdrawn else "active"

    ordered = sorted(
        records.values(),
        key=lambda r: (r["book"]["title"].casefold(),
                       r["position"] if r["position"] is not None else 0.0),
    )
    return ordered, errors, backups, newest


def filtered(records, book=None, annotated_only=False, include_withdrawn=False):
    if not include_withdrawn:
        records = [r for r in records if r["withdrawal_state"] == "active"]
    if book:
        needle = book.casefold()
        records = [r for r in records if needle in r["book"]["title"].casefold()]
    if annotated_only:
        records = [r for r in records if r["has_annotation"]]
    return records


def build_result(paths, book=None, annotated_only=False, include_withdrawn=False):
    records, errors, backups, newest = collect(paths)
    withdrawn = [r for r in records if r["withdrawal_state"] == "withdrawn"]
    records = filtered(records, book=book, annotated_only=annotated_only,
                       include_withdrawn=include_withdrawn)
    titles = {r["book"]["title"] for r in records}
    return provider_result(
        "readera", "recover highlights", status_for(records, errors),
        scope={"backups": len(backups), "books": len(titles)},
        freshness={"current_backup": newest["path"] if newest else None,
                   "created": newest["created"] if newest else None},
        records=records, errors=errors,
        backups=backups,
        current_backup=newest["path"] if newest else None,
        books=len(titles), highlights=len(records),
        annotated=sum(1 for r in records if r["has_annotation"]),
        recovered_titles_from_filename=sorted(
            {r["book"]["title"] for r in records
             if r["book"]["title_source"] == "filename"}),
        withdrawn=[
            {"id": r["id"], "book": r["book"]["title"], "page": r["page"],
             "annotated": r["has_annotation"]}
            for r in withdrawn
        ],
    )


def arguments():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("backups", nargs="+", help="ReadEra .bak archives")
    parser.add_argument("--out", help="write JSON here instead of stdout")
    parser.add_argument("--book", help="only citations whose book title contains this")
    parser.add_argument("--annotated-only", action="store_true",
                        help="only citations carrying the reader's own note")
    parser.add_argument("--include-withdrawn", action="store_true",
                        help="also emit highlights the newest backup no longer "
                             "has, which were deleted in ReadEra")
    parser.add_argument("--indent", type=int, default=2,
                        help="JSON indentation (default: 2)")
    return parser.parse_args()


def main():
    args = arguments()
    paths = [Path(name) for name in args.backups]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise SystemExit("error: no such backup: " + ", ".join(missing))

    result = build_result(paths, book=args.book,
                          annotated_only=args.annotated_only,
                          include_withdrawn=args.include_withdrawn)
    payload = json.dumps(result, ensure_ascii=False, indent=args.indent) + "\n"
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8", newline="\n")
        withdrawn = len(result["withdrawn"])
        note = (f", {withdrawn} withdrawn in ReadEra"
                + ("" if args.include_withdrawn else " and excluded")) if withdrawn else ""
        print(
            f"wrote {result['highlights']} highlights from {result['books']} books "
            f"({result['annotated']} annotated, {len(result['errors'])} errors{note}) "
            f"to {args.out}",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(payload)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
