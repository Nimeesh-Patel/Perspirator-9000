#!/usr/bin/env python3
"""Cheap Spotify playlist-ledger helpers (inventory JSON in / out).

Not a Spotify API client. Agents/browser capture inventories; this module
diffs and validates them. Pushable with Perspirator-9000 when ready.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REQUIRED = ("captured_at", "totals", "owned_playlists")


def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be an object")
    return data


def validate(data: dict, path: str) -> list[str]:
    problems = []
    for key in REQUIRED:
        if key not in data:
            problems.append(f"{path}: missing {key}")
    owned = data.get("owned_playlists")
    if owned is not None and not isinstance(owned, list):
        problems.append(f"{path}: owned_playlists must be a list")
    return problems


def name_set(data: dict, field: str) -> set[str]:
    value = data.get(field) or []
    if field == "other_owners":
        return {item["name"] for item in value if isinstance(item, dict) and "name" in item}
    return {str(name) for name in value}


def diff(prev: dict, nxt: dict) -> dict:
    prev_owned = name_set(prev, "owned_playlists")
    next_owned = name_set(nxt, "owned_playlists")
    prev_other = name_set(prev, "other_owners")
    next_other = name_set(nxt, "other_owners")
    prev_gen = name_set(prev, "generated")
    next_gen = name_set(nxt, "generated")
    return {
        "owned_added": sorted(next_owned - prev_owned),
        "owned_removed": sorted(prev_owned - next_owned),
        "other_added": sorted(next_other - prev_other),
        "other_removed": sorted(prev_other - next_other),
        "generated_added": sorted(next_gen - prev_gen),
        "generated_removed": sorted(prev_gen - next_gen),
        "prev_captured_at": prev.get("captured_at"),
        "next_captured_at": nxt.get("captured_at"),
        "prev_totals": prev.get("totals"),
        "next_totals": nxt.get("totals"),
    }


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.path)
    problems = validate(load(path), str(path))
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"ok: {path}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    prev_path = Path(args.prev)
    next_path = Path(args.next)
    prev = load(prev_path)
    nxt = load(next_path)
    problems = validate(prev, str(prev_path)) + validate(nxt, str(next_path))
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(json.dumps(diff(prev, nxt), indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_val = sub.add_parser("validate", help="validate a ledger JSON snapshot")
    p_val.add_argument("--path", required=True)
    p_val.set_defaults(func=cmd_validate)

    p_diff = sub.add_parser("diff", help="diff two ledger JSON snapshots")
    p_diff.add_argument("--prev", required=True)
    p_diff.add_argument("--next", required=True)
    p_diff.set_defaults(func=cmd_diff)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
