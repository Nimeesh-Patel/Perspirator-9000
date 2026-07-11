#!/usr/bin/env python3
"""
doctor.py — validate a Perspirator installation.

Checks the pieces the bootstrap depends on, so a broken setup fails here
and not halfway through a run:

  1. the canonical runtime exists in the vault;
  2. its frontmatter says `status: active`;
  3. it carries a `version:`;
  4. at least one deployed bootstrap points at that canonical path;
  5. the required directories exist (proposals/, cases/, runs/);
  6. the helper scripts are discoverable next to this script;
  7. the runs directory is writable.

Usage:
    python doctor.py                     # vault defaults to ~/nimeesh vault
    python doctor.py "<vault-root>"

Exit code 0 iff every check passes. No third-party dependencies.
"""

import sys
import tempfile
from pathlib import Path

BOOTSTRAP_CANDIDATES = [
    Path.home() / ".claude" / "commands" / "perspirate.md",
    Path.home() / ".agents" / "skills" / "perspirate" / "SKILL.md",
]

results = []


def check(label, ok, detail=""):
    results.append(ok)
    mark = "ok  " if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def frontmatter_fields(text):
    """Tiny YAML-block reader: top-level `key: value` pairs only."""
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    fields = {}
    for line in text[4:end].split("\n"):
        if ":" in line and not line[:1].isspace():
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip().strip("'\"")
    return fields


def main():
    vault = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "nimeesh vault"
    vault = vault.expanduser()
    base = vault / "memory" / "perspirator"
    runtime = base / "Perspirator.md"
    print(f"Perspirator doctor — vault: {vault}")

    # 1-3. Canonical runtime: exists, active, versioned.
    if check("canonical runtime exists", runtime.is_file(), str(runtime)):
        fm = frontmatter_fields(runtime.read_text(encoding="utf-8-sig",
                                                  errors="replace"))
        check("runtime status is active", fm.get("status") == "active",
              f"status: {fm.get('status')!r}")
        check("runtime has a version", bool(fm.get("version")),
              f"version: {fm.get('version')!r}")
    else:
        check("runtime status is active", False, "runtime missing")
        check("runtime has a version", False, "runtime missing")

    # 4. Deployed bootstrap(s) point at the canonical path. The installer
    #    renders paths with forward slashes; compare case-insensitively.
    canonical = (vault.as_posix() + "/memory/perspirator/Perspirator.md").lower()
    found = [p for p in BOOTSTRAP_CANDIDATES if p.is_file()]
    check("a deployed bootstrap exists", bool(found),
          "looked in: " + ", ".join(str(p) for p in BOOTSTRAP_CANDIDATES))
    for p in found:
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        ok = canonical in text.replace("\\", "/").lower()
        ok = ok and "{{VAULT_PATH}}" not in text and "{{COMMANDS_DIR}}" not in text
        check(f"bootstrap points at canonical runtime: {p}", ok,
              "" if ok else "canonical path missing or placeholders unrendered")

    # 5. Required directories.
    for d in ("proposals", "cases", "runs"):
        check(f"directory exists: memory/perspirator/{d}", (base / d).is_dir())

    # 6. Helper scripts next to this script.
    here = Path(__file__).resolve().parent
    for s in ("problem_half.py", "problem_index.py"):
        check(f"helper script discoverable: {s}", (here / s).is_file(),
              str(here / s))

    # 7. Runs directory writable.
    runs = base / "runs"
    writable = False
    if runs.is_dir():
        try:
            with tempfile.NamedTemporaryFile(dir=runs, prefix=".doctor-",
                                             suffix=".tmp", delete=True):
                writable = True
        except OSError as e:
            check("runs directory is writable", False, str(e))
    if runs.is_dir() and writable:
        check("runs directory is writable", True)
    elif not runs.is_dir():
        check("runs directory is writable", False, "runs/ missing")

    print()
    if all(results):
        print(f"all {len(results)} checks passed")
        sys.exit(0)
    print(f"{results.count(False)} of {len(results)} checks FAILED")
    sys.exit(1)


if __name__ == "__main__":
    main()
