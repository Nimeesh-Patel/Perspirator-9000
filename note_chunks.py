#!/usr/bin/env python3
"""Form inspectable retrieval units from vault Markdown.

Processing is shared; formation is selected by note structure. A Problem Note
contributes its complete problem identity and normally one contextualized
conjecture. Long conjectures fall back through authored blocks before token
windows. Other notes retain heading-aware authored blocks.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from problem_half import parse_note  # noqa: E402

WIKILINK = re.compile(r"\[\[([^\]\|#]+)")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
LIST_ITEM = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+\S")
FENCE = re.compile(r"^\s*```")
THEMATIC = re.compile(r"^\s*---\s*$")

# Two different questions were being answered by one flat list per tool, which
# is why four of them had drifted apart. Kept separate so a difference between
# tools is either explained or visibly a defect.
#
# Folders that structurally never hold authored notes. This is a fact about the
# vault's layout, not a policy, so no tool has a reason to differ — one that
# does is a bug. (`Neighbour Retrieval.md`'s `## Exempt` currently names the
# same four, but that is a configurable retrieval choice which happens to
# coincide; it is loaded and validated from Markdown, not from here.)
NON_NOTE_FOLDERS = (".obsidian", ".trash", ".perspirator", "Attachments")

# Basic Memory's corpus. Whether it is in scope is a real corpus choice: the
# neighbour index includes it deliberately, while a tool that maps or writes
# root vault notes excludes it. Every exclusion of it should say why.
MEMORY_FOLDER = "memory"

DEFAULT_EXCLUDES = NON_NOTE_FOLDERS
DEFAULT_MAX_TOKENS = 256


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
    # YAML permits sequence items at the same indentation as their key. The
    # structural parser treats that spelling like the indented equivalent.
    lines = [('  ' + line if line.startswith('- ') else line) for line in lines]
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
    return category, list(dict.fromkeys(up))


def frontmatter_fields(text):
    """Every top-level ``key: value`` in the frontmatter block."""
    parsed = parse_note(text)
    if parsed["frontmatter"] is None:
        return {}
    fields = {}
    for line in parsed["frontmatter"].split("\n"):
        if ":" in line and not line[:1].isspace():
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip("'\"")
    return fields


def section(text, heading):
    """Lines under a ``##`` heading, up to the next one."""
    out, collecting = [], False
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            collecting = line.strip().lower() == heading.lower()
            continue
        if collecting:
            out.append(line)
    return "\n".join(out).strip()


def bullets(body):
    return [b.strip()[2:].strip() for b in body.split("\n")
            if b.strip().startswith("- ")]


def _blocks(body):
    """Return ``(start, text)`` authored blocks; multi-line lists stay whole."""
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
        if line.strip() == "***" or THEMATIC.match(line):
            flush()
            blocks.append((offsets[idx], line.strip()))
            continue
        if LIST_ITEM.match(line) and current and LIST_ITEM.match(current[0] or ""):
            flush()
        if start is None:
            start = offsets[idx]
        current.append(line)
    flush()
    return blocks


def rough_token_length(text):
    """Deterministic fallback used outside the embedding runtime."""
    return len(re.findall(r"\w+|[^\w\s]", text, re.UNICODE)) + 2


def _trimmed_span(full_text, start, end):
    raw = full_text[start:end]
    left = len(raw) - len(raw.lstrip())
    right = len(raw.rstrip())
    return start + left, start + right, raw.strip()


def _unit(path, vault_root, text, start, end, side, unit, strategy,
          heading=None, embedding_text=None):
    rel = path.relative_to(vault_root).as_posix()
    return {
        "note": rel,
        "stem": path.stem,
        "heading": list(heading or []),
        "start": start,
        "end": end,
        "text": text,
        "embedding_text": embedding_text if embedding_text is not None else text,
        "side": side,
        "unit": unit,
        "strategy": strategy,
        "corpus": "memory" if rel == "memory" or rel.startswith("memory/") else "vault",
        "links": extract_links(text),
    }


def _heading_blocks(body, absolute_start):
    """Authored content blocks with their enclosing heading paths."""
    out, headings = [], []
    for rel_start, block in _blocks(body):
        head = HEADING.match(block)
        if head:
            level = len(head.group(1))
            headings = [(old_level, title) for old_level, title in headings
                        if old_level < level]
            headings.append((level, head.group(2).strip()))
            continue
        if block in ("***", "---"):
            continue
        out.append({"start": absolute_start + rel_start,
                    "end": absolute_start + rel_start + len(block),
                    "text": block, "heading": [title for _, title in headings]})
    return out


def _windows(full_text, start, end, budget, token_length):
    """Last-resort whitespace windows whose text stays slice-inspectable."""
    out = []
    cursor = start
    while cursor < end:
        while cursor < end and full_text[cursor].isspace():
            cursor += 1
        if cursor >= end:
            break
        lo, hi, best = cursor + 1, end, cursor + 1
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate_end = mid
            if mid < end:
                gap = full_text.rfind(" ", cursor, mid + 1)
                newline = full_text.rfind("\n", cursor, mid + 1)
                boundary = max(gap, newline)
                if boundary > cursor:
                    candidate_end = boundary
            candidate = full_text[cursor:candidate_end].strip()
            if candidate and token_length(candidate) <= budget:
                best = candidate_end
                lo = mid + 1
            else:
                hi = mid - 1
        if best <= cursor:
            best = min(end, cursor + 1)
        window_end = best
        while window_end < end and not full_text[window_end].isspace():
            window_end += 1
        s, e, value = _trimmed_span(full_text, cursor, window_end)
        if value:
            out.append((s, e, value))
        cursor = max(window_end, cursor + 1)
    return out


def _conjecture_units(path, vault_root, parsed, identity, max_tokens, token_length,
                      oversize_strategy):
    conjecture = parsed["conjecture"] or ""
    if not conjecture:
        return []
    prefix = f"{path.stem}\n{identity}\n***\n"
    full_embedding = prefix + conjecture
    if token_length(conjecture) <= max_tokens:
        return [_unit(path, vault_root, conjecture,
                      parsed["conjecture_start"], parsed["conjecture_end"],
                      "answer", "conjecture", "whole-conjecture",
                      embedding_text=full_embedding)]

    blocks = _heading_blocks(conjecture, parsed["conjecture_start"])
    segments = []
    for block in blocks:
        if token_length(block["text"]) <= max_tokens:
            segments.append(block)
            continue
        payload_budget = max_tokens
        for start, end, value in _windows(parsed["normalized"], block["start"],
                                           block["end"], payload_budget, token_length):
            segments.append({"start": start, "end": end, "text": value,
                             "heading": block["heading"], "window": True})

    packed, current = [], []
    for segment in segments:
        if segment.get("window"):
            if current:
                packed.append(current)
                current = []
            packed.append([segment])
            continue
        trial = current + [segment]
        start, end = trial[0]["start"], trial[-1]["end"]
        _, _, value = _trimmed_span(parsed["normalized"], start, end)
        if current and token_length(value) > max_tokens:
            packed.append(current)
            current = [segment]
        else:
            current = trial
    if current:
        packed.append(current)

    out = []
    for group in packed:
        start, end = group[0]["start"], group[-1]["end"]
        start, end, value = _trimmed_span(parsed["normalized"], start, end)
        strategy = ("token-window" if len(group) == 1 and group[0].get("window")
                    else oversize_strategy)
        out.append(_unit(path, vault_root, value, start, end, "answer",
                         "conjecture", strategy, group[0]["heading"],
                         prefix + value))
    return out


def chunks_for_note(path, vault_root, text=None, max_tokens=DEFAULT_MAX_TOKENS,
                    token_length=None,
                    problem_strategy="identity-and-contextual-conjecture",
                    nonproblem_strategy="authored-blocks",
                    oversize_strategy="authored-boundaries-then-token-windows"):
    """Form retrieval units for one note with complete provenance."""
    text = read_note(path) if text is None else text
    if text is None:
        return []
    parsed = parse_note(text)
    normalized = parsed["normalized"]
    token_length = token_length or rough_token_length

    if parsed["has_separator"]:
        identity = parsed["problem"] or ""
        out = []
        if identity:
            out.append(_unit(path, vault_root, identity,
                             parsed["problem_start"], parsed["problem_end"],
                             "problem", "problem_identity", problem_strategy,
                             embedding_text=f"{path.stem}\n{identity}"))
        out.extend(_conjecture_units(path, vault_root, parsed, identity,
                                     max_tokens, token_length, oversize_strategy))
        return out

    out = []
    for block in _heading_blocks(parsed["body"], parsed["body_start"]):
        if token_length(block["text"]) <= max_tokens:
            out.append(_unit(path, vault_root, block["text"], block["start"],
                             block["end"], "none", "block", nonproblem_strategy,
                             block["heading"]))
            continue
        for start, end, value in _windows(normalized, block["start"], block["end"],
                                           max_tokens, token_length):
            out.append(_unit(path, vault_root, value, start, end, "none", "block",
                             "token-window", block["heading"]))
    return out


def iter_notes(vault_root, excludes=DEFAULT_EXCLUDES):
    excludes = DEFAULT_EXCLUDES if excludes is None else excludes
    for path in sorted(Path(vault_root).rglob("*.md")):
        rel = path.relative_to(vault_root).as_posix()
        if any(rel == e or rel.startswith(e + "/") for e in excludes):
            continue
        yield path


def all_chunks(vault_root, corpus=None, side=None, excludes=DEFAULT_EXCLUDES,
               **formation):
    vault_root = Path(vault_root)
    out = []
    for path in iter_notes(vault_root, excludes):
        for chunk in chunks_for_note(path, vault_root, **formation):
            if corpus and chunk["corpus"] != corpus:
                continue
            if side and chunk["side"] != side:
                continue
            out.append(chunk)
    return out


def vault_links(vault_root, excludes=DEFAULT_EXCLUDES):
    """target -> referring paths, existing stems, and enumerated notes."""
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
    print(f"{len(found)} units  corpus={args.corpus or 'all'} side={args.side or 'all'}")
    for chunk in found[:args.limit]:
        where = " > ".join(chunk["heading"]) or "(no heading)"
        print(f"\n[{chunk['corpus']}/{chunk['side']}/{chunk['unit']}] "
              f"{chunk['note']} :: {where}")
        print(f"  {chunk['text'][:160]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
