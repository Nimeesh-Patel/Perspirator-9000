#!/usr/bin/env python3
"""Read-only structural facts for a directory refactor.

The tool deliberately does not decide whether a file is useful, superseded,
or safe to delete.  It exposes repeatable facts that can criticise those
semantic conjectures: directory shape, byte-identical files, and verified
ZIP/extracted-directory relations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import zipfile
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileRecord:
    path: Path
    relative: str
    size: int
    mtime_ns: int
    top_level: str


def _is_reparse(path: Path) -> bool:
    """Return whether *path* is a link/reparse boundary without following it."""
    info = path.lstat()
    attrs = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attrs & reparse)


def scan_tree(root: Path, *, progress=None,
              progress_every: int = 10_000) -> tuple[list[FileRecord], list[str], int]:
    """Scan regular files below *root* without traversing reparse points."""
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")

    files: list[FileRecord] = []
    boundaries: list[str] = []
    directory_count = 0
    scanned_bytes = 0
    work_units = 0
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = os.scandir(current)
        except OSError as error:
            relative = ("." if current == root
                        else current.relative_to(root).as_posix())
            boundaries.append(
                f"{relative} [unavailable: {error.__class__.__name__}: {error}]")
            continue
        with entries:
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                try:
                    if _is_reparse(path):
                        boundaries.append(relative)
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        directory_count += 1
                        pending.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        info = entry.stat(follow_symlinks=False)
                        scanned_bytes += info.st_size
                        files.append(FileRecord(
                            path=path,
                            relative=relative,
                            size=info.st_size,
                            mtime_ns=info.st_mtime_ns,
                            top_level=Path(relative).parts[0],
                        ))
                except OSError as error:
                    # A concurrently removed or inaccessible entry is an
                    # explicit boundary rather than a fabricated empty record.
                    boundaries.append(
                        f"{relative} [unavailable: {error.__class__.__name__}: {error}]")
                work_units += 1
                if progress and work_units % progress_every == 0:
                    progress({
                        "files": len(files),
                        "directories": directory_count,
                        "bytes": scanned_bytes,
                        "boundaries": len(boundaries),
                        "pending_directories": len(pending),
                    })
    files.sort(key=lambda item: item.relative.casefold())
    boundaries.sort(key=str.casefold)
    return files, boundaries, directory_count


def top_level_census(root: Path, *, progress=None,
                     progress_every: int = 10_000) -> dict:
    """Census independently reportable top-level partitions of a large root."""
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    partitions = []
    global_boundaries = []
    for child in sorted(root.iterdir(), key=lambda path: path.name.casefold()):
        record = {"name": child.name, "path": str(child)}
        try:
            if _is_reparse(child):
                record.update({
                    "kind": "reparse-boundary", "status": "boundary",
                    "files": 0, "directories": 0, "bytes": 0,
                    "reparse_boundaries": [child.name],
                })
                global_boundaries.append(child.name)
            elif child.is_file():
                record.update({
                    "kind": "file", "status": "complete", "files": 1,
                    "directories": 0, "bytes": child.stat().st_size,
                    "reparse_boundaries": [],
                })
            elif child.is_dir():
                def report(state, partition=child.name):
                    if progress:
                        progress({"event": "partition-progress",
                                  "partition": partition, **state})

                files, boundaries, directories = scan_tree(
                    child, progress=report, progress_every=progress_every)
                prefixed = [f"{child.name}/{boundary}" for boundary in boundaries]
                global_boundaries.extend(prefixed)
                record.update({
                    "kind": "directory",
                    "status": ("partial" if any("[unavailable:" in item
                                                for item in boundaries)
                               else "complete"),
                    "files": len(files),
                    "directories": directories + 1,
                    "bytes": sum(item.size for item in files),
                    "reparse_boundaries": boundaries,
                })
            else:
                record.update({
                    "kind": "other", "status": "boundary", "files": 0,
                    "directories": 0, "bytes": 0,
                    "reparse_boundaries": [child.name],
                })
                global_boundaries.append(child.name)
        except OSError as error:
            record.update({
                "kind": "unavailable", "status": "unavailable", "files": 0,
                "directories": 0, "bytes": 0,
                "reparse_boundaries": [], "error": str(error),
            })
        partitions.append(record)
        if progress:
            progress({"event": "partition-complete",
                      "partition": child.name, **record})

    incomplete = [item for item in partitions
                  if item["status"] in {"partial", "unavailable"}]
    ranked = sorted(partitions, key=lambda item: (-item["bytes"],
                                                  item["name"].casefold()))
    return {
        "schema_version": 1,
        "mode": "top-level-census",
        "status": "partial" if incomplete else "complete",
        "root": str(root),
        "scope": "read-only; top-level partitions are independent and reparse points are not traversed",
        "files": sum(item["files"] for item in partitions),
        "directories": sum(item["directories"] for item in partitions),
        "bytes": sum(item["bytes"] for item in partitions),
        "partitions": ranked,
        "reparse_boundaries": sorted(global_boundaries, key=str.casefold),
        "incomplete_partitions": [item["name"] for item in incomplete],
        "limitations": [
            "A completed partition establishes structural size, not that its contents are dispensable.",
            "Partial partitions preserve observed counts but may omit inaccessible state.",
            "Dependency cache, project requirement, installed runtime, and unique data remain distinct explanatory roles.",
        ],
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def exact_duplicate_groups(files: list[FileRecord]) -> list[dict]:
    """Hash only same-size candidates and return byte-identity groups."""
    by_size: dict[int, list[FileRecord]] = defaultdict(list)
    for item in files:
        if item.size:
            by_size[item.size].append(item)

    by_identity: dict[tuple[int, str], list[FileRecord]] = defaultdict(list)
    for size, candidates in by_size.items():
        if len(candidates) < 2:
            continue
        for item in candidates:
            by_identity[(size, sha256_file(item.path))].append(item)

    groups = []
    for (size, digest), copies in by_identity.items():
        if len(copies) < 2:
            continue
        groups.append({
            "sha256": digest,
            "bytes_per_copy": size,
            "copies": len(copies),
            "bytes_beyond_one_copy": size * (len(copies) - 1),
            "paths": [item.relative for item in copies],
        })
    groups.sort(key=lambda item: (-item["bytes_beyond_one_copy"], item["paths"]))
    return groups


def _normal_zip_entries(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    return [entry for entry in archive.infolist() if not entry.is_dir()]


def _zip_maps(entries: list[zipfile.ZipInfo]) -> list[tuple[str, dict[str, zipfile.ZipInfo]]]:
    direct = {entry.filename.replace("\\", "/").lstrip("/").casefold(): entry
              for entry in entries}
    variants = [("direct", direct)]
    parts = [entry.filename.replace("\\", "/").lstrip("/").split("/", 1)
             for entry in entries]
    roots = {item[0].casefold() for item in parts}
    if len(roots) == 1 and parts and all(len(item) == 2 for item in parts):
        stripped = {item[1].casefold(): entry for item, entry in zip(parts, entries)}
        variants.append(("strip-common-root", stripped))
    return variants


def _crc32(path: Path) -> int:
    checksum = 0
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            checksum = zlib.crc32(block, checksum)
    return checksum & 0xFFFFFFFF


def compare_zip_folder(zip_path: Path, folder_path: Path, *, verify_crc: bool) -> dict:
    """Compare a ZIP manifest with a candidate extracted directory."""
    folder_files, boundaries, directory_count = scan_tree(folder_path)
    folder_map = {item.relative.casefold(): item for item in folder_files}
    with zipfile.ZipFile(zip_path) as archive:
        entries = _normal_zip_entries(archive)
        variants = _zip_maps(entries)
        mapping_name, entry_map = max(
            variants,
            key=lambda variant: sum(key in folder_map for key in variant[1]),
        )

        crc_matches = 0
        crc_mismatches = 0
        size_mismatches = 0
        missing = 0
        path_matches = 0
        for relative, entry in entry_map.items():
            item = folder_map.get(relative)
            if item is None:
                missing += 1
                continue
            path_matches += 1
            if item.size != entry.file_size:
                size_mismatches += 1
            elif verify_crc:
                if _crc32(item.path) == entry.CRC:
                    crc_matches += 1
                else:
                    crc_mismatches += 1

    extra = len(folder_files) - path_matches
    manifest_identical = not (missing or extra or size_mismatches)
    content_identical = manifest_identical and (
        not verify_crc or (crc_matches == len(entries) and not crc_mismatches)
    )
    return {
        "zip": str(zip_path),
        "folder": str(folder_path),
        "mapping": mapping_name,
        "verification": "crc32" if verify_crc else "path-and-size",
        "status": "identical-representation" if content_identical else "different",
        "zip_entries": len(entries),
        "folder_files": len(folder_files),
        "path_matches": path_matches,
        "crc_matches": crc_matches if verify_crc else None,
        "crc_mismatches": crc_mismatches if verify_crc else None,
        "size_mismatches": size_mismatches,
        "zip_missing_from_folder": missing,
        "folder_extra": extra,
        "zip_bytes": zip_path.stat().st_size,
        "folder_bytes": sum(item.size for item in folder_files),
        "folder_directories": directory_count,
        "folder_reparse_boundaries": boundaries,
    }


def _git(repo: Path, *arguments: str, text: bool = True):
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=text,
    ).stdout


def _git_head_tree(repo: Path) -> tuple[str, str, dict[str, str]]:
    head = _git(repo, "rev-parse", "HEAD").strip()
    object_format = _git(repo, "rev-parse", "--show-object-format").strip()
    raw = _git(repo, "ls-tree", "-r", "-z", "HEAD", text=False)
    tree = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, encoded_path = record.split(b"\t", 1)
        _mode, kind, object_id = metadata.decode("ascii").split()
        if kind == "blob":
            tree[encoded_path.decode("utf-8", "surrogateescape")] = object_id
    return head, object_format, tree


def _git_blob_id(data: bytes, object_format: str) -> str:
    digest = hashlib.new(object_format)
    digest.update(f"blob {len(data)}\0".encode("ascii"))
    digest.update(data)
    return digest.hexdigest()


def compare_zip_git_head(zip_path: Path, repo_path: Path) -> dict:
    """Compare ZIP entry bytes with the tracked blobs at a repository HEAD."""
    repo_path = Path(_git(repo_path, "rev-parse", "--show-toplevel").strip())
    head, object_format, tree = _git_head_tree(repo_path)
    dirty_paths = [line for line in _git(repo_path, "status", "--porcelain").splitlines()
                   if line]
    with zipfile.ZipFile(zip_path) as archive:
        entries = _normal_zip_entries(archive)
        variants = _zip_maps(entries)
        mapping_name, entry_map = max(
            variants,
            key=lambda variant: sum(key in {name.casefold() for name in tree}
                                    for key in variant[1]),
        )
        head_by_case = {name.casefold(): (name, object_id)
                        for name, object_id in tree.items()}
        same_paths = []
        changed_paths = []
        missing_paths = []
        archive_paths = set()
        for relative, entry in entry_map.items():
            archive_paths.add(relative)
            head_item = head_by_case.get(relative)
            if head_item is None:
                missing_paths.append(relative)
                continue
            head_name, expected_id = head_item
            if _git_blob_id(archive.read(entry), object_format) == expected_id:
                same_paths.append(head_name)
            else:
                changed_paths.append(head_name)
    extra_paths = sorted(name for name in tree if name.casefold() not in archive_paths)
    if not changed_paths and not missing_paths and not extra_paths:
        status = "identical-head"
    elif not changed_paths and not missing_paths:
        status = "head-contains-archive-with-extras"
    else:
        status = "divergent"
    return {
        "zip": str(zip_path),
        "repository": str(repo_path),
        "head": head,
        "object_format": object_format,
        "mapping": mapping_name,
        "status": status,
        "zip_entries": len(entries),
        "head_blobs": len(tree),
        "same_as_head": len(same_paths),
        "changed_from_head": len(changed_paths),
        "missing_from_head": len(missing_paths),
        "head_extra": len(extra_paths),
        "changed_paths": sorted(changed_paths),
        "missing_paths": sorted(missing_paths),
        "head_extra_paths": extra_paths,
        "working_tree_dirty": bool(dirty_paths),
        "working_tree_status": dirty_paths,
        "zip_bytes": zip_path.stat().st_size,
    }


def discover_archive_pairs(root: Path) -> list[tuple[Path, Path, str]]:
    """Nominate unambiguous sibling ZIP/directory name relations."""
    pairs = []
    directories_by_parent: dict[Path, list[Path]] = defaultdict(list)
    for current, names, _ in os.walk(root, followlinks=False):
        parent = Path(current)
        names[:] = [name for name in names if not _is_reparse(parent / name)]
        directories_by_parent[parent] = [parent / name for name in names]

    for current, names, filenames in os.walk(root, followlinks=False):
        parent = Path(current)
        names[:] = [name for name in names if not _is_reparse(parent / name)]
        directories = directories_by_parent[parent]
        for filename in filenames:
            zip_path = parent / filename
            if zip_path.suffix.casefold() != ".zip":
                continue
            stem = zip_path.stem.casefold()
            ranked = []
            for directory in directories:
                name = directory.name.casefold()
                if name == stem:
                    ranked.append((3, directory, "same-stem"))
                elif stem.startswith(name) and stem[len(name):len(name) + 1] in "-_. [":
                    ranked.append((2, directory, "directory-name-prefix"))
                elif name.startswith(stem) and name[len(stem):len(stem) + 1] in "-_. [":
                    ranked.append((1, directory, "zip-stem-prefix"))
            if not ranked:
                continue
            ranked.sort(key=lambda item: (-item[0], item[1].name.casefold()))
            best_score = ranked[0][0]
            best = [item for item in ranked if item[0] == best_score]
            if len(best) == 1:
                _, directory, rule = best[0]
                pairs.append((zip_path, directory, rule))
    pairs.sort(key=lambda item: item[0].as_posix().casefold())
    return pairs


def audit(root: Path, *, hash_duplicates: bool, verify_archives: bool,
          explicit_archive_pairs: list[tuple[Path, Path]] | None = None,
          explicit_git_pairs: list[tuple[Path, Path]] | None = None) -> dict:
    root = root.resolve(strict=True)
    files, boundaries, directory_count = scan_tree(root)
    extension_counts = Counter((item.path.suffix.casefold() or "[none]") for item in files)
    extension_bytes = Counter()
    for item in files:
        extension_bytes[item.path.suffix.casefold() or "[none]"] += item.size

    top_level_files = [item for item in files if "/" not in item.relative]
    top_level_directories = [path for path in root.iterdir()
                             if path.is_dir() and not _is_reparse(path)]
    result = {
        "schema_version": 1,
        "root": str(root),
        "scope": "read-only; reparse points are reported and not traversed",
        "files": len(files),
        "directories": directory_count,
        "bytes": sum(item.size for item in files),
        "top_level_files": len(top_level_files),
        "top_level_directories": len(top_level_directories),
        "reparse_boundaries": boundaries,
        "extensions": [
            {"extension": extension, "files": extension_counts[extension],
             "bytes": extension_bytes[extension]}
            for extension in sorted(extension_counts,
                                    key=lambda value: (-extension_bytes[value], value))
        ],
        "exact_duplicates": exact_duplicate_groups(files) if hash_duplicates else None,
        "archive_pairs": [],
        "git_pairs": [],
        "limitations": [
            "Byte identity does not establish that one path is dispensable.",
            "Archive discovery is a filename-based nomination among sibling paths.",
            "Usefulness, supersession, canonical identity, and retention remain explanatory judgments.",
            "Git comparison is against committed HEAD; working-tree changes are reported separately.",
        ],
    }
    if verify_archives:
        nominated = discover_archive_pairs(root)
        for zip_path, folder_path in explicit_archive_pairs or []:
            zip_path = zip_path if zip_path.is_absolute() else root / zip_path
            folder_path = folder_path if folder_path.is_absolute() else root / folder_path
            nominated.append((zip_path.resolve(strict=True),
                              folder_path.resolve(strict=True), "explicit"))
        unique = {}
        for zip_path, folder_path, rule in nominated:
            unique[(zip_path.resolve(), folder_path.resolve())] = rule
        for (zip_path, folder_path), rule in sorted(
                unique.items(), key=lambda item: str(item[0][0]).casefold()):
            comparison = compare_zip_folder(zip_path, folder_path, verify_crc=True)
            comparison["discovery_rule"] = rule
            result["archive_pairs"].append(comparison)
    for zip_path, repo_path in explicit_git_pairs or []:
        zip_path = zip_path if zip_path.is_absolute() else root / zip_path
        repo_path = repo_path if repo_path.is_absolute() else root / repo_path
        result["git_pairs"].append(compare_zip_git_head(
            zip_path.resolve(strict=True), repo_path.resolve(strict=True)))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="directory to inspect read-only")
    parser.add_argument("--hash-duplicates", action="store_true",
                        help="SHA-256 same-size candidates and report exact groups")
    parser.add_argument("--verify-archives", action="store_true",
                        help="CRC-check unambiguous sibling ZIP/directory candidates")
    parser.add_argument("--archive-pair", action="append", nargs=2,
                        metavar=("ZIP", "FOLDER"), default=[],
                        help="explicitly nominate a ZIP and extracted folder; repeatable")
    parser.add_argument("--git-pair", action="append", nargs=2,
                        metavar=("ZIP", "REPOSITORY"), default=[],
                        help="compare ZIP blobs with a Git repository HEAD; repeatable")
    parser.add_argument("--top-level-census", action="store_true",
                        help="scan large roots as independently reported top-level partitions")
    parser.add_argument("--progress", action="store_true",
                        help="with --top-level-census, emit JSON progress records to stderr")
    parser.add_argument("--json", action="store_true",
                        help="emit the stable JSON record (currently the only format)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.top_level_census:
            incompatible = (args.hash_duplicates or args.verify_archives
                            or args.archive_pair or args.git_pair)
            if incompatible:
                raise ValueError(
                    "--top-level-census is a structural pass; run hashing and pair verification on nominated partitions")

            def emit_progress(record):
                print(json.dumps(record, ensure_ascii=False),
                      file=sys.stderr, flush=True)

            result = top_level_census(
                args.root, progress=(emit_progress if args.progress else None))
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        pairs = [(Path(zip_path), Path(folder_path))
                 for zip_path, folder_path in args.archive_pair]
        git_pairs = [(Path(zip_path), Path(repo_path))
                     for zip_path, repo_path in args.git_pair]
        result = audit(args.root, hash_duplicates=args.hash_duplicates,
                       verify_archives=args.verify_archives or bool(pairs),
                       explicit_archive_pairs=pairs,
                       explicit_git_pairs=git_pairs)
    except (OSError, ValueError, zipfile.BadZipFile,
            subprocess.CalledProcessError) as error:
        print(json.dumps({"status": "unavailable", "error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
