#!/usr/bin/env python3
"""Parse the structural problem/conjecture boundary of an Obsidian note.

The parser is deliberately semantic-free: it recognizes frontmatter and the
first line containing only ``***``. Consumers reuse one full structural record.
"""

import json
import sys
from pathlib import Path

STATUS_PROBLEM_NOTE = "problem-note"
STATUS_EMPTY_PROBLEM = "empty-problem"
STATUS_NO_SEPARATOR = "no-separator"
STATUS_MISSING_FILE = "missing-file"
STATUS_UNREADABLE = "unreadable"


def normalize(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_note(text: str):
    """Return one normalized structural representation of a note.

    Offsets address ``normalized``. Problem/conjecture offsets exclude the
    separator and surrounding whitespace, so their slices equal the returned
    strings whenever those strings are non-empty.
    """
    normalized = normalize(text)
    frontmatter = None
    body_start = 0
    if normalized.startswith("---\n"):
        end = normalized.find("\n---\n", 4)
        if end != -1:
            frontmatter = normalized[4:end]
            body_start = end + 5

    body = normalized[body_start:]
    separator = None
    pos = 0
    in_fence = False
    for line in body.splitlines(keepends=True):
        stripped = line.rstrip("\n").strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            pos += len(line)
            continue
        if not in_fence and stripped == "***":
            separator = (body_start + pos,
                         body_start + pos + len(line.rstrip("\n")))
            break
        pos += len(line)

    if separator is None:
        return {
            "normalized": normalized,
            "frontmatter": frontmatter,
            "body": body,
            "body_start": body_start,
            "has_separator": False,
            "separator_start": None,
            "separator_end": None,
            "problem": None,
            "problem_start": None,
            "problem_end": None,
            "conjecture": None,
            "conjecture_start": None,
            "conjecture_end": None,
        }

    sep_start, sep_end = separator
    raw_problem = normalized[body_start:sep_start]
    problem = raw_problem.strip()
    problem_start = body_start + (len(raw_problem) - len(raw_problem.lstrip()))
    problem_end = body_start + len(raw_problem.rstrip())

    after = sep_end
    if after < len(normalized) and normalized[after] == "\n":
        after += 1
    raw_conjecture = normalized[after:]
    conjecture = raw_conjecture.strip()
    conjecture_start = after + (len(raw_conjecture) - len(raw_conjecture.lstrip()))
    conjecture_end = after + len(raw_conjecture.rstrip())

    return {
        "normalized": normalized,
        "frontmatter": frontmatter,
        "body": body,
        "body_start": body_start,
        "has_separator": True,
        "separator_start": sep_start,
        "separator_end": sep_end,
        "problem": problem,
        "problem_start": problem_start,
        "problem_end": problem_end,
        "conjecture": conjecture,
        "conjecture_start": conjecture_start,
        "conjecture_end": conjecture_end,
    }


def body_after_frontmatter(text: str, frontmatter=None):
    """The normalized note body with any parsed YAML block removed."""
    parsed = parse_note(text)
    return parsed["body"].strip()


def classify(text: str):
    """Return ``(status, frontmatter, problem)`` without semantic judgment."""
    parsed = parse_note(text)
    if not parsed["has_separator"]:
        return STATUS_NO_SEPARATOR, parsed["frontmatter"], None
    if not parsed["problem"]:
        return STATUS_EMPTY_PROBLEM, parsed["frontmatter"], parsed["problem"]
    return STATUS_PROBLEM_NOTE, parsed["frontmatter"], parsed["problem"]


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
    except OSError as exc:
        if as_json:
            emit_json({"path": raw_path, "status": STATUS_UNREADABLE,
                       "error": str(exc)}, 2)
        print(f"error: unreadable: {path}: {exc}", file=sys.stderr)
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

    out = []
    if frontmatter is not None:
        out.append("---\n" + frontmatter + "\n---")
    if status != STATUS_NO_SEPARATOR:
        if problem_side:
            out.append(problem_side)
    elif full_on_miss:
        out.append(body_after_frontmatter(text, frontmatter))
    print("\n\n".join(out).strip())
    print(f"status: {status}", file=sys.stderr)


if __name__ == "__main__":
    main()
