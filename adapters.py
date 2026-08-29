#!/usr/bin/env python3
"""The one adapter table and shared installed-file declaration.

`install.py` and `doctor.py` derive targets, directory options, and validation
from this module so a first-class host is declared in one place.
"""

import os
from pathlib import Path

SHARED_FILES = ("problem_half.py", "problem_index.py", "problem_candidates.py",
                "note_chunks.py", "neighbour.py", "obsidian_cli.py",
                "policy_index.py", "note_rename.py", "anki_query.py",
                "calibre_query.py", "pdf_annotations.py", "requirements-pdf.txt",
                "directory_audit.py", "cleanup_manifest.py",
                "cleanup_partition.py", "cleanup_transaction.py",
                "source_to_notes.py", "x_posts.py", "readera_highlights.py",
                "highlights_to_notes.py", "video_sources.py", "evaluate_retrieval.py",
                "change_transaction.py", "contracts.py", "artifact_lifecycle.py",
                "contract_copy.py", "installation.py", "doctor.py", "adapters.py",
                "contract_copies.json",
                "fixtures/problem_note_conformance.json")

VAULT_ENV = "PERSPIRATOR_VAULT"

ADAPTERS = {
    "ClaudeCode": {
        "label": "Claude Code",
        "filename": "perspirate.md",
        "default_dir": Path.home() / ".claude" / "commands",
        "directory_option": "--claude-dir",
        "aliases": ("claude", "claudecode"),
        "first_class": True,
        "invoke": "invoke /perspirate",
    },
    "Codex": {
        "label": "Codex",
        "filename": "SKILL.md",
        "default_dir": Path.home() / ".agents" / "skills" / "perspirate",
        "directory_option": "--codex-dir",
        "aliases": (),
        "first_class": True,
        "invoke": "invoke the perspirate skill by name or request",
    },
    # Custom has no default directory: an unsupported agent must say where.
    "Custom": {
        "label": "Custom",
        "filename": "SKILL.md",
        "default_dir": None,
        "directory_option": "--custom-dir",
        "aliases": (),
        "first_class": False,
        "invoke": "load the rendered SKILL.md in the agent",
    },
}

TARGETS = (*ADAPTERS, "All")
FIRST_CLASS = tuple(key for key, value in ADAPTERS.items()
                    if value["first_class"])

ALIASES = {"all": "All", "auto": "Auto"}
for key, value in ADAPTERS.items():
    for alias in (key, *value["aliases"]):
        ALIASES[alias.casefold()] = key


def parse_target(value):
    """Resolve a target name case-insensitively, or raise ValueError."""
    try:
        return ALIASES[value.strip().lower()]
    except KeyError:
        raise ValueError(f"unknown target: {value}") from None


def selected_keys(target):
    """The adapter keys a target installs or validates."""
    if target == "All":
        return list(FIRST_CLASS)
    return [target]


def add_vault_argument(parser):
    """Add the shared explicit-or-environment vault location contract."""
    configured = os.environ.get(VAULT_ENV)
    parser.add_argument(
        "--vault", default=configured, required=configured is None,
        help=f"Obsidian vault root (or set {VAULT_ENV})")


def add_adapter_directory_arguments(parser, *, include_custom=True):
    """Add every declared host-directory override to an argument parser."""
    for value in ADAPTERS.values():
        if not include_custom and not value["first_class"]:
            continue
        parser.add_argument(value["directory_option"],
                            default=(str(value["default_dir"])
                                     if value["default_dir"] is not None else None),
                            help=f"override the {value['label']} adapter directory")


def adapter_directories(args):
    """Return configured adapter directories keyed by declared target."""
    directories = {}
    for key, value in ADAPTERS.items():
        destination = getattr(
            args, value["directory_option"][2:].replace("-", "_"), None)
        if destination:
            directories[key] = absolute(destination)
    return directories


def generated_files(key):
    """Relative files owned by one generated adapter installation."""
    return [*SHARED_FILES, ADAPTERS[key]["filename"]]


def absolute(path):
    return Path(path).expanduser().resolve(strict=False)


def slash(path):
    return absolute(path).as_posix()


def render(template, vault, tools_dir):
    """Resolve the two path placeholders. The only templating there is."""
    return (template.replace("{{VAULT_PATH}}", slash(vault))
            .replace("{{TOOLS_DIR}}", slash(tools_dir)))
