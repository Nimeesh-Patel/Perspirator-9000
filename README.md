# Perspirator 9000

A Claude Code skill (portable to any coding agent) that does the "99%
perspiration" over an Obsidian vault of **problem notes** in the
Popperian / Deutschian tradition: drawing out implications, making hidden
assumptions explicit, computing consequences across linked ideas, finding
conflicts between conjectures that sit too far apart in the link graph to
notice by hand — and, in its write modes, ingesting sources into new
problem notes, connecting existing notes, and filing conflicts as new
notes. Its bridge modes exchange curated context with a cross-app shared
memory. **The agent does the perspiration; you keep the inspiration.**

A *problem note* is any note containing a line that is exactly `***`:
above it the open problem, below it the current best conjecture. Both
sides evolve. It is a thinking artifact, not a Q/A card.

Since v1 of the vault runtime (2026-07-10), the skill is split in two:

- **`SKILL.md` here is only a small, stable bootstrap.** It locates the
  canonical runtime, loads it at the start of every task, points at the
  structural scripts, requires a run report, and refuses to run if the
  runtime is unreadable. It contains no reasoning policy.
- **The semantic runtime lives in the vault** at
  `<vault>/memory/perspirator/Perspirator.md` — philosophy, traversal,
  relevance, all eight modes, invariants, and the authority rule. Editing
  that file in Obsidian changes the next run; no redeployment. Alongside
  it: `CHANGELOG.md` (approved changes), `proposals/` (agent-suggested
  changes awaiting Nimeesh), `cases/` (replayable behavioural cases), and
  `runs/` (inspectable run reports).

This repo is **self-contained** for deployment: the bootstrap, the three
helper scripts, and the installers are all here. Point any agent at this
folder and it can set itself up — but the behaviour it will follow is the
vault's runtime file.

---

## What's in this repo

| File | Role |
|------|------|
| `SKILL.md` | The **bootstrap only**: locate + load the vault runtime, tool pointers, run-report duty, refusal rule, authority rule. Contains `{{COMMANDS_DIR}}` and `{{VAULT_PATH}}` placeholders the installer resolves. The behaviour itself lives in `<vault>/memory/perspirator/Perspirator.md`. |
| `problem_half.py` | Returns frontmatter + the problem side (above the first `***`) of a note, for cheap relevance routing. |
| `problem_index.py` | Builds a derived, disposable index of every problem note (name, problem-side, category, `up:`, outbound links, stub flag). Read by the write modes to dedup before creating and to find what to link. Excludes the vault's `memory/` folder so bridge notes never enter the problem index. |
| `doctor.py` | Validates an installation: runtime present/active/versioned, bootstrap points at it, required dirs, scripts discoverable, runs dir writable. |
| `install.sh` / `install.ps1` | Deploy the bootstrap + scripts into an agent's commands directory, substituting real paths for the placeholders. |
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

This copies the three helper scripts (`problem_half.py`,
`problem_index.py`, `doctor.py`) and renders `SKILL.md` into
`~/.claude/commands/perspirate.md` with the real script and vault paths
filled in. Run `python doctor.py` to verify the canonical runtime is
reachable, then, with Obsidian running, invoke `/perspirate` and prompt
by mode (below).

To install into a different commands directory or vault:

```bash
bash install.sh /path/to/commands "/path/to/vault"        # POSIX
pwsh ./install.ps1 -CommandsDir D:\dir -VaultDir D:\vault  # Windows
```

(The vault argument defaults to `~/nimeesh vault`.)

### Codex / other agents

These agents don't have Claude Code's `~/.claude/commands` convention.
Two options:

1. **Run the installer** pointed at the agent's own prompt/command
   directory: `bash install.sh /path/to/that/agents/commands`. It will
   render `perspirate.md` there with the script path resolved.
2. **Point the agent directly at `SKILL.md`** in this repo (e.g. include
   it as project context / a system prompt). When reading `SKILL.md`
   raw, treat `{{COMMANDS_DIR}}` as the folder the `.py` scripts live
   in and resolve `{{VAULT_PATH}}` via `obsidian vault info=path`.

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

**Bridge modes** (connect the vault to a cross-app shared memory):

These modes assume a [basic-memory](https://github.com/basicmachines-co/basic-memory)
project scoped to a `memory/` subfolder of the vault, shared as an MCP server
with other AI apps (ChatGPT, Claude web, Codex, Claude Code). The vault's
problem notes and the memory notes are two deliberately separate populations:
bm never indexes the vault outside `memory/`, and `problem_index.py` excludes
`memory/`. These modes are the only crossing point. (The memory system's own
infrastructure — the MCP wiring, remote access for the web apps, operations —
is documented in its own repo, `basic-memory-remote`.)

- **Context export (Mode 7)** — "brief memory on [[X]]." Gathers the relevant
  vault neighbourhood of a problem and writes a curated brief into `memory/`,
  so the other apps can see the vault's current state of that problem without
  ever touching the vault. Augments an existing brief rather than duplicating.
- **Promotion (Mode 8)** — "promote memory." Treats a memory note (progress
  made in another app) as a source: extracts the durable problems and
  conjectures and promotes them into proper problem notes, dedup'd and
  additive, under the same invariants as Ingest. The memory note stays —
  memory is the working layer, the vault is the record.

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
