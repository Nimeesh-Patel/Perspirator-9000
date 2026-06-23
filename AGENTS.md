# Agent setup instructions

If you are an AI agent and the user pointed you at this repository, here
is how to set it up (for the user, or for yourself).

## What this is

Perspirator 9000 is a skill for doing the "99% perspiration" over an
Obsidian vault of problem notes. The full behavior spec is in `SKILL.md`
— read it. This file is only the install pointer.

## Prerequisites to confirm

1. **Obsidian 1.12+** running, with the **Command line interface** enabled
   and `obsidian` on `PATH`. Verify with: `obsidian help`.
2. **Python 3** available. Verify with: `python --version` or
   `python3 --version`.

## Install

Run the installer, targeting the commands/prompts directory for the agent
you are setting up (default is Claude Code's `~/.claude/commands`):

- POSIX / Git-Bash / WSL / macOS / Linux:
  `bash install.sh [commands-dir]`
- Windows PowerShell:
  `pwsh ./install.ps1 [-CommandsDir <dir>]`

The installer copies `problem_half.py` and `problem_index.py` into that
directory and renders `SKILL.md` into `perspirate.md` there, substituting
the real absolute script path for the `{{COMMANDS_DIR}}` placeholder.

If the agent you are configuring has no commands directory, just load
`SKILL.md` as context directly and read `{{COMMANDS_DIR}}` as "the folder
the two `.py` scripts live in."

## After install

Tell the user: with Obsidian running and their vault open, they invoke the
skill by prompting it per mode (see `README.md` → Use). Do not run the
write modes (Ingest, Connect, conflict write-back) without the user's
go-ahead — they are approval-gated by design.
