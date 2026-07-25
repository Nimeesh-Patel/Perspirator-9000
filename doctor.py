#!/usr/bin/env python3
"""Validate the canonical runtime, structural toolkit, and agent adapters."""

import argparse
import sys
import tempfile
from pathlib import Path

from adapters import (ADAPTERS, FIRST_CLASS, SCRIPT_NAMES, absolute,
                      parse_target, render, selected_keys, slash)

RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append(bool(ok))
    mark = "ok  " if ok else "FAIL"
    suffix = f" - {detail}" if detail else ""
    print(f"  [{mark}] {label}{suffix}")
    return bool(ok)


def frontmatter_fields(text):
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    fields = {}
    for line in text[4:end].split("\n"):
        if ":" in line and not line[:1].isspace():
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip("'\"")
    return fields


def normalized_adapter(text, vault, tools_dir):
    return (text.replace("\\", "/")
            .replace(slash(vault), "{{VAULT_PATH}}")
            .replace(slash(tools_dir), "{{TOOLS_DIR}}"))


def check_active_note(label, path):
    """The note exists and its frontmatter says status: active."""
    if not check(f"{label} exists", path.is_file(), str(path)):
        check(f"{label} status is active", False, "note missing")
        return
    fields = frontmatter_fields(path.read_text(encoding="utf-8-sig", errors="replace"))
    check(f"{label} status is active", fields.get("status") == "active",
          f"status: {fields.get('status')!r}")


def validate_vault(vault):
    base = vault / "memory" / "perspirator"
    print("\nCanonical runtime")
    check_active_note("runtime", base / "Perspirator.md")
    check_active_note("bootstrap note", base / "Bootstrap.md")
    check_active_note("policy loader note",
                      vault / "memory" / "policies" / "Policy Loader.md")

    for name in ("proposals", "runs"):
        check(f"runtime directory exists: {name}", (base / name).is_dir())

    runs = base / "runs"
    writable = False
    error = "runs directory missing"
    if runs.is_dir():
        try:
            with tempfile.NamedTemporaryFile(
                    dir=runs, prefix=".doctor-", suffix=".tmp", delete=True):
                writable = True
            error = ""
        except OSError as exc:
            error = str(exc)
    check("runs directory is writable", writable, error)


def validate_shared_scripts(source_dir):
    print("\nShared structural toolkit")
    for name in SCRIPT_NAMES:
        check(f"source script exists: {name}", (source_dir / name).is_file(),
              str(source_dir / name))


def validate_adapter(key, adapter, tools_dir, vault, template, source_dir):
    print(f"\n{ADAPTERS[key]['label']} adapter")
    if not check("adapter exists", adapter.is_file(), str(adapter)):
        return None

    text = adapter.read_text(encoding="utf-8-sig", errors="replace")
    normalized_text = text.replace("\\", "/").lower()

    bootstrap = f"{slash(vault)}/memory/perspirator/Bootstrap.md"
    check("names the bootstrap note", bootstrap.lower() in normalized_text, bootstrap)
    unresolved = any(marker in text for marker in ("{{VAULT_PATH}}", "{{TOOLS_DIR}}"))
    check("contains no unresolved path placeholders", not unresolved)
    check("points at its structural toolkit",
          slash(tools_dir).lower() in normalized_text, slash(tools_dir))

    for script in SCRIPT_NAMES:
        installed = tools_dir / script
        check(f"installed script exists: {script}", installed.is_file(),
              str(installed))
        source = source_dir / script
        if installed.is_file() and source.is_file():
            check(f"installed script matches shared source: {script}",
                  installed.read_bytes() == source.read_bytes())

    if template is not None:
        check("render matches canonical bootstrap source exactly",
              text == render(template, vault, tools_dir))
    return text


def target_type(value):
    try:
        return parse_target(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", default=str(Path.home() / "nimeesh vault"),
                        help="Obsidian vault root")
    parser.add_argument("--target", type=target_type, default="Auto",
                        help="Auto, ClaudeCode, Codex, All, or Custom")
    parser.add_argument("--claude-dir", default=str(ADAPTERS["ClaudeCode"]["default_dir"]))
    parser.add_argument("--codex-dir", default=str(ADAPTERS["Codex"]["default_dir"]))
    parser.add_argument("--custom-dir")
    parser.add_argument("--source", help="canonical SKILL.md template")
    return parser.parse_args()


def main():
    args = arguments()
    vault = absolute(args.vault)
    here = Path(__file__).resolve().parent

    directories = {
        "ClaudeCode": absolute(args.claude_dir),
        "Codex": absolute(args.codex_dir),
    }
    if args.custom_dir:
        directories["Custom"] = absolute(args.custom_dir)

    candidate = absolute(args.source) if args.source else here / "SKILL.md"
    template = None
    if candidate.is_file():
        candidate_text = candidate.read_text(encoding="utf-8-sig", errors="replace")
        if "{{VAULT_PATH}}" in candidate_text and "{{TOOLS_DIR}}" in candidate_text:
            template = candidate_text
    source_dir = candidate.parent if template is not None else here

    if args.target == "Auto":
        selected = [key for key in FIRST_CLASS
                    if (directories[key] / ADAPTERS[key]["filename"]).is_file()]
    else:
        selected = selected_keys(args.target)

    print(f"Perspirator doctor - target: {args.target}")
    print(f"Vault: {vault}")
    if not selected:
        check("an installed adapter was discovered", False,
              "use --target or install an adapter")
    if "Custom" in selected and "Custom" not in directories:
        check("custom directory supplied", False, "use --custom-dir")
        selected = []

    validate_vault(vault)
    validate_shared_scripts(source_dir)

    rendered = {}
    for key in selected:
        tools_dir = directories[key]
        adapter = tools_dir / ADAPTERS[key]["filename"]
        text = validate_adapter(key, adapter, tools_dir, vault, template, source_dir)
        if text is not None:
            rendered[key] = (text, tools_dir)

    if len(rendered) > 1:
        print("\nCross-adapter consistency")
        keys = sorted(rendered)
        first_key = keys[0]
        first_text, first_tools = rendered[first_key]
        baseline = normalized_adapter(first_text, vault, first_tools)
        for key in keys[1:]:
            text, tools_dir = rendered[key]
            check(f"{first_key} and {key} share one bootstrap semantics",
                  normalized_adapter(text, vault, tools_dir) == baseline)
        for script in SCRIPT_NAMES:
            paths = [tools / script for _, tools in rendered.values()]
            same = (all(path.is_file() for path in paths) and
                    len({path.read_bytes() for path in paths}) == 1)
            check(f"all adapters share script bytes: {script}", same)

    print()
    if RESULTS and all(RESULTS):
        print(f"all {len(RESULTS)} checks passed")
        return 0
    print(f"{RESULTS.count(False)} of {len(RESULTS)} checks FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
