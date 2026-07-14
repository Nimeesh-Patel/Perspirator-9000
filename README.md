# Perspirator 9000

Perspirator is an agent-neutral research toolkit for an Obsidian vault of
Popperian/Deutschian problem notes. It recovers relevant context, follows the
problem graph selectively, draws explanatory implications, exposes
assumptions, and states conflicts between conjectures as precise problems.

Its architecture has four deliberately separate parts:

```text
one canonical bootstrap source        repository SKILL.md
+ one structural toolkit              problem_half.py / problem_index.py
+ one canonical vault runtime         memory/perspirator/Perspirator.md
+ thin discovery adapters             Claude Code and Codex locations
```

The repository owns packaging, structural tools, installers, and validation.
It is not sufficient by itself: every Perspirator run must load the configured
vault runtime, and must refuse if that runtime is unavailable. Core behaviour
lives in the runtime; adapters only make the same bootstrap discoverable.

## Authority and capabilities

The active runtime is authoritative for traversal, relevance, conflicts, and
write behaviour. This README summarizes it; when the two disagree, the
runtime wins. Different agents can have different capabilities, so the
runtime requires disclosing, before substantial work, any missing capability
that limits the answer — never implying access that did not exist.

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
| `SKILL.md` | Canonical bootstrap template: runtime location/refusal, tool paths, run-report duty, and authority rules. No reasoning policy. |
| `problem_half.py` | Stable structural contract for extracting the problem half. |
| `problem_index.py` | Derived, disposable problem-note index; excludes `memory/`. |
| `doctor.py` | Target-aware validation of runtime, scripts, and adapters. |
| `install.ps1` / `install.sh` | Render agent-specific discovery adapters from `SKILL.md`. |
| `EPISTEMIC_METHOD.md` | Small repository-wide epistemic method shared by all agents. |
| `CLAUDE.md` / `AGENTS.md` | Thin environment pointers, not semantic runtimes. |

Python 3 is the only dependency of the structural toolkit and doctor.

## Claude Code setup

Claude Code discovers project commands at a Claude-specific location. Install
the rendered command and scripts with:

```powershell
.\install.ps1 -Target ClaudeCode -VaultDir "C:\path\to\vault"
python .\doctor.py --target ClaudeCode --vault "C:\path\to\vault"
```

```bash
./install.sh --target ClaudeCode --vault "/path/to/vault"
python3 ./doctor.py --target ClaudeCode --vault "/path/to/vault"
```

Default destination: `~/.claude/commands/perspirate.md`. Invoke
`/perspirate`. The adjacent Python files are the structural toolkit. Omitting
the target retains this historical default, but the installer prints the
selected target and destination before writing.

## Codex setup

Codex discovers skills as directories containing `SKILL.md`:

```powershell
.\install.ps1 -Target Codex -VaultDir "C:\path\to\vault"
python .\doctor.py --target Codex --vault "C:\path\to\vault"
```

```bash
./install.sh --target Codex --vault "/path/to/vault"
python3 ./doctor.py --target Codex --vault "/path/to/vault"
```

Default destination: `~/.agents/skills/perspirate/SKILL.md`. The installer
creates the complete `perspirate/` skill directory and places the same three
Python files beside the rendered bootstrap. Invoke the `perspirate` skill by
name or by a request matching its description.

## Combined and custom setup

`All` installs both first-class adapters from the same source:

```powershell
.\install.ps1 -Target All -VaultDir "C:\path\to\vault"
python .\doctor.py --target All --vault "C:\path\to\vault"
```

```bash
./install.sh --target All --vault "/path/to/vault"
python3 ./doctor.py --target All --vault "/path/to/vault"
```

For an unsupported agent, `Custom` requires an explicit destination and
renders a generic `SKILL.md` there without claiming that directory follows
Claude Code or Codex conventions:

```powershell
.\install.ps1 -Target Custom -Destination "D:\agent prompts" -VaultDir "D:\vault"
python .\doctor.py --target Custom --custom-dir "D:\agent prompts" --vault "D:\vault"
```

```bash
./install.sh --target Custom --destination "/agent/prompts" --vault "/vault"
python3 ./doctor.py --target Custom --custom-dir "/agent/prompts" --vault "/vault"
```

An unsupported agent can instead read repository `SKILL.md` directly. It must
resolve `{{VAULT_PATH}}` to the vault root and `{{TOOLS_DIR}}` to this
repository before following it. Both installers are idempotent: they replace
only Perspirator's generated adapter and script copies, and do not remove other
installation files.

## Runtime summary

A problem note contains a line exactly equal to `***`: the problem is above it
and the current conjecture below it. The runtime starts from a seed, reads
problem halves before pulling conjectures, traverses explanatory links rather
than scanning blindly, separates structural facts from semantic judgement,
and leaves conflicts open rather than manufacturing convergence.

The runtime defines one loop, not a catalogue of modes: Nimeesh supplies
a problem or criticism; Perspirator recovers context, builds a bounded
frontier, draws out implications and assumptions, states conflicts as precise
problems, and returns control to Nimeesh. It does not give advice. Reading is
the default; any write to user-authored knowledge needs a named plan, an
exact operation summary, and explicit approval. Detailed artifact classes and
retention rules are delegated to the vault's current policies (see the Memory
Structure Policy) and folder READMEs.

Every substantial traversal or write produces the inspectable run report
required by `runs/README.md`. Behavioural cases and the report contract live
with the runtime under `memory/perspirator/`; installation does not copy or
redefine them.

Runtime-note history is provided by Obsidian Sync rather than manual version
numbers or a parallel changelog. Repository changes are tracked in Git; after
a verified repository change, commit and push the scoped change so the remote
remains the current recoverable source.

## Doctor contract

`doctor.py` defaults to `Auto`: it validates adapters that actually exist and
does not fail a Codex-only setup because Claude Code is absent, or vice versa.
An explicit target validates only that target (`All` validates both). Checks
are grouped into canonical runtime, shared scripts, and adapter sections.

For each adapter, doctor verifies the runtime and script paths, unresolved
placeholders, the complete bootstrap contract, absence of semantic-policy
headings, and byte equality with shared scripts. When the repository template
is available, the rendered adapter must match it exactly. When both adapters
exist, doctor also normalizes their target-specific tool paths and verifies
that their bootstrap semantics and script bytes are identical.
