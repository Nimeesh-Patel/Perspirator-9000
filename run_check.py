#!/usr/bin/env python3
"""Report advisory field-presence facts for explicitly supplied run reports.

The required field names belong to memory/perspirator/runs/README.md. This
tool checks headings and bold labels only; it does not judge report content
or make an absent field an error.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from adapters import add_vault_argument
from note_chunks import section


FIELD = re.compile(r"^[ \t]*-[ \t]+\*\*([^*\r\n]+?):\*\*", re.MULTILINE)
HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+|$)(.*)$")
BOLD_LABEL = re.compile(r"^ {0,3}\*\*([^*\r\n]+)\*\*")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
SETEXT = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")


def required_fields(vault):
    """Load the Markdown-owned field names, or explain an unusable surface."""
    readme = Path(vault).expanduser().resolve() / "memory/perspirator/runs/README.md"
    try:
        text = readme.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read run-report README: {readme}: {exc}") from exc
    body = section(text, "## Required contents")
    if not body:
        raise ValueError(f"missing or empty ## Required contents section: {readme}")
    fields = list(dict.fromkeys(match.group(1).strip() for match in FIELD.finditer(body)
                                if match.group(1).strip()))
    if not fields:
        raise ValueError(f"no field names in ## Required contents: {readme}")
    return readme, fields


def normalized_label(label):
    label = label.strip().removesuffix(":").strip()
    if label.startswith("**") and label.endswith("**"):
        label = label[2:-2].strip()
    return label.removesuffix(":").strip().casefold()


def report_labels(text):
    """Return section/label names, excluding text inside fenced code blocks."""
    labels = set()
    fence = None
    lines = text.splitlines()
    for index, line in enumerate(lines):
        marker = FENCE.match(line)
        if fence:
            if (marker and marker.group(1)[0] == fence[0]
                    and len(marker.group(1)) >= len(fence)
                    and not marker.group(2).strip()):
                fence = None
            continue
        if marker:
            fence = marker.group(1)
            continue
        heading = HEADING.match(line)
        bold = BOLD_LABEL.match(line)
        if heading:
            title = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(1))
            labels.add(normalized_label(title))
        elif bold:
            labels.add(normalized_label(bold.group(1)))
        elif line.strip() and index + 1 < len(lines) and SETEXT.match(lines[index + 1]):
            labels.add(normalized_label(line))
    return labels


def inspect_report(path, fields):
    path = Path(path).expanduser().resolve()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        # Unobserved content cannot establish that a field is absent.
        return {"path": str(path), "status": "unreadable", "fields": {}}
    labels = report_labels(text)
    return {
        "path": str(path),
        "status": "ok",
        "fields": {field: ("present" if normalized_label(field) in labels else "absent")
                   for field in fields},
    }


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_vault_argument(parser)
    parser.add_argument("reports", metavar="run.md", nargs="+",
                        help="explicit report files to inspect; no folder scan")
    parser.add_argument("--json", action="store_true",
                        help="emit field-presence facts as JSON")
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    try:
        readme, fields = required_fields(args.vault)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    reports = [inspect_report(path, fields) for path in args.reports]
    payload = {"readme": str(readme), "fields": fields, "reports": reports}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"readme: {readme}")
        for report in reports:
            print(f"\n{report['path']}: {report['status']}")
            for field, state in report["fields"].items():
                print(f"  {field}: {state}")
    return 2 if any(report["status"] == "unreadable" for report in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
