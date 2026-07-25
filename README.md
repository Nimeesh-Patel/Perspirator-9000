# Perspirator 9000

Perspirator is an agent-neutral research toolkit for an Obsidian vault of
Popperian/Deutschian problem notes. It recovers relevant context, follows the
problem graph selectively, draws explanatory implications, exposes
assumptions, and states conflicts between conjectures as precise problems.

Its architecture has four deliberately separate parts:

```text
one canonical bootstrap contract      vault memory/perspirator/Bootstrap.md
+ one canonical vault runtime         vault memory/perspirator/Perspirator.md
+ one structural toolkit              problem_half.py / problem_index.py
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
| `problem_half.py` | Stable structural contract for extracting the problem half. |
| `problem_index.py` | Derived, disposable problem-note index; excludes `memory/`. |
| `CLAUDE.md` / `AGENTS.md` | Thin environment pointers, not semantic runtimes. |

Python 3 is the only dependency. Adding a new agent is one row in
`adapters.py`.

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
