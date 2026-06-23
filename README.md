# Perspirator 9000

A Claude Code skill (portable to any coding agent) that does the "99%
perspiration" over an Obsidian vault of **problem notes** in the
Popperian / Deutschian tradition: drawing out implications, making hidden
assumptions explicit, computing consequences across linked ideas, finding
conflicts between conjectures that sit too far apart in the link graph to
notice by hand — and, in its write modes, ingesting sources into new
problem notes, connecting existing notes, and filing conflicts as new
notes. **The agent does the perspiration; you keep the inspiration.**

A *problem note* is any note containing a line that is exactly `***`:
above it the open problem, below it the current best conjecture. Both
sides evolve. It is a thinking artifact, not a Q/A card.

This repo is **self-contained**: the skill, both helper scripts, and the
installers are all here. Point any agent at this folder and it can set
itself up.

---

## What's in this repo

| File | Role |
|------|------|
| `SKILL.md` | The skill itself — philosophy, tools, traversal loop, and all six modes. The single source of truth for behavior. Contains a `{{COMMANDS_DIR}}` placeholder the installer resolves. |
| `problem_half.py` | Returns frontmatter + the problem side (above the first `***`) of a note, for cheap relevance routing. |
| `problem_index.py` | Builds a derived, disposable index of every problem note (name, problem-side, category, `up:`, outbound links, stub flag). Read by the write modes to dedup before creating and to find what to link. |
| `install.sh` / `install.ps1` | Deploy the skill + scripts into an agent's commands directory, substituting the real path for `{{COMMANDS_DIR}}`. |
| `AGENTS.md` | Pointer for agents (Codex and others) that auto-read it: read this README, then run the installer. |
| `CLAUDE.md` | The epistemic approach this project follows (Popper / Deutsch). |

---

## Prerequisites

- **Obsidian 1.12+** with the **Command line interface** enabled
  (Settings → General → Command line interface) and `obsidian` on your
  `PATH`. Desktop only; Obsidian must be running with your vault open.
- **Python 3** (no third-party dependencies).

---

## Setup

### Claude Code

```bash
git clone https://github.com/Nimeesh-Patel/Perspirator-9000.git
cd Perspirator-9000
bash install.sh            # or: pwsh ./install.ps1  (Windows)
```

This copies the helper scripts and renders `SKILL.md` into
`~/.claude/commands/perspirate.md` with the real script path filled in.
Then, with Obsidian running, invoke `/perspirate` and prompt by mode
(below).

To install into a different commands directory:

```bash
bash install.sh /path/to/commands       # POSIX
pwsh ./install.ps1 -CommandsDir D:\dir   # Windows
```

### Codex / other agents

These agents don't have Claude Code's `~/.claude/commands` convention.
Two options:

1. **Run the installer** pointed at the agent's own prompt/command
   directory: `bash install.sh /path/to/that/agents/commands`. It will
   render `perspirate.md` there with the script path resolved.
2. **Point the agent directly at `SKILL.md`** in this repo (e.g. include
   it as project context / a system prompt). When reading `SKILL.md`
   raw, treat `{{COMMANDS_DIR}}` as the folder the two `.py` scripts live
   in.

### For an agent setting *itself* up

If you are an AI agent and the user pointed you at this repo, you can
install it yourself: read `AGENTS.md`, confirm the prerequisites, run
`install.sh` (or `install.ps1` on Windows) targeting the correct commands
directory, and report the next steps to the user. Everything you need is
in this folder — no external files.

---

## Use

All modes are driven by how you prompt the skill.

**Read modes** (read-only; never write):

- **Seeded** — `perspirate on [[Deutsch's Law]]` / "what follows from
  [[X]]?" Draws implications, surfaces assumptions, and names conflicts
  between X and its neighbourhood.
- **Conflict-hunt** — "audit my Epistemology collection for conflicts."
  Hunts for conjecture pairs that can't both stand, stated as precise
  problems. No resolutions.
- **External task** — "tomorrow I interview at example.com for a PM role —
  what should I keep in mind, grounded in my notes?" Brings your own
  conjectures to bear on an outside situation.

**Write modes** (additive only — never delete or overwrite; approval-gated):

- **Ingest** — give it a source (file path, URL, or pasted text). It
  extracts the *open problems* the source raises, drafts a conjecture for
  each as new problem notes, dedups against your vault, and links them in.
- **Connect** — "connect my vault" / "find missing links." Appends
  contextual links between existing notes that belong together but aren't
  linked — each link stated as an *argument* for the relationship, never
  touching your existing prose.
- **Conflict write-back** — when conflict-hunting finds a conflict, files
  it as a new problem note linking the two conjectures, leaving the
  resolution to you.

---

## Invariants (what the write modes will never do)

- Never delete or overwrite existing content. All writing is additive:
  new notes, or appended lines. Your problem/conjecture prose is never
  modified.
- Always dedup against the index before creating, so a source is never
  turned into a duplicate of a note you already have.
- Match your vault's conventions (`up:` / `category:` frontmatter, the
  `***` separator, your title style), learned from the vault.
- Surface a diff for every write. The index is derived and disposable;
  your markdown is the database.
