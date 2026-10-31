#!/usr/bin/env python3
"""Report advisory field-presence facts for explicitly supplied Markdown records.

The default field contract is memory/perspirator/runs/README.md. Another
explicit Markdown contract and section can supply fields for a handoff or
return packet. This tool checks headings and bold labels only; it does not
judge content, authority, retention, review quality or the truth of a result.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from adapters import add_vault_argument
from note_chunks import section


FIELD = re.compile(r"^ {0,3}-[ \t]+\*\*([^*\r\n]+?):\*\*", re.MULTILINE)
HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+|$)(.*)$")
BOLD_LABEL = re.compile(r"^ {0,3}\*\*([^*\r\n]+)\*\*")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
SETEXT = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")


def unfenced_lines(text):
    """Blank fenced examples while preserving line positions for both readers."""
    fence = None
    for line in text.splitlines():
        marker = FENCE.match(line)
        if fence:
            if (marker and marker.group(1)[0] == fence[0]
                    and len(marker.group(1)) >= len(fence)
                    and not marker.group(2).strip()):
                fence = None
            yield ""
        elif marker:
            fence = marker.group(1)
            yield ""
        else:
            yield line


def required_fields(vault, contract=None, section_heading="## Required contents"):
    """Load the Markdown-owned field names, or explain an unusable surface."""
    root = Path(vault).expanduser().resolve()
    readme = Path(contract).expanduser() if contract else Path("memory/perspirator/runs/README.md")
    if not readme.is_absolute():
        readme = root / readme
    readme = readme.resolve()
    label = "field contract" if contract else "run-report README"
    if not section_heading.startswith("## ") or not section_heading[3:].strip():
        raise ValueError("field contract section must be a ## heading")
    try:
        text = readme.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read {label}: {readme}: {exc}") from exc
    body = section("\n".join(unfenced_lines(text)), section_heading)
    if not body:
        raise ValueError(f"missing or empty {section_heading} section: {readme}")
    fields = list(dict.fromkeys(match.group(1).strip() for match in FIELD.finditer(body)
                                if match.group(1).strip()))
    if not fields:
        raise ValueError(f"no field names in {section_heading}: {readme}")
    return readme, fields


def normalized_label(label):
    label = label.strip().removesuffix(":").strip()
    if label.startswith("**") and label.endswith("**"):
        label = label[2:-2].strip()
    return label.removesuffix(":").strip().casefold()


def report_labels(text):
    """Return section/label names, excluding text inside fenced code blocks."""
    labels = set()
    lines = list(unfenced_lines(text))
    for index, line in enumerate(lines):
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
    parser.add_argument("--contract", metavar="note.md",
                        help="explicit field contract; relative paths are vault-relative")
    parser.add_argument("--section", default="## Required contents",
                        help="## heading containing the contract's - **Field:** list")
    parser.add_argument("--json", action="store_true",
                        help="emit field-presence facts as JSON")
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    try:
        readme, fields = required_fields(args.vault, args.contract, args.section)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    reports = [inspect_report(path, fields) for path in args.reports]
    # Keep readme for existing consumers; contract also describes non-run uses.
    payload = {"readme": str(readme), "contract": str(readme),
               "section": args.section, "fields": fields, "reports": reports}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"contract: {readme} ({args.section})")
        for report in reports:
            print(f"\n{report['path']}: {report['status']}")
            for field, state in report["fields"].items():
                print(f"  {field}: {state}")
    return 2 if any(report["status"] == "unreadable" for report in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
