#!/usr/bin/env python3
"""
problem_half.py — return the cheap "problem side" of an Obsidian note.

The Obsidian CLI's `read` returns a whole note. For relevance routing we
usually only need the frontmatter + everything ABOVE the first `***`
separator (the problem situation / open question), not the conjecture
below it. This script returns exactly that, and says unambiguously which
structural case it found.

Usage:
    python problem_half.py <path-to-note.md>
    python problem_half.py <path-to-note.md> --json
    python problem_half.py <path-to-note.md> --full-on-miss

Structural statuses (this script decides structure only — never whether
a note is *relevant*; that stays semantic judgement):

    problem-note    a `***` separator with a non-empty problem side
    empty-problem   a `***` separator but nothing above it
    no-separator    no `***` line — not a problem note
    missing-file    the path does not exist or is not a file
    unreadable      the path exists but could not be read

Output modes:
    - Default (text): backward compatible. Prints frontmatter (if any)
      followed by the problem side to stdout, exactly as before; the
      status is printed to stderr as `status: <status>` so the cases are
      distinguishable without changing what callers parse. A note with
      no `***` prints nothing from the body unless --full-on-miss is
      passed, in which case the whole body is printed.
    - --json: one JSON object on stdout — the stable machine-readable
      contract:
          {
            "path":          "<the path as given>",
            "status":        "<one of the statuses above>",
            "has_separator": true|false,
            "frontmatter":   "<yaml block without --- fences>" | null,
            "problem":       "<problem side>" | null,
            "body":          "<whole body>"   // only with --full-on-miss
                                              // on a no-separator note
            "error":         "<message>"      // only for missing-file /
                                              // unreadable
          }

Exit codes: 0 for any readable note (problem-note, empty-problem,
no-separator — structure is a finding, not an error); 2 for
missing-file, unreadable, or a usage error.

This is intentionally dumb and deterministic: split on the first `***`,
return the top. No parsing of the conjecture side, no network, no
Obsidian dependency — it reads the file directly from disk.
"""

import json
import sys
from pathlib import Path

STATUS_PROBLEM_NOTE = "problem-note"
STATUS_EMPTY_PROBLEM = "empty-problem"
STATUS_NO_SEPARATOR = "no-separator"
STATUS_MISSING_FILE = "missing-file"
STATUS_UNREADABLE = "unreadable"


def split_note(text: str):
    """Return (frontmatter_or_none, problem_side_or_none, has_separator)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    frontmatter = None
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            frontmatter = text[4:end]
            body = text[end + 5:]

    # Find the first line that is exactly *** (a problem/conjecture split)
    lines = body.split("\n")
    sep_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "***":
            sep_idx = i
            break

    if sep_idx is None:
        return frontmatter, None, False

    problem_side = "\n".join(lines[:sep_idx]).strip()
    return frontmatter, problem_side, True


def body_after_frontmatter(text: str, frontmatter):
    """The note body with the YAML block (if one was parsed) removed."""
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    if frontmatter is not None and body.startswith("---\n"):
        end = body.find("\n---\n", 4)
        if end != -1:
            body = body[end + 5:]
    return body.strip()


def classify(text: str):
    """Return (status, frontmatter_or_none, problem_side_or_none)."""
    frontmatter, problem_side, has_sep = split_note(text)
    if not has_sep:
        return STATUS_NO_SEPARATOR, frontmatter, None
    if not problem_side:
        return STATUS_EMPTY_PROBLEM, frontmatter, problem_side
    return STATUS_PROBLEM_NOTE, frontmatter, problem_side


def emit_json(record: dict, exit_code: int):
    print(json.dumps(record, ensure_ascii=False, indent=2))
    sys.exit(exit_code)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    full_on_miss = "--full-on-miss" in sys.argv
    as_json = "--json" in sys.argv

    if not args:
        print("usage: python problem_half.py <path-to-note.md> "
              "[--json] [--full-on-miss]", file=sys.stderr)
        sys.exit(2)

    raw_path = args[0]
    path = Path(raw_path).expanduser()

    if not path.is_file():
        if as_json:
            emit_json({"path": raw_path, "status": STATUS_MISSING_FILE,
                       "error": f"not a file: {path}"}, 2)
        print(f"error: not a file: {path}", file=sys.stderr)
        print(f"status: {STATUS_MISSING_FILE}", file=sys.stderr)
        sys.exit(2)

    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as e:
        if as_json:
            emit_json({"path": raw_path, "status": STATUS_UNREADABLE,
                       "error": str(e)}, 2)
        print(f"error: unreadable: {path}: {e}", file=sys.stderr)
        print(f"status: {STATUS_UNREADABLE}", file=sys.stderr)
        sys.exit(2)

    status, frontmatter, problem_side = classify(text)

    if as_json:
        record = {
            "path": raw_path,
            "status": status,
            "has_separator": status != STATUS_NO_SEPARATOR,
            "frontmatter": frontmatter,
            "problem": problem_side,
        }
        if status == STATUS_NO_SEPARATOR and full_on_miss:
            record["body"] = body_after_frontmatter(text, frontmatter)
        emit_json(record, 0)

    # Text mode: stdout stays exactly as it always was; the structural
    # status goes to stderr so the four cases are still distinguishable.
    out = []
    if frontmatter is not None:
        out.append("---\n" + frontmatter + "\n---")

    if status != STATUS_NO_SEPARATOR:
        if problem_side:
            out.append(problem_side)
    elif full_on_miss:
        out.append(body_after_frontmatter(text, frontmatter))
    # else: not a problem note, emit frontmatter only (or nothing)

    print("\n\n".join(out).strip())
    print(f"status: {status}", file=sys.stderr)


if __name__ == "__main__":
    main()
