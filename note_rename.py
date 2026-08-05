#!/usr/bin/env python3
"""Guard one Obsidian rename as a complete vault identity transaction."""

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path, PurePosixPath

from note_chunks import DEFAULT_EXCLUDES
from obsidian_cli import ObsidianCLI

WIKILINK = re.compile(re.escape("[[") + r"([^]|#]+)(?:[#|][^]]*)?" + re.escape("]]"))


def relative_note(value):
    raw = str(value).replace("\\", "/").strip()
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"not a vault-relative exact path: {value}")
    if path.suffix and path.suffix.casefold() != ".md":
        raise ValueError(f"not a Markdown note path: {value}")
    return path if path.suffix else path.with_suffix(".md")


def destination(old, name):
    raw = str(name).strip()
    if not raw or "/" in raw or "\\" in raw:
        raise ValueError("new name must be a filename, not a path")
    leaf = relative_note(raw)
    return old.parent / leaf.name


def note_files(vault):
    excluded = {part.casefold() for part in DEFAULT_EXCLUDES}
    return sorted(path for path in vault.rglob("*.md")
                  if not any(part.casefold() in excluded
                             for part in path.relative_to(vault).parts))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backlinks(cli, path):
    result = cli.path_command("backlinks", path, counts=True, format="json")
    if not result["ok"]:
        raise RuntimeError(f"backlinks unavailable: {result['error']}")
    mapping = {}
    for item in result["data"]:
        if isinstance(item, dict):
            mapping[item["file"]] = int(item.get("count", 1))
        else:
            mapping[str(item)] = 1
    return mapping


def stale_links(vault, old, unique=None):
    corpus = note_files(vault)
    if unique is None:
        unique = Counter(path.stem.casefold()
                         for path in corpus)[old.stem.casefold()] == 1
    exact, stem, hits = old.with_suffix("").as_posix().casefold(), old.stem.casefold(), []
    for path in corpus:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        count = 0
        for match in WIKILINK.finditer(text):
            target = match.group(1).strip().replace("\\", "/").removesuffix(".md")
            if target.casefold() == exact or (unique and "/" not in target
                                               and target.casefold() == stem):
                count += 1
        if count:
            hits.append({"path": path.relative_to(vault).as_posix(),
                         "count": count})
    return hits


def build_plan(vault, old, new_name, executable="obsidian", timeout=10,
               runner=subprocess.run):
    vault = Path(vault).expanduser().resolve()
    old = relative_note(old)
    new = destination(old, new_name)
    old_file, new_file = vault / old, vault / new
    if old == new:
        raise ValueError("old and new identities are identical")
    if not old_file.is_file() or new_file.exists():
        raise ValueError("rename requires old present and destination absent")
    cli = ObsidianCLI(vault, executable=executable, timeout=timeout,
                      limit=1000000, runner=runner)
    probe = cli.probe()
    if not probe["ok"]:
        raise RuntimeError(f"Obsidian unavailable: {probe['error']}")
    unique = Counter(path.stem.casefold()
                     for path in note_files(vault))[old.stem.casefold()] == 1
    return {"vault": vault, "old": old.as_posix(), "new": new.as_posix(),
            "new_name": new.stem, "sha256": sha256(old_file),
            "backlinks": backlinks(cli, old.as_posix()),
            "old_links": stale_links(vault, old, unique), "unique": unique,
            "executable": executable, "timeout": timeout, "runner": runner}


def public(plan, status="planned", **extra):
    result = {"status": status, "old": plan["old"], "new": plan["new"],
              "sha256": plan["sha256"], "backlinks": plan["backlinks"],
              "old_links": plan["old_links"],
              "resync_candidates": sorted(
                  set([plan["new"], *plan["backlinks"].keys()]))}
    result.update(extra)
    return result


def apply_plan(plan):
    vault = plan["vault"]
    old, new = PurePosixPath(plan["old"]), PurePosixPath(plan["new"])
    old_file, new_file = vault / old, vault / new
    if (not old_file.is_file() or new_file.exists()
            or sha256(old_file) != plan["sha256"]):
        raise RuntimeError("rename preconditions changed after planning")
    argv = [plan["executable"], f"vault={vault.name}", "rename",
            f"path={plan['old']}", f"name={plan['new_name']}"]
    try:
        completed = plan["runner"](
            argv, cwd=str(vault), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=plan["timeout"],
            shell=False)
    except subprocess.TimeoutExpired:
        return public(plan, "indeterminate", do_not_retry=True,
                      observed={"old_exists": old_file.exists(),
                                "new_exists": new_file.exists()},
                      error="Obsidian rename timed out; inspect state before any action")
    if completed.returncode != 0:
        if new_file.exists() or not old_file.exists():
            return public(plan, "indeterminate", do_not_retry=True,
                          observed={"old_exists": old_file.exists(),
                                    "new_exists": new_file.exists()},
                          error=(completed.stderr or completed.stdout
                                 or f"Obsidian exit {completed.returncode}").strip())
        raise RuntimeError((completed.stderr or completed.stdout
                            or f"Obsidian exit {completed.returncode}").strip())
    failures = []
    if old_file.exists() or not new_file.is_file():
        failures.append("filesystem identity did not move exactly once")
    elif sha256(new_file) != plan["sha256"]:
        failures.append("note content changed during rename")
    remaining = stale_links(vault, old, plan["unique"])
    if remaining:
        failures.append("old wikilink targets remain")
    cli = ObsidianCLI(vault, executable=plan["executable"],
                      timeout=plan["timeout"], limit=1000000,
                      runner=plan["runner"])
    try:
        after = backlinks(cli, new.as_posix()) if new_file.is_file() else {}
    except RuntimeError as exc:
        after = None
        failures.append(str(exc))
    if after != plan["backlinks"]:
        failures.append("backlink identity changed")
    return public(plan, "partial" if failures else "applied",
                  failures=failures, backlinks_after=after,
                  stale_old_links=remaining)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old", help="exact vault-relative old note path")
    parser.add_argument("new_name", help="new filename, with optional .md")
    parser.add_argument("--vault", default=str(Path.home() / "nimeesh vault"))
    parser.add_argument("--executable", default="obsidian")
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--apply", action="store_true",
                        help="invoke Obsidian once; default is inspection only")
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    try:
        plan = build_plan(args.vault, args.old, args.new_name,
                          args.executable, args.timeout)
        result = apply_plan(plan) if args.apply else public(plan)
    except (OSError, RuntimeError, ValueError) as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return {"applied": 0, "planned": 0, "indeterminate": 2}.get(
        result["status"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
