#!/usr/bin/env python3
"""Validate the canonical runtime, structural toolkit, and agent adapters."""

import argparse
import sys
from pathlib import Path

from adapters import (ADAPTERS, FIRST_CLASS, SHARED_FILES, TARGETS,
                      adapter_directories, add_adapter_directory_arguments,
                      add_vault_argument, absolute, parse_target, render, selected_keys)

RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append(bool(ok))
    mark = "ok  " if ok else "FAIL"
    suffix = f" - {detail}" if detail else ""
    print(f"  [{mark}] {label}{suffix}")
    return bool(ok)


sys.path.insert(0, str(Path(__file__).resolve().parent))
from note_chunks import frontmatter_fields, read_note  # noqa: E402,F401
from policy_index import active_policy_surface  # noqa: E402
from artifact_lifecycle import lifecycle_problems, load_lifecycle  # noqa: E402
from contract_copy import validate_contract_copies  # noqa: E402
from installation import manifest_problems  # noqa: E402


def check_active_note(label, path):
    """The note exists and its frontmatter says status: active."""
    if not check(f"{label} exists", path.is_file(), str(path)):
        check(f"{label} status is active", False, "note missing")
        return
    try:
        text = read_note(path)
    except ValueError as exc:
        check(f"{label} status is active", False, str(exc))
        return
    if text is None:
        check(f"{label} status is active", False, "note unreadable")
        return
    fields = frontmatter_fields(text)
    check(f"{label} status is active", fields.get("status") == "active",
          f"status: {fields.get('status')!r}")


def validate_vault(vault):
    base = vault / "memory" / "perspirator"
    print("\nCanonical runtime")
    check_active_note("runtime", base / "Perspirator.md")
    check_active_note("bootstrap note", base / "Bootstrap.md")
    check_active_note("policy loader note",
                      vault / "memory" / "policies" / "Policy Loader.md")
    check_active_note("bot grants configuration",
                      vault / "memory" / "policies" / "Bot grants.md")
    check_active_note("candidate selection note", base / "Candidate Selection.md")
    check_active_note("neighbour retrieval note", base / "Neighbour Retrieval.md")
    check_active_note("error correction role note", base / "Error Correction role.md")
    try:
        policies = active_policy_surface(vault)
        check("active policy surface is well formed", True,
              f"{len(policies)} policies")
    except ValueError as exc:
        check("active policy surface is well formed", False, str(exc))

    try:
        lifecycle = load_lifecycle(vault)
        problems = lifecycle_problems(vault, lifecycle)
        check("artifact lifecycle declaration is well formed", True,
              f"{len(lifecycle)} roles")
        check("declared artifact lifecycles have no mechanical violation",
              not problems, "; ".join(problems[:4]))
    except ValueError as exc:
        check("artifact lifecycle declaration is well formed", False, str(exc))


NOT_SHIPPED = {"install.py"}


def validate_shared_files(source_dir):
    print("\nShared structural toolkit")
    for name in SHARED_FILES:
        check(f"shared source exists: {name}", (source_dir / name).is_file(),
              str(source_dir / name))

    # Check the required toolkit inventory against the source directory.
    present = {path.name for path in source_dir.glob("*.py")
               if not path.name.startswith("test_")} - NOT_SHIPPED
    listed_scripts = {name for name in SHARED_FILES if "/" not in name
                      and name.endswith(".py")}
    unlisted = sorted(present - listed_scripts)
    check("every source script is in the toolkit inventory", not unlisted,
          ", ".join(unlisted))
    copy_problems = validate_contract_copies(source_dir)
    check("generated contract fixtures match their canonical source",
          not copy_problems, "; ".join(copy_problems))


def validate_adapter(key, directory, expected):
    print(f"\n{ADAPTERS[key]['label']} adapter")
    filename = ADAPTERS[key]["filename"]
    adapter = directory / filename
    if not check("adapter exists", adapter.is_file(), str(adapter)):
        return
    text = adapter.read_text(encoding="utf-8-sig", errors="replace")
    check("adapter points to the canonical toolkit and vault", text == expected)
    ownership_problems = manifest_problems(directory, [filename])
    check("generated-file ownership is current", not ownership_problems,
          "; ".join(ownership_problems))


def target_type(value):
    try:
        return parse_target(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    add_vault_argument(parser)
    parser.add_argument("--target", type=target_type, default="Auto",
                        help=f"Auto or one of {', '.join(TARGETS)}")
    add_adapter_directory_arguments(parser)
    parser.add_argument("--source", help="canonical SKILL.md template")
    return parser.parse_args()


def main():
    args = arguments()
    vault = absolute(args.vault)
    here = Path(__file__).resolve().parent

    directories = adapter_directories(args)

    candidate = absolute(args.source) if args.source else here / "SKILL.md"
    template = None
    if candidate.is_file():
        candidate_text = candidate.read_text(encoding="utf-8-sig", errors="replace")
        if "{{VAULT_PATH}}" in candidate_text and "{{TOOLS_DIR}}" in candidate_text:
            template = candidate_text
    if template is None:
        print(f"Canonical template unavailable: {candidate} must contain both path placeholders")
        return 1
    source_dir = candidate.parent

    if args.target == "Auto":
        selected = [key for key in FIRST_CLASS
                    if (directories[key] / ADAPTERS[key]["filename"]).is_file()]
    else:
        selected = selected_keys(args.target)

    print(f"Perspirator doctor - target: {args.target}")
    print(f"Vault: {vault}")
    print(f"Shared toolkit: {source_dir}")
    if not selected:
        check("an installed adapter was discovered", False,
              "use --target or install an adapter")
    if "Custom" in selected and "Custom" not in directories:
        check("custom directory supplied", False, "use --custom-dir")
        selected = []

    validate_vault(vault)
    validate_shared_files(source_dir)

    expected = render(template, vault, source_dir)
    for key in selected:
        validate_adapter(key, directories[key], expected)

    print()
    if RESULTS and all(RESULTS):
        print(f"all {len(RESULTS)} checks passed")
        return 0
    print(f"{RESULTS.count(False)} of {len(RESULTS)} checks FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
