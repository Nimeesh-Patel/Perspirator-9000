"""Native Obsidian link evidence for ranked retrieval and guarded renames.

This is one capability adapter, not retrieval policy. It preserves the exact
vault-relative ``path=`` contract and returns explicit unavailability instead
of silently confusing a closed app or stale CLI index with an empty result.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath


def default_executable():
    """Find the native CLI even when a Windows host inherited an old PATH."""
    on_path = shutil.which("obsidian")
    if on_path:
        return on_path
    if sys.platform == "win32":
        for directory in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
                          Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
                          / "Programs"):
            candidate = directory / "Obsidian" / "Obsidian.com"
            if candidate.is_file():
                return str(candidate)
    return "obsidian"


def exact_path(value):
    """Normalize and validate an exact vault-relative path."""
    raw = str(value).replace("\\", "/")
    path = PurePosixPath(raw)
    if path.is_absolute() or PureWindowsPath(raw).drive or not raw or ".." in path.parts:
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
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        if (len(lines) == 1 and lines[0].lower().startswith("no ")
                and lines[0].lower().endswith(" found.")):
            return []
        return lines


def cli_error(stdout):
    """Recognize diagnostics Obsidian can print even on exit zero."""
    message = stdout.strip()
    lower = message.lower()
    if lower.startswith("the cli is unable to find obsidian"):
        return "unavailable", message
    if lower == "vault not found.":
        return "unavailable", message
    if lower.startswith("error: "):
        return "error", message
    return None


class ObsidianCLI:
    def __init__(self, vault, executable=None, timeout=5, limit=5,
                 runner=subprocess.run):
        self.vault = Path(vault).expanduser().resolve()
        self.vault_name = self.vault.name
        self.executable = default_executable() if executable is None else executable
        self.timeout = timeout
        self.limit = max(1, int(limit))
        self.runner = runner
        self._probe = None
        self._context_cache = {}

    def _run(self, argv):
        try:
            completed = self.runner(
                argv, cwd=str(self.vault), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=self.timeout,
                shell=False)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            return {"ok": False, "status": "unavailable", "argv": argv,
                    "data": [], "error": f"{type(exc).__name__}: {exc}"}
        stdout = completed.stdout or ""
        stderr = (completed.stderr or "").strip()
        diagnostic = cli_error(stdout)
        if diagnostic:
            status, error = diagnostic
            return {"ok": False, "status": status, "argv": argv,
                    "data": [], "error": error}
        if completed.returncode != 0:
            return {"ok": False, "status": "error", "argv": argv,
                    "data": parsed(stdout), "error": stderr or
                    f"exit {completed.returncode}"}
        return {"ok": True, "status": "ok", "argv": argv,
                "data": parsed(stdout), "error": None}

    def probe(self):
        """Ask the running application which vault it has open.

        The answer is state only a running Obsidian holds, so it separates
        "the app is closed" from "that subcommand failed" — the confusion this
        probe exists to prevent. It also proves the CLI is answering about the
        intended vault rather than another open one. Bare ``obsidian`` proves
        neither and costs a several-hundred-line help banner.
        """
        if self._probe is not None:
            return self._probe
        result = self._run([self.executable, f"vault={self.vault_name}",
                            "vault", "info=path"])
        if not result["ok"]:
            return result
        data = result["data"]
        opened = Path(data[0]) if isinstance(data, list) and len(data) == 1 else None
        result["vault_open"] = str(opened) if opened else None
        if opened is None or not opened.is_absolute():
            result.update(ok=False, status="unavailable",
                          error="Obsidian did not report an absolute vault path")
        elif opened.resolve() != self.vault:
            result.update(ok=False, status="wrong-vault",
                          error=f"CLI answers about {opened}, expected {self.vault}")
        else:
            self._probe = result
        return result

    def path_command(self, name, path, **options):
        """Read links or backlinks from the verified vault."""
        if name not in {"links", "backlinks"}:
            raise ValueError(f"not an Obsidian link command: {name}")
        rel = exact_path(path)
        probe = self.probe()
        if not probe["ok"]:
            return probe
        argv = [self.executable, f"vault={self.vault_name}", name, f"path={rel}"]
        for key, value in options.items():
            if value is None or value is False:
                continue
            argv.append(key if value is True else f"{key}={value}")
        return self._run(argv)

    def note_context(self, path):
        """Links and backlinks for an exact note path."""
        rel = exact_path(path)
        if rel in self._context_cache:
            return self._context_cache[rel]
        probe = self.probe()
        if not probe["ok"]:
            return {"provider": "obsidian", "status": probe["status"],
                    "path": rel, "error": probe["error"], "probe": probe}
        calls = {
            "backlinks": self.path_command("backlinks", rel, format="json"),
            "links": self.path_command("links", rel),
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
        result = {
            "provider": "obsidian", "status": "ok", "path": rel,
            "backlinks": list(backlinks)[:self.limit],
            "links": list(links)[:self.limit],
            "coverage": {
                "scope": "received-provider-response", "limit": self.limit,
                "received": {"links": len(links), "backlinks": len(backlinks)},
                "omitted": {"links": max(0, len(links) - self.limit),
                            "backlinks": max(0, len(backlinks) - self.limit)},
            },
        }
        self._context_cache[rel] = result
        return result
