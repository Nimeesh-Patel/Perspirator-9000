#!/usr/bin/env python3
"""Render agent discovery adapters from the canonical SKILL.md.

One installer for every platform; the adapter table lives in adapters.py.
Examples:
    python install.py --target ClaudeCode --vault "D:\\My Vault"
    python install.py --target All --vault "D:\\My Vault"
    python install.py --target Custom --vault "D:\\My Vault" --destination "D:\\agent prompts"
"""

import argparse
import sys
from pathlib import Path

from adapters import (ADAPTERS, SHARED_FILES, TARGETS, adapter_directories,
                      add_adapter_directory_arguments, add_vault_argument,
                      absolute, parse_target, render,
                      selected_keys)
from installation import (digest, load_manifest, replace_text,
                          retire_stale_owned_files, retirement_plan, write_manifest)


def target_type(value):
    try:
        target = parse_target(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    if target == "Auto":
        raise argparse.ArgumentTypeError("Auto is a doctor target, not an install target")
    return target


def arguments():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", type=target_type, default="ClaudeCode",
                        help=f"one of {', '.join(TARGETS)} (default: ClaudeCode)")
    add_vault_argument(parser)
    parser.add_argument("--destination",
                        help="required for Custom; overrides a single target")
    add_adapter_directory_arguments(parser, include_custom=False)
    return parser.parse_args()


def destinations(args):
    """[(key, directory)] for the selected target."""
    overrides = adapter_directories(args)
    keys = selected_keys(args.target)
    if args.target == "All" and args.destination:
        raise SystemExit("error: --destination is valid only with a single target")
    if args.target == "Custom" and not args.destination:
        raise SystemExit("error: Custom target requires --destination")

    out = []
    for key in keys:
        if args.destination and args.target != "All":
            directory = args.destination
        else:
            directory = overrides[key]
        out.append((key, absolute(directory)))
    return out


def main():
    args = arguments()
    repo = Path(__file__).resolve().parent
    vault = absolute(args.vault)
    jobs = destinations(args)
    template_path = repo / "SKILL.md"
    template = template_path.read_text(encoding="utf-8")
    if any(not (repo / path).is_file() for path in SHARED_FILES):
        raise SystemExit("error: canonical structural toolkit is incomplete")
    rendered = render(template, vault, repo)

    plans = []
    for key, directory in jobs:
        filename = ADAPTERS[key]["filename"]
        adapter = directory / filename
        if directory == repo or adapter.is_symlink():
            raise SystemExit(f"error: unsafe adapter destination: {adapter}")
        previous = load_manifest(directory)["files"].get(filename)
        if adapter.exists() and not (
            adapter.is_file()
            and (adapter.read_bytes() == rendered.encode("utf-8")
                 or digest(adapter) == previous)
        ):
            raise SystemExit(f"error: adapter has unowned or modified content: {adapter}")
        # Check all targets before changing the first one.
        plans.append((key, directory, retirement_plan(directory, [filename])))

    print("Perspirator installer")
    print(f"  target: {args.target}")
    print(f"  vault:  {vault}")
    for key, directory in jobs:
        print(f"  {ADAPTERS[key]['label']}: {directory / ADAPTERS[key]['filename']}")
    print()

    for key, directory, stale in plans:
        directory.mkdir(parents=True, exist_ok=True)
        filename = ADAPTERS[key]["filename"]
        replace_text(directory / filename, rendered)
        retired = retire_stale_owned_files(directory, [filename], planned=stale)
        write_manifest(directory, [filename])
        if retired:
            print(f"  retired owned files: {', '.join(retired)}")

    print(f"Installed entry points for the shared toolkit at {repo}.")
    doctor = repo / "doctor.py"
    for key, directory in jobs:
        print(f"  {ADAPTERS[key]['label']}: {ADAPTERS[key]['invoke']}")
        print(f'  Validate: python "{doctor}" --target {key} --vault "{vault}" '
              f'{ADAPTERS[key]["directory_option"]} "{directory}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
