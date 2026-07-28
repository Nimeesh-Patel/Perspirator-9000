#!/usr/bin/env python3
"""Bounded, read-only Obsidian CLI context for Perspirator mechanisms.

This is one capability adapter, not retrieval policy. It preserves the exact
vault-relative ``path=`` contract and returns explicit unavailability instead
of silently confusing a closed app or stale CLI index with an empty result.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath

READ_ONLY = {
    "aliases", "backlinks", "base:query", "bases", "deadends", "file",
    "files", "folder", "folders", "help", "history", "history:list",
    "history:read", "links", "orphans", "outline", "properties",
    "property:read", "read", "recents", "search", "search:context",
    "sync:history", "sync:read", "sync:status", "tags", "tasks",
    "unresolved", "vault", "vaults", "version", "wordcount",
}


def exact_path(value):
    """Normalize and validate an exact vault-relative path."""
    raw = str(value).replace("\\", "/")
    path = PurePosixPath(raw)
    if path.is_absolute() or not raw or ".." in path.parts:
        raise ValueError(f"not a vault-relative exact path: {value}")
    return path.as_posix()


def parsed(stdout):
    """Parse JSON when offered; otherwise return bounded non-empty lines."""
    value = (stdout or "").strip()
    if not value:
        return []
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return [line.strip() for line in value.splitlines() if line.strip()]


class ObsidianCLI:
    def __init__(self, vault, executable="obsidian", timeout=5, limit=5,
                 runner=subprocess.run):
        self.vault = Path(vault).expanduser().resolve()
        self.vault_name = self.vault.name
        self.executable = executable
        self.timeout = timeout
        self.limit = max(1, int(limit))
        self.runner = runner
        self._probe = None

    def _run(self, argv):
        try:
            completed = self.runner(
                argv, cwd=str(self.vault), capture_output=True, text=True,
                timeout=self.timeout, shell=False)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            return {"ok": False, "status": "unavailable", "argv": argv,
                    "data": [], "error": f"{type(exc).__name__}: {exc}"}
        stdout = completed.stdout or ""
        stderr = (completed.stderr or "").strip()
        if completed.returncode != 0:
            return {"ok": False, "status": "error", "argv": argv,
                    "data": parsed(stdout), "error": stderr or
                    f"exit {completed.returncode}"}
        return {"ok": True, "status": "ok", "argv": argv,
                "data": parsed(stdout), "error": None}

    def probe(self):
        """Bare ``obsidian`` is the handshake with the running application."""
        if self._probe is None:
            self._probe = self._run([self.executable])
        return self._probe

    def command(self, name, **options):
        """Run one whitelisted read command without a shell."""
        if name not in READ_ONLY:
            raise ValueError(f"not a read-only Obsidian command: {name}")
        argv = [self.executable, f"vault={self.vault_name}", name]
        for key, value in options.items():
            if value is None or value is False:
                continue
            argv.append(key if value is True else f"{key}={value}")
        return self._run(argv)

    def path_command(self, name, path, **options):
        return self.command(name, path=exact_path(path), **options)

    def note_context(self, path):
        """Links, backlinks, and properties for an exact note path."""
        rel = exact_path(path)
        probe = self.probe()
        if not probe["ok"]:
            return {"provider": "obsidian", "status": probe["status"],
                    "path": rel, "error": probe["error"], "probe": probe}
        calls = {
            "backlinks": self.path_command("backlinks", rel, format="json"),
            "links": self.path_command("links", rel),
            "properties": self.path_command("properties", rel, format="json"),
        }
        failed = next((result for result in calls.values() if not result["ok"]), None)
        if failed:
            return {"provider": "obsidian", "status": failed["status"],
                    "path": rel, "error": failed["error"], "commands": calls}
        backlinks = calls["backlinks"]["data"]
        if isinstance(backlinks, list):
            backlinks = [item.get("file", item) if isinstance(item, dict) else item
                         for item in backlinks]
        links = calls["links"]["data"]
        return {
            "provider": "obsidian", "status": "ok", "path": rel,
            "backlinks": list(backlinks)[:self.limit],
            "links": list(links)[:self.limit],
            "properties": calls["properties"]["data"],
        }

    def search(self, query, path=None, context=False):
        options = {"query": query, "limit": self.limit, "format": "json"}
        if path:
            options["path"] = exact_path(path)
        return self.command("search:context" if context else "search", **options)

    def base_query(self, path, view=None):
        return self.path_command("base:query", path, view=view, format="json")

    def vault_shape(self):
        """Bounded graph-health evidence, separate from semantic ranking."""
        return {name: self.command(name, total=True)
                for name in ("orphans", "deadends", "unresolved")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", default=str(Path.home() / "nimeesh vault"))
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--limit", type=int, default=5)
    sub = parser.add_subparsers(dest="action", required=True)
    context = sub.add_parser("context")
    context.add_argument("path")
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--path")
    search.add_argument("--context", action="store_true")
    base = sub.add_parser("base-query")
    base.add_argument("path")
    base.add_argument("--view")
    sub.add_parser("vault-shape")
    args = parser.parse_args()

    cli = ObsidianCLI(args.vault, timeout=args.timeout, limit=args.limit)
    if args.action == "context":
        result = cli.note_context(args.path)
    elif args.action == "search":
        result = cli.search(args.query, args.path, args.context)
    elif args.action == "base-query":
        result = cli.base_query(args.path, args.view)
    else:
        result = cli.vault_shape()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())