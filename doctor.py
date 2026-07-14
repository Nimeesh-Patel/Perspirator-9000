#!/usr/bin/env python3
"""Validate the canonical runtime, structural toolkit, and agent adapters."""

import argparse
import sys
import tempfile
from pathlib import Path

RESULTS = []
SCRIPT_NAMES = ("problem_half.py", "problem_index.py", "doctor.py")
REQUIRED_BOOTSTRAP_TEXT = (
    "## 1. Locate the canonical runtime",
    "## 2. Load it or refuse",
    "If the runtime cannot be found or read, STOP.",
    "## 3. Structural tools",
    "## 4. Run report",
    "## 5. Authority rule",
)
POLICY_HEADINGS = (
    "## Theory",
    "## Task",
    "## Method",
    "## Capabilities, writes, and persistence",
)


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


def absolute(path):
    return Path(path).expanduser().resolve(strict=False)


def slash(path):
    return absolute(path).as_posix()


def render(template, vault, tools_dir):
    return (template.replace("{{VAULT_PATH}}", slash(vault))
            .replace("{{TOOLS_DIR}}", slash(tools_dir)))


def normalized_adapter(text, vault, tools_dir):
    return (text.replace("\\", "/")
            .replace(slash(vault), "{{VAULT_PATH}}")
            .replace(slash(tools_dir), "{{TOOLS_DIR}}"))


def validate_runtime(vault):
    base = vault / "memory" / "perspirator"
    runtime = base / "Perspirator.md"
    print("\nCanonical runtime")
    if check("runtime exists", runtime.is_file(), str(runtime)):
        text = runtime.read_text(encoding="utf-8-sig", errors="replace")
        fields = frontmatter_fields(text)
        check("runtime status is active", fields.get("status") == "active",
              f"status: {fields.get('status')!r}")
    else:
        check("runtime status is active", False, "runtime missing")

    for name in ("proposals", "cases", "runs"):
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
    return runtime


def validate_shared_scripts(source_dir):
    print("\nShared structural toolkit")
    for name in SCRIPT_NAMES:
        check(f"source script exists: {name}", (source_dir / name).is_file(),
              str(source_dir / name))


def validate_adapter(name, adapter, tools_dir, vault, template, source_dir):
    print(f"\n{name} adapter")
    if not check("adapter exists", adapter.is_file(), str(adapter)):
        return None

    text = adapter.read_text(encoding="utf-8-sig", errors="replace")
    canonical = f"{slash(vault)}/memory/perspirator/Perspirator.md"
    normalized_text = text.replace("\\", "/")
    check("points at canonical runtime", canonical.lower() in normalized_text.lower(),
          canonical)
    unresolved = any(marker in text for marker in
                     ("{{VAULT_PATH}}", "{{TOOLS_DIR}}", "{{COMMANDS_DIR}}"))
    check("contains no unresolved path placeholders", not unresolved)
    check("points at its structural toolkit",
          slash(tools_dir).lower() in normalized_text.lower(), slash(tools_dir))
    check("contains the complete bootstrap contract",
          all(item in text for item in REQUIRED_BOOTSTRAP_TEXT))
    check("contains no semantic runtime policy headings",
          not any(heading in text for heading in POLICY_HEADINGS))

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


def parse_target(value):
    aliases = {
        "auto": "Auto", "claude": "ClaudeCode",
        "claudecode": "ClaudeCode", "codex": "Codex",
        "all": "All", "custom": "Custom",
    }
    try:
        return aliases[value.lower()]
    except KeyError as exc:
        raise argparse.ArgumentTypeError(f"unknown target: {value}") from exc


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vault_positional", nargs="?",
                        help="legacy positional vault root")
    parser.add_argument("--vault", help="Obsidian vault root")
    parser.add_argument("--target", type=parse_target, default="Auto",
                        help="Auto, ClaudeCode, Codex, All, or Custom")
    parser.add_argument("--claude-dir",
                        default=str(Path.home() / ".claude" / "commands"))
    parser.add_argument("--codex-dir",
                        default=str(Path.home() / ".agents" / "skills" / "perspirate"))
    parser.add_argument("--custom-dir")
    parser.add_argument("--source", help="canonical SKILL.md template")
    return parser.parse_args()


def main():
    args = arguments()
    if args.vault and args.vault_positional:
        raise SystemExit("use either positional vault or --vault, not both")
    vault = absolute(args.vault or args.vault_positional or
                     (Path.home() / "nimeesh vault"))
    claude_dir = absolute(args.claude_dir)
    codex_dir = absolute(args.codex_dir)
    custom_dir = absolute(args.custom_dir) if args.custom_dir else None
    here = Path(__file__).resolve().parent

    candidate = absolute(args.source) if args.source else here / "SKILL.md"
    template = None
    if candidate.is_file():
        candidate_text = candidate.read_text(encoding="utf-8-sig", errors="replace")
        if "{{VAULT_PATH}}" in candidate_text and "{{TOOLS_DIR}}" in candidate_text:
            template = candidate_text
    source_dir = candidate.parent if template is not None else here

    adapters = {
        "ClaudeCode": ("Claude Code", claude_dir / "perspirate.md", claude_dir),
        "Codex": ("Codex", codex_dir / "SKILL.md", codex_dir),
    }
    if custom_dir is not None:
        adapters["Custom"] = ("Custom", custom_dir / "SKILL.md", custom_dir)

    if args.target == "All":
        selected = ["ClaudeCode", "Codex"]
    elif args.target == "Auto":
        selected = [key for key, (_, path, _) in adapters.items() if path.is_file()]
    else:
        selected = [args.target]

    print(f"Perspirator doctor - target: {args.target}")
    print(f"Vault: {vault}")
    if not selected:
        check("an installed adapter was discovered", False,
              "use --target or install an adapter")
    if "Custom" in selected and custom_dir is None:
        check("custom directory supplied", False, "use --custom-dir")
        selected = []

    validate_runtime(vault)
    validate_shared_scripts(source_dir)

    rendered = {}
    for key in selected:
        name, path, tools_dir = adapters[key]
        text = validate_adapter(name, path, tools_dir, vault, template, source_dir)
        if text is not None:
            rendered[key] = (text, tools_dir)

    if "ClaudeCode" in rendered and "Codex" in rendered:
        print("\nCross-adapter consistency")
        claude_text, claude_tools = rendered["ClaudeCode"]
        codex_text, codex_tools = rendered["Codex"]
        check("both adapters share one bootstrap semantics",
              normalized_adapter(claude_text, vault, claude_tools) ==
              normalized_adapter(codex_text, vault, codex_tools))
        for script in SCRIPT_NAMES:
            check(f"both adapters share script bytes: {script}",
                  (claude_tools / script).is_file() and
                  (codex_tools / script).is_file() and
                  (claude_tools / script).read_bytes() ==
                  (codex_tools / script).read_bytes())

    print()
    if RESULTS and all(RESULTS):
        print(f"all {len(RESULTS)} checks passed")
        return 0
    print(f"{RESULTS.count(False)} of {len(RESULTS)} checks FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
