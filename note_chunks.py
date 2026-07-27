#!/usr/bin/env python3
"""
note_chunks.py — the one structural parser for vault Markdown.

Everything that reads Markdown structure goes through here: frontmatter,
wikilinks, headings, list items, and the `***` problem/conjecture split. The
`***` contract itself stays owned by problem_half.split_note(); this module
imports it rather than re-implementing it.

A chunk is a paragraph-sized block carrying enough provenance to interpret it
later:

    note        path relative to the vault root, forward slashes
    stem        filename without .md
    heading     list of enclosing headings, outermost first
    start, end  character offsets into the file text
    text        the chunk itself
    side        problem | answer | none   (relative to the first `***`)
    corpus      memory | vault            (preserves the split problem_index
                                           enforces; memory notes are largely
                                           derived from vault notes)
    links       wikilink targets occurring inside the chunk

A multi-line list item is ONE chunk. That matters: the previous extractor took
list items line by line and truncated multi-line problems at their first
newline, which made a third of a recurrence evaluation unjudgeable.

This module decides nothing about importance, recurrence, candidacy, or
placement. It reports structure.

Usage:
    python note_chunks.py <vault-root> [--corpus memory|vault]
                          [--side problem|answer|none] [--json]
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from problem_half import split_note  # noqa: E402

WIKILINK = re.compile(r"\[\[([^\]\|#]+)")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
LIST_ITEM = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+\S")
FENCE = re.compile(r"^\s*```")

DEFAULT_EXCLUDES = (".obsidian", ".trash", ".perspirator", "Attachments")


def read_note(path):
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None


def extract_links(text):
    """Unique wikilink targets, first-seen order."""
    seen = {}
    for match in WIKILINK.finditer(text):
        target = match.group(1).strip()
        if target:
            seen.setdefault(target, None)
    return list(seen)


def parse_frontmatter(fm):
    """category/collection and up: parents, without a YAML dependency."""
    category, up = None, []
    if not fm:
        return category, up
    lines = fm.split("\n")
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("category:"):
            category = stripped[len("category:"):].strip() or category
        elif stripped.startswith("collection:") and category is None:
            category = stripped[len("collection:"):].strip() or None
        elif stripped.startswith("up:"):
            up.extend(extract_links(stripped[len("up:"):].strip()))
            j = i + 1
            while j < len(lines) and (lines[j].strip() == "" or lines[j][:1].isspace()):
                up.extend(extract_links(lines[j]))
                j += 1
            i = j
            continue
        i += 1
    seen = {}
    for u in up:
        seen.setdefault(u, None)
    return category, list(seen)


def frontmatter_fields(text):
    """Every top-level `key: value` in the frontmatter block."""
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    fields = {}
    for line in text[4:end].split("\n"):
        if ":" in line and not line[:1].isspace():
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip("'\"")
    return fields


def section(text, heading):
    """Lines under a `## ` heading, up to the next one. Case-insensitive."""
    out, collecting = [], False
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            collecting = line.strip().lower() == heading.lower()
            continue
        if collecting:
            out.append(line)
    return "\n".join(out).strip()


def bullets(body):
    return [b.strip()[2:].strip() for b in body.split("\n") if b.strip().startswith("- ")]


def body_offset(text):
    """Character offset where the body starts, after any frontmatter."""
    text = text.replace("\r\n", "\n")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return end + 5
    return 0


def _blocks(body):
    """(start_offset, text) per block. A multi-line list item is one block."""
    lines = body.split("\n")
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1

    blocks, current, start, in_fence = [], [], None, False

    def flush():
        nonlocal current, start
        if current and "\n".join(current).strip():
            blocks.append((start, "\n".join(current).strip()))
        current, start = [], None

    for idx, line in enumerate(lines):
        if FENCE.match(line):
            in_fence = not in_fence
            if start is None:
                start = offsets[idx]
            current.append(line)
            if not in_fence:
                flush()
            continue
        if in_fence:
            current.append(line)
            continue
        if not line.strip():
            flush()
            continue
        if line.strip() == "***":
            # A thematic break is its own block, and the vault's notes rarely
            # leave a blank line around it — without this the problem side and
            # the conjecture side glue into one chunk.
            flush()
            blocks.append((offsets[idx], "***"))
            continue
        if LIST_ITEM.match(line) and current and LIST_ITEM.match(current[0] or ""):
            # a new item ends the previous one; continuation lines do not
            flush()
        if start is None:
            start = offsets[idx]
        current.append(line)
    flush()
    return blocks


def chunks_for_note(path, vault_root, text=None):
    """Every chunk in one note, with provenance."""
    text = read_note(path) if text is None else text
    if text is None:
        return []
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    frontmatter, _, has_sep = split_note(text)
    base = body_offset(text)
    body = text[base:]

    sep_at = None
    if has_sep:
        for match in re.finditer(r"^\*\*\*\s*$", body, re.M):
            sep_at = match.start()
            break

    rel = path.relative_to(vault_root).as_posix()
    corpus = "memory" if rel == "memory" or rel.startswith("memory/") else "vault"

    out, heading_path = [], []
    for start, block in _blocks(body):
        head = HEADING.match(block)
        if head:
            level = len(head.group(1))
            heading_path = heading_path[:level - 1] + [head.group(2).strip()]
            continue
        if block.strip() == "***":
            continue
        if sep_at is None:
            side = "none"
        else:
            side = "problem" if start < sep_at else "answer"
        out.append({
            "note": rel,
            "stem": path.stem,
            "heading": list(heading_path),
            "start": base + start,
            "end": base + start + len(block),
            "text": block,
            "side": side,
            "corpus": corpus,
            "links": extract_links(block),
        })
    return out


def iter_notes(vault_root, excludes=DEFAULT_EXCLUDES):
    for path in sorted(Path(vault_root).rglob("*.md")):
        rel = path.relative_to(vault_root).as_posix()
        if any(rel == e or rel.startswith(e + "/") for e in excludes):
            continue
        yield path


def all_chunks(vault_root, corpus=None, side=None, excludes=DEFAULT_EXCLUDES):
    vault_root = Path(vault_root)
    out = []
    for path in iter_notes(vault_root, excludes):
        for chunk in chunks_for_note(path, vault_root):
            if corpus and chunk["corpus"] != corpus:
                continue
            if side and chunk["side"] != side:
                continue
            out.append(chunk)
    return out


def vault_links(vault_root, excludes=DEFAULT_EXCLUDES):
    """target -> set of referring note paths, and the set of existing stems."""
    vault_root = Path(vault_root)
    refs, existing = {}, set()
    notes = list(iter_notes(vault_root, excludes))
    for path in notes:
        existing.add(path.stem.lower())
    for path in notes:
        text = read_note(path)
        if text is None:
            continue
        for target in extract_links(text):
            refs.setdefault(target.split("/")[-1], set()).add(path)
    return refs, existing, notes


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vault")
    parser.add_argument("--corpus", choices=("memory", "vault"))
    parser.add_argument("--side", choices=("problem", "answer", "none"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    root = Path(args.vault).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"error: not a directory: {root}")

    found = all_chunks(root, args.corpus, args.side)
    if args.json:
        print(json.dumps(found, ensure_ascii=False, indent=1))
        return 0
    print(f"{len(found)} chunks  corpus={args.corpus or 'all'} side={args.side or 'all'}")
    for chunk in found[:args.limit]:
        where = " > ".join(chunk["heading"]) or "(no heading)"
        print(f"\n[{chunk['corpus']}/{chunk['side']}] {chunk['note']} :: {where}")
        print(f"  {chunk['text'][:160]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
