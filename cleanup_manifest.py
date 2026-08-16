#!/usr/bin/env python3
"""Validate that an approved directory-cleanup manifest still names exact state.

This tool is deliberately read-only.  The manifest carries the explanatory
retention judgment; this mechanism proves only that every nominated target is
still inside the declared root and still has the approved byte identity.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
from pathlib import Path

from directory_audit import _is_reparse, scan_tree, sha256_file


SCHEMA_VERSION = 1


def tree_state(root: Path, *, progress=None,
               progress_every: int = 10_000,
               hash_workers: int = 8) -> dict:
    """Return the deterministic regular-file identity of a directory tree.

    Progress is explanatory evidence only: it reports observed scan and hash
    state without weakening the final all-or-nothing identity result.
    """
    root = root.resolve(strict=True)

    def report_scan(state):
        if progress:
            progress({"event": "tree-scan-progress", "root": str(root),
                      **state})

    files, boundaries, directories = scan_tree(
        root, progress=report_scan, progress_every=progress_every)
    digest = hashlib.sha256()
    hashed_bytes = 0
    total_bytes = sum(entry.size for entry in files)
    batch_size = max(progress_every, hash_workers * 64)
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=hash_workers) as executor:
        for start in range(0, len(files), batch_size):
            batch = files[start:start + batch_size]
            identities = executor.map(
                sha256_file, (item.path for item in batch))
            for offset, (item, identity) in enumerate(
                    zip(batch, identities), start=1):
                index = start + offset
                hashed_bytes += item.size
                digest.update(item.relative.encode("utf-8", "surrogatepass"))
                digest.update(b"\0")
                digest.update(str(item.size).encode("ascii"))
                digest.update(b"\0")
                digest.update(identity.encode("ascii"))
                digest.update(b"\n")
                if progress and index % progress_every == 0:
                    progress({
                        "event": "tree-hash-progress", "root": str(root),
                        "files_hashed": index, "files_total": len(files),
                        "bytes_hashed": hashed_bytes,
                        "bytes_total": total_bytes,
                    })
    if progress:
        progress({
            "event": "tree-hash-complete", "root": str(root),
            "files_hashed": len(files), "files_total": len(files),
            "bytes_hashed": hashed_bytes, "bytes_total": total_bytes,
        })
    return {
        "file_count": len(files),
        "directory_count": directories,
        "bytes": sum(item.size for item in files),
        "tree_sha256": digest.hexdigest(),
        "reparse_boundaries": boundaries,
    }


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


def _resolve_target(source: Path, root: Path) -> Path:
    """Resolve an in-root target while refusing every reparse boundary."""
    lexical = Path(os.path.abspath(source))
    try:
        parts = lexical.relative_to(root).parts
    except ValueError as error:
        raise ValueError("target escapes the declared root") from error
    if not parts:
        raise ValueError("target equals the declared root")
    current = root
    for part in parts:
        current = current / part
        if _is_reparse(current):
            raise ValueError(f"reparse boundary: {current}")
    resolved = lexical.resolve(strict=True)
    if not _inside(resolved, root):
        raise ValueError("target escapes or equals the declared root")
    return resolved


def validate_manifest(manifest_path: Path, *, progress=None,
                      progress_every: int = 10_000,
                      hash_workers: int = 8) -> dict:
    """Validate schema, containment, totals, and current target identities."""
    manifest_path = manifest_path.resolve(strict=True)
    problems = []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        return {
            "status": "invalid",
            "manifest": str(manifest_path),
            "problems": [f"cannot read manifest: {error}"],
        }
    if not isinstance(payload, dict):
        return {
            "status": "invalid",
            "manifest": str(manifest_path),
            "problems": ["manifest root must be an object"],
        }
    if payload.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION}")

    try:
        root_source = Path(payload["root"])
        if not root_source.is_absolute():
            raise ValueError("root must be absolute")
        if _is_reparse(root_source):
            raise ValueError("root is a reparse point")
        root = root_source.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("root is not a directory")
    except (KeyError, OSError, ValueError) as error:
        problems.append(f"invalid root: {error}")
        root = None

    groups = payload.get("groups")
    if not isinstance(groups, list) or not groups:
        problems.append("groups must be a non-empty list")
        groups = []

    targets = []
    seen_paths = set()
    resolved_paths = []
    expected_total = 0
    observed_total = 0
    group_names = set()
    for group_index, group in enumerate(groups):
        label = f"group {group_index}"
        if not isinstance(group, dict):
            problems.append(f"{label} must be an object")
            continue
        name = group.get("name")
        if not isinstance(name, str) or not name.strip() or name in group_names:
            problems.append(f"{label} has an invalid or duplicate name")
        else:
            group_names.add(name)
            label = name
        items = group.get("items")
        if not isinstance(items, list) or not items:
            problems.append(f"{label}: items must be a non-empty list")
            continue
        declared_group_bytes = group.get("bytes")
        item_total = 0
        for item_index, item in enumerate(items):
            item_label = f"{label} item {item_index}"
            if not isinstance(item, dict):
                problems.append(f"{item_label} must be an object")
                continue
            expected_bytes = item.get("bytes")
            if not isinstance(expected_bytes, int) or expected_bytes < 0:
                problems.append(f"{item_label}: bytes must be a non-negative integer")
                continue
            expected_total += expected_bytes
            item_total += expected_bytes
            path_text = item.get("path")
            if not isinstance(path_text, str) or not path_text:
                problems.append(f"{item_label}: path must be non-empty text")
                continue
            source = Path(path_text)
            if not source.is_absolute():
                problems.append(f"{item_label}: path must be absolute")
                continue
            try:
                if root is None:
                    raise ValueError("declared root is invalid")
                resolved = _resolve_target(source, root)
            except (OSError, ValueError) as error:
                problems.append(f"{item_label}: unavailable target: {error}")
                continue
            key = str(resolved).casefold()
            if key in seen_paths:
                problems.append(f"{item_label}: duplicate target")
                continue
            overlap = None
            for existing in resolved_paths:
                try:
                    resolved.relative_to(existing)
                    overlap = f"nested inside {existing}"
                    break
                except ValueError:
                    pass
                try:
                    existing.relative_to(resolved)
                    overlap = f"contains prior target {existing}"
                    break
                except ValueError:
                    pass
            if overlap:
                problems.append(f"{item_label}: overlapping target ({overlap})")
                continue
            seen_paths.add(key)
            resolved_paths.append(resolved)

            kind = item.get("type")
            observed = {"path": str(resolved), "type": kind}
            if kind == "file":
                if not resolved.is_file():
                    problems.append(f"{item_label}: target is not a regular file")
                    continue
                size = resolved.stat().st_size
                identity = sha256_file(resolved)
                observed.update({"bytes": size, "sha256": identity})
                observed_total += size
                if size != expected_bytes:
                    problems.append(f"{item_label}: byte length changed")
                expected_hash = item.get("sha256")
                if not isinstance(expected_hash, str) or identity != expected_hash.casefold():
                    problems.append(f"{item_label}: SHA-256 changed or is invalid")
            elif kind == "directory_tree":
                if not resolved.is_dir():
                    problems.append(f"{item_label}: target is not a directory")
                    continue
                def report_tree(state, target=str(resolved)):
                    if progress:
                        progress({"target": target, **state})

                state = tree_state(
                    resolved, progress=report_tree,
                    progress_every=progress_every,
                    hash_workers=hash_workers)
                observed.update(state)
                observed_total += state["bytes"]
                if state["reparse_boundaries"]:
                    problems.append(f"{item_label}: tree contains reparse boundaries")
                for field in ("bytes", "file_count", "directory_count",
                              "tree_sha256"):
                    expected = item.get(field)
                    actual = state[field]
                    if field.endswith("sha256") and isinstance(expected, str):
                        expected = expected.casefold()
                    if expected != actual:
                        problems.append(f"{item_label}: {field} changed or is invalid")
            else:
                problems.append(f"{item_label}: unsupported type {kind!r}")
                continue
            targets.append(observed)
        if declared_group_bytes != item_total:
            problems.append(f"{label}: declared group bytes do not equal item bytes")

    if payload.get("total_bytes") != expected_total:
        problems.append("declared total_bytes does not equal item bytes")
    return {
        "status": "ready" if not problems else "stale-or-invalid",
        "manifest": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "root": str(root) if root else None,
        "expected_total_bytes": expected_total,
        "observed_total_bytes": observed_total,
        "targets_observed": len(targets),
        "problems": problems,
        "targets": targets,
        "limitations": [
            "Read-only validation does not authorize or perform deletion.",
            "The mechanism proves target identity, not semantic redundancy or survivor adequacy.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--json", action="store_true",
                        help="emit the stable JSON record (currently the only format)")
    parser.add_argument(
        "--progress", action="store_true",
        help="emit JSON scan/hash progress records to stderr")
    parser.add_argument(
        "--progress-every", type=int, default=10_000,
        help="progress interval in observed entries/files (default: 10000)")
    parser.add_argument(
        "--hash-workers", type=int, default=8,
        help="bounded parallel file-hash workers (default: 8)")
    args = parser.parse_args(argv)
    if args.progress_every <= 0:
        parser.error("--progress-every must be positive")
    if args.hash_workers <= 0:
        parser.error("--hash-workers must be positive")

    def emit_progress(record):
        print(json.dumps(record, ensure_ascii=False), file=sys.stderr,
              flush=True)

    try:
        result = validate_manifest(
            args.manifest,
            progress=emit_progress if args.progress else None,
            progress_every=args.progress_every,
            hash_workers=args.hash_workers)
    except OSError as error:
        result = {"status": "unavailable", "problems": [str(error)]}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
