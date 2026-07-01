#!/usr/bin/env python3
"""
problem_half.py — return the cheap "problem side" of an Obsidian note.

The Obsidian CLI's `read` returns a whole note. For relevance routing we
usually only need the frontmatter + everything ABOVE the first `***`
separator (the problem situation / open question), not the conjecture
below it. This script returns exactly that.

Usage:
    python problem_half.py <path-to-note.md>
    python problem_half.py <path-to-note.md> --full-on-miss

Behaviour:
    - Prints YAML frontmatter (if present) followed by the body text
      above the FIRST line that is exactly `***`.
    - If the note has no `***`, prints nothing from the body by default
      (it is not a problem note) unless --full-on-miss is passed, in
      which case the whole body is printed.
    - Exit code 0 always (a missing `***` is not an error).

This is intentionally dumb and deterministic: split on the first `***`,
return the top. No parsing of the conjecture side, no network, no
Obsidian dependency — it reads the file directly from disk.
"""

import sys
from pathlib import Path


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


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    full_on_miss = "--full-on-miss" in sys.argv

    if not args:
        print("usage: python problem_half.py <path-to-note.md> [--full-on-miss]",
              file=sys.stderr)
        sys.exit(2)

    path = Path(args[0]).expanduser()
    if not path.is_file():
        print(f"error: not a file: {path}", file=sys.stderr)
        sys.exit(2)

    text = path.read_text(encoding="utf-8-sig", errors="replace")
    frontmatter, problem_side, has_sep = split_note(text)

    out = []
    if frontmatter is not None:
        out.append("---\n" + frontmatter + "\n---")

    if has_sep:
        if problem_side:
            out.append(problem_side)
    elif full_on_miss:
        # No separator: emit the whole body after frontmatter
        body = text
        if frontmatter is not None and text.startswith("---\n"):
            end = text.find("\n---\n", 4)
            if end != -1:
                body = text[end + 5:]
        out.append(body.strip())
    # else: not a problem note, emit frontmatter only (or nothing)

    print("\n\n".join(out).strip())


if __name__ == "__main__":
    main()
