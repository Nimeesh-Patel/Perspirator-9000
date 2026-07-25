#!/usr/bin/env python3
"""Render agent discovery adapters from the canonical SKILL.md.

One installer for every platform; the adapter table lives in adapters.py.
Examples:
    python install.py --target ClaudeCode
    python install.py --target All --vault "D:\\My Vault"
    python install.py --target Custom --destination "D:\\agent prompts"
"""

import argparse
import shutil
import sys
from pathlib import Path

from adapters import (ADAPTERS, SCRIPT_NAMES, absolute, parse_target, render,
                      selected_keys)


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
                        help="ClaudeCode (default), Codex, All, or Custom")
    parser.add_argument("--vault", default=str(Path.home() / "nimeesh vault"),
                        help="Obsidian vault root")
    parser.add_argument("--destination",
                        help="required for Custom; overrides a single target")
    parser.add_argument("--claude-dir", default=str(ADAPTERS["ClaudeCode"]["default_dir"]))
    parser.add_argument("--codex-dir", default=str(ADAPTERS["Codex"]["default_dir"]))
    return parser.parse_args()


def destinations(args):
    """[(key, directory)] for the selected target."""
    overrides = {"ClaudeCode": args.claude_dir, "Codex": args.codex_dir}
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

    print("Perspirator installer")
    print(f"  target: {args.target}")
    print(f"  vault:  {vault}")
    for key, directory in jobs:
        print(f"  {ADAPTERS[key]['label']}: {directory / ADAPTERS[key]['filename']}")
    print()

    template = (repo / "SKILL.md").read_text(encoding="utf-8")
    for key, directory in jobs:
        directory.mkdir(parents=True, exist_ok=True)
        for script in SCRIPT_NAMES:
            shutil.copyfile(repo / script, directory / script)
        adapter = directory / ADAPTERS[key]["filename"]
        adapter.write_text(render(template, vault, directory),
                           encoding="utf-8", newline="\n")

    print("Installed without removing any existing unrelated files.")
    doctor = repo / "doctor.py"
    flags = {"ClaudeCode": "--claude-dir", "Codex": "--codex-dir",
             "Custom": "--custom-dir"}
    for key, directory in jobs:
        print(f"  {ADAPTERS[key]['label']}: {ADAPTERS[key]['invoke']}")
        print(f'  Validate: python "{doctor}" --target {key} --vault "{vault}" '
              f'{flags[key]} "{directory}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
