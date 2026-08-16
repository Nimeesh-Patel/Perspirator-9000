#!/usr/bin/env python3
"""Derive exact disposition-specific child manifests from an approved parent.

This mechanism never selects semantic groups.  The caller names exact parent
group identities and supplies the authority explanation; the tool preserves
their group and item objects unchanged, recalculates child totals, and records
the parent content address so the child operation remains criticisable.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import date
from pathlib import Path

from directory_audit import sha256_file


def derive_manifest(parent_path: Path, group_names: list[str],
                    disposition: str, authority: str) -> dict:
    parent_path = parent_path.resolve(strict=True)
    if disposition not in {"recycle", "permanent"}:
        raise ValueError("disposition must be recycle or permanent")
    if not group_names or len(group_names) != len(set(group_names)):
        raise ValueError("group names must be non-empty and unique")
    if not authority.strip():
        raise ValueError("authority explanation must be non-empty")
    payload = json.loads(parent_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("parent must be a cleanup manifest with schema_version 1")
    groups = payload.get("groups")
    if not isinstance(groups, list):
        raise ValueError("parent groups must be a list")
    by_name = {}
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            raise ValueError(f"parent group {index} must be an object")
        name = group.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"parent group {index} has an invalid name")
        if name in by_name:
            raise ValueError(f"parent has duplicate group name: {name}")
        by_name[name] = group
    missing = [name for name in group_names if name not in by_name]
    if missing:
        raise ValueError("unknown parent groups: " + ", ".join(missing))
    selected = [copy.deepcopy(by_name[name]) for name in group_names]
    for group in selected:
        if (not isinstance(group.get("bytes"), int) or group["bytes"] < 0 or
                not isinstance(group.get("items"), list) or not group["items"]):
            raise ValueError(f"selected parent group is malformed: {group['name']}")
    total = sum(group["bytes"] for group in selected)
    return {
        "schema_version": 1,
        "created_at": date.today().isoformat(),
        "root": payload["root"],
        "status": "authorized-derived-transaction",
        "disposition": disposition,
        "operation": ("move exact nominated paths to the Windows Recycle Bin"
                      if disposition == "recycle"
                      else "permanently delete exact nominated paths"),
        "authority": authority.strip(),
        "derived_from_manifest": str(parent_path),
        "derived_from_manifest_sha256": sha256_file(parent_path),
        "total_bytes": total,
        "total_gb_decimal": round(total / 1_000_000_000, 3),
        "total_gib": round(total / (1024 ** 3), 3),
        "groups": selected,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parent", type=Path)
    parser.add_argument("--group", action="append", required=True,
                        help="exact parent group name; repeatable")
    parser.add_argument("--disposition", choices=("recycle", "permanent"),
                        required=True)
    parser.add_argument("--authority", required=True,
                        help="the supplied decision that authorizes this partition")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = derive_manifest(
            args.parent, args.group, args.disposition, args.authority)
        output = args.out.resolve(strict=False)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        summary = {
            "status": "created", "path": str(output),
            "sha256": sha256_file(output), "disposition": args.disposition,
            "groups": args.group, "targets": sum(
                len(group["items"]) for group in result["groups"]),
            "bytes": result["total_bytes"],
        }
    except (OSError, ValueError, json.JSONDecodeError) as error:
        summary = {"status": "refused", "error": str(error)}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["status"] == "created" else 2


if __name__ == "__main__":
    raise SystemExit(main())
