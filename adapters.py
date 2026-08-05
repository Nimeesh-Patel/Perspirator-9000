#!/usr/bin/env python3
"""The one adapter table: name, output filename, default directory.

`install.py` and `doctor.py` both import this, so adding an agent is one row
here rather than parallel edits to an installer and a validator.
"""

from pathlib import Path

SCRIPT_NAMES = ("problem_half.py", "problem_index.py", "problem_candidates.py",
                "note_chunks.py", "neighbour.py", "obsidian_cli.py",
                "policy_index.py", "note_rename.py", "anki_query.py",
                "source_to_notes.py", "x_posts.py", "readera_highlights.py",
                "highlights_to_notes.py", "video_sources.py", "evaluate_retrieval.py",
                "doctor.py", "adapters.py")

ADAPTERS = {
    "ClaudeCode": {
        "label": "Claude Code",
        "filename": "perspirate.md",
        "default_dir": Path.home() / ".claude" / "commands",
        "invoke": "invoke /perspirate",
    },
    "Codex": {
        "label": "Codex",
        "filename": "SKILL.md",
        "default_dir": Path.home() / ".agents" / "skills" / "perspirate",
        "invoke": "invoke the perspirate skill by name or request",
    },
    # Custom has no default directory: an unsupported agent must say where.
    "Custom": {
        "label": "Custom",
        "filename": "SKILL.md",
        "default_dir": None,
        "invoke": "load the rendered SKILL.md in the agent",
    },
}

TARGETS = ("ClaudeCode", "Codex", "All", "Custom")
FIRST_CLASS = ("ClaudeCode", "Codex")

ALIASES = {
    "claude": "ClaudeCode",
    "claudecode": "ClaudeCode",
    "codex": "Codex",
    "all": "All",
    "custom": "Custom",
    "auto": "Auto",
}


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


def absolute(path):
    return Path(path).expanduser().resolve(strict=False)


def slash(path):
    return absolute(path).as_posix()


def render(template, vault, tools_dir):
    """Resolve the two path placeholders. The only templating there is."""
    return (template.replace("{{VAULT_PATH}}", slash(vault))
            .replace("{{TOOLS_DIR}}", slash(tools_dir)))
