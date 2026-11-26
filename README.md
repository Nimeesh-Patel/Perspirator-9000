# Perspirator 9000

Perspirator is a portable institution for criticism, error correction, and
delegating mechanical work to tools around an Obsidian vault of `***` Problem
Notes. Its explanatory heart is the **Theory of LLMs** in the vault runtime:
the human collaborator supplies problems, criticism, and new explanatory
knowledge; agents and scripts perform perspiration over that knowledge and
those criteria.

This repository is not the institution's current theory. It packages its
repeatable mechanisms and thin host adapters. The vault supplies the live
runtime, policies, and working explanations.

## One owner for each role

| Role | Canonical owner |
|---|---|
| Host discovery | installed adapter generated from `SKILL.md` |
| Run loading and scope selection | `<vault>/memory/perspirator/Bootstrap.md` |
| On-demand mechanism routes | `<vault>/memory/perspirator/Tool Routes.md` |
| Explanatory method and Theory of LLMs | `<vault>/memory/perspirator/Perspirator.md` |
| Pre-delivery draft criticism | `<vault>/memory/perspirator/Error Correction role.md` |
| Selective role execution and parallel delegation | `<vault>/memory/perspirator/Role Delegation.md` |
| Institutional correction from supplied knowledge | `<vault>/memory/perspirator/Optimism role.md` |
| Task-specific context and explanatory reconstruction | `<vault>/memory/perspirator/Context Recovery role.md` |
| Evolving working understanding of problems and their connections | `<vault>/memory/Thales/*.md`, maintained under `Note Placement.md` |
| Working problem records, handoffs and delivery | `<vault>/memory/policies/Error Correction.md` |
| Editable semantic and write policies | `<vault>/memory/policies/*.md` |
| Executable mechanisms and enforced invariants | this repository |
| Evidence retention and exact bot records | `<vault>/memory/policies/Note Deletion.md` and `Bot grants.md` |

Each installed adapter points to this shared checkout and the configured
vault. Run `doctor.py` from the checkout to validate both pointers and the
toolkit. These checks cover installation and declared contracts. Retained
execution evidence supports claims about past work.

## Runtime and tools

Start with the configured vault's `Bootstrap.md`. It loads the active runtime
and selects material for the current task. Look up a mechanism in
`Tool Routes.md` when needed. Each command's `--help`, code and tests own its
detailed interface.
`Role Delegation.md` owns selecting and running roles in separate contexts.
`Vault Evolution.md` owns the open-system boundary between the vault and
bounded native providers.

`role_harness.py` discovers roles and prepares exact, bounded handoffs;
`context_query.py nominate` uses the shared rankers to nominate source notes
for original-source reading. Their vault role instructions own interpretation.

Local agents use the shared tools directly. Reinstall an adapter when its
template changes or either directory moves.

## Install and verify

Python 3 is required. From this repository:

```bash
python install.py --target All --vault "/path/to/vault"
python doctor.py --target All --vault "/path/to/vault"
```

Targets are `ClaudeCode` (default), `Codex`, `All`, and `Custom`. `Custom`
requires `--destination`; directory overrides are available through
`--claude-dir`, `--codex-dir`, and `--destination`.

Installation writes a small entry point for each target. When upgrading a
copy-based installation, it retires only files whose hashes still match the
old installer manifest. Switch any external consumers of those copies to the
checkout before upgrading. The adapter declaration in `adapters.py` owns
host names, aliases, directories, invocation text, and validation options.
The same module owns the required toolkit inventory, including
contract declarations and canonical fixtures.

For live vault operations, enable **Command line interface** in Obsidian's
General settings and make the official Obsidian plugin available in the host.
`/perspirate` loads the relevant Obsidian instructions when a task needs
them. See [Obsidian's connector setup](https://obsidian.md/help/connectors).

Native providers also need their own application setup. Calibre requires a
literal-loopback Content Server with local write disabled; the adapter never
opens `metadata.db` or mutates the library. Anki and Obsidian availability must
likewise be established through their own providers.

For supported operations, dependencies and observation limits, start with the
relevant command's `--help`. Calibre/document reading and native annotation
ownership are explained in the vault's `Reading Integration` note. The code,
emitted scope and tests own exact coverage and locator contracts.

## Dependencies and tests

The standard library is sufficient for structural, EPUB-provider, source, and
transaction tools. Native PDF annotation recovery additionally uses the
explicit dependency in `requirements-pdf.txt`; without it only that capability
reports `unavailable`. Neighbour retrieval uses `numpy`, `torch`, and
`transformers`. Obsidian CLI is an optional provider, not an installation
prerequisite.

`anki_query.py compare` reads two explicitly selected dated collection
backups (or closed SQLite copies), compares note/card/review records by ID,
and reports bounded records with complete totals and omissions. It makes no
live collection or scheduling changes. Compressed AnkiDroid backups need
`python -m pip install -r requirements-anki.txt`; other Anki queries do not
import that optional dependency. `compare --help` lists input, database, row
and output limits. Caller-supplied dates and a shared collection identity are
not independently established by a file name or an ID overlap.

Install the bounded PDF annotation dependency with:

```bash
python -m pip install --user --requirement requirements-pdf.txt
```

Run the complete repository suite with:

```bash
python -m unittest discover -p "test_*.py"
```

The documented discovery command also runs the chunking and neighbour contract
checks; they must not survive solely behind standalone __main__ entry points.
The tests use synthetic fixtures; neighbour tests use a stub embedder and do
not download a model. Run `doctor.py` to check the target entry points against
the shared checkout.

## Repository entry points

- [SKILL.md](SKILL.md) — minimal host-neutral locator template;
- [install.py](install.py) and [adapters.py](adapters.py) — generated host packaging;
- [doctor.py](doctor.py) — runtime, contract, lifecycle, and entry-point validation;
- [role_harness.py](role_harness.py) — small role discovery surface, bounded exact
  handoff snapshots and response identity/freshness checks;
- [context_query.py](context_query.py) — task-derived, identity-labelled vault
  nominations using the shared batch rankers and original source readback;
- [artifact_lifecycle.py](artifact_lifecycle.py) — read-only inventory of explicitly
  selected declared artifact trees and path/type/pattern checks; hashes and
  metadata inform retention decisions owned by the vault policies;
- [contracts.py](contracts.py) — shared minimal provider/source boundaries; and
- each tool's `--help` plus its tests — canonical executable interface.

For current behaviour, begin with the configured vault's `Bootstrap.md`, not
with repository prose.
