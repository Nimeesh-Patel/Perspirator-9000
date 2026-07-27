# Perspirator 9000

Perspirator is an agent-neutral research toolkit for an Obsidian vault of
Popperian/Deutschian problem notes. It recovers relevant context, follows the
problem graph selectively, draws explanatory implications, exposes
assumptions, and states conflicts between conjectures as precise problems.

Its architecture has four deliberately separate parts:

```text
one canonical bootstrap contract      vault memory/perspirator/Bootstrap.md
+ one canonical vault runtime         vault memory/perspirator/Perspirator.md
+ one structural toolkit              note_chunks.py / problem_*.py / neighbour.py
+ thin discovery adapters             rendered from repository SKILL.md
```

Every text an agent reads is a vault note; this repository keeps only
locators, structural tools, the installer, and validation. `SKILL.md` is a
template holding two path placeholders and one instruction — read
`Bootstrap.md` and follow it, or STOP. Editing the bootstrap contract or the
runtime therefore takes effect on the next run; **a reinstall is needed only
when a path changes.**

Because the bootstrap contract is a vault note, a Git clone alone does not
reproduce agent behaviour: it reproduces the locator that finds it.

## Authority and capabilities

The active runtime is authoritative for traversal, relevance, conflicts, and
write behaviour; the bootstrap note is authoritative for loading, tools,
reporting, and authority. This README describes packaging only — where the two
disagree about behaviour, the vault notes win. Different agents can have
different capabilities, so the runtime requires disclosing, before substantial
work, any missing capability that limits the answer — never implying access
that did not exist.

| Capability | What it enables | Universal requirement? |
|---|---|---|
| Basic Memory MCP | Cross-app problem map and shared working notes | No; direct `memory/` access can substitute |
| Direct `memory/` access | Filesystem fallback for the same problem map | No; MCP can substitute |
| Full-vault filesystem | Problem-note reads and structural traversal | Required only for tasks that need the vault |
| Obsidian CLI | Search context, backlinks, properties, and CLI writes | No; requires Obsidian running when used |
| Structural scripts | Deterministic note halves and derived indexes | Required when their structural facts are needed |
| Write access | Run reports and approval-gated writes | Required only for the relevant write |

`problem_half.py` and `problem_index.py` read Markdown directly and do not need
Obsidian to be running. Obsidian desktop and its CLI are useful or required for
the particular capabilities that call them, not for every run.

## Repository files

| File | Responsibility |
|---|---|
| `SKILL.md` | Adapter template: two path placeholders and the pointer to `Bootstrap.md`. No reasoning policy, no bootstrap contract. |
| `adapters.py` | The one adapter table — name, output filename, default directory — imported by the installer and the doctor. |
| `install.py` | Renders discovery adapters from `SKILL.md` and copies the toolkit. |
| `doctor.py` | Target-aware validation of vault notes, scripts, and adapters. |
| `problem_half.py` | Stable structural contract for extracting the problem half — sole owner of the `***` split. |
| `note_chunks.py` | The one structural parser: frontmatter, wikilinks, headings, list items, offsets, `side`, `corpus`. Everything else imports it. |
| `problem_index.py` | Derived, disposable problem-note index; excludes `memory/`. |
| `neighbour.py` | Distributional neighbours of a piece of text. The only file with embedding dependencies. |
| `test_note_chunks.py` / `test_neighbour.py` | Chunking, provenance, filtering, index freshness, and failure cases. Synthetic fixtures; no vault, no model. |
| `CLAUDE.md` / `AGENTS.md` | Thin environment pointers, not semantic runtimes. |

Python 3 is the only dependency of the structural toolkit. `neighbour.py` is
the one exception — it needs `numpy`, `torch`, and `transformers`, and nothing
else imports them, so the rest of the toolkit still runs on a bare Python.
Adding a new agent is one row in `adapters.py`.

## Installing

```bash
python install.py --target ClaudeCode --vault "/path/to/vault"
python doctor.py  --target ClaudeCode --vault "/path/to/vault"
```

| Target | Default destination | Invocation |
|---|---|---|
| `ClaudeCode` (default) | `~/.claude/commands/perspirate.md` | `/perspirate` |
| `Codex` | `~/.agents/skills/perspirate/SKILL.md` | the `perspirate` skill by name or matching request |
| `All` | both of the above | — |
| `Custom` | `--destination` (required) | load the rendered `SKILL.md` in the agent |

`--claude-dir`, `--codex-dir`, and `--destination` override destinations;
`doctor.py` takes the same flags plus `--custom-dir`. Installs are idempotent:
they replace only Perspirator's generated adapter and script copies and remove
nothing else. An unsupported agent can instead read `SKILL.md` directly,
resolving `{{VAULT_PATH}}` to the vault root and `{{TOOLS_DIR}}` to this
repository.

## Neighbour retrieval

One substrate, several uses. `neighbour.py` answers a single question — *which
chunks are distributionally near this text?* — and callers decide what the
answer is for:

```bash
python neighbour.py index --vault "/path/to/vault"
python neighbour.py match --vault "/path/to/vault" --file "some note.md" --corpus vault --k 10
python neighbour.py match --vault "/path/to/vault" --text "loose idea" --side problem --json
```

Recurrence, problem-candidate material, idea placement, link discovery, and
frontier expansion during a run are *queries*, not modes: `--corpus`, `--side`,
`--folder`, and `--k` are how a caller narrows the space. The substrate does not
know which use is intended and never files, moves, links, merges, or creates
anything.

It explicitly does **not** claim that two passages state the same problem, that
one belongs inside the other, that a result is a criticism or a rival
conjecture, or that a note is the right destination. Every result carries rank,
score, note, heading, `side`, `corpus`, and whether the two notes already link
or share referrers — so *already contained* and *already connected* stay
distinguishable from *possibly new*. Scores are ordinal; a score is not
confidence.

The mechanism — model, indexed corpora and sides, exemptions, index location,
provenance fields — lives in `memory/perspirator/Neighbour Retrieval.md` and is
edited there, not in code. `neighbour.py` stops if that note is missing or not
`status: active`. `Candidate Selection.md` continues to own what counts as a
candidate.

The index is disposable and regenerable: `<vault>/.perspirator/neighbours.npz`,
a dot-folder Obsidian and `problem_index.py` both skip. Delete the file to
remove it. Embedding dependencies (numpy, torch, transformers) are confined to
`neighbour.py`; every other tool stays standard-library only.

**Staying fresh is not scheduled and not remembered.** Every `match` levels the
index against the vault before answering, and the cost tracks what changed, not
the size of the vault: a note is re-read only when its mtime or size moved, a
chunk is re-embedded only when its text changed, and deleted notes drop out
with their vectors. Editing in Obsidian and querying immediately retrieves the
edited text; `--no-refresh` skips the stat pass when the vault is known
unchanged. A full build of 5,617 chunks takes ~140 s, an unchanged refresh is
instant, and touching one note re-embeds only that note's changed chunks.

Two changes can't be repaired incrementally and stop with an explicit message
instead of silently mixing: a different model, and a change to the indexed
corpora, sides, or exemptions. Both are fixed with `index --rebuild`.

## Runtime and bootstrap

Both are vault notes, and both are authoritative over anything written here:

- `memory/perspirator/Bootstrap.md` — load the runtime or refuse, structural
  tools, run-report duty, authority rule.
- `memory/perspirator/Perspirator.md` — the reasoning policy.

A problem note contains a line exactly equal to `***`: the problem is above it
and the current conjecture below it. That structural fact is what the toolkit
depends on; everything else about how to reason over it lives in the runtime,
deliberately not in this README, so the two cannot drift apart.

Runtime-note history is provided by Obsidian Sync rather than manual version
numbers or a parallel changelog. Repository changes are tracked in Git; after
a verified repository change, commit and push the scoped change so the remote
remains the current recoverable source.

## Doctor contract

`doctor.py` defaults to `Auto`: it validates adapters that actually exist and
does not fail a Codex-only setup because Claude Code is absent, or vice versa.
An explicit target validates only that target (`All` validates both
first-class adapters). Checks are grouped into vault notes, shared scripts,
and adapter sections.

It verifies that the runtime, `Bootstrap.md`, and the Policy Loader note exist
and are `status: active`; that each adapter resolves both placeholders, names
the `Bootstrap.md` path, points at its own toolkit, and renders exactly from
the template; and that the installed scripts match the shared source byte for
byte. When more than one adapter is present, it normalizes their
target-specific paths and verifies that all of them share one bootstrap
semantics and identical script bytes.
