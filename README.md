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
| Working problem records, handoffs and delivery | `<vault>/memory/policies/Error Correction.md` |
| Editable semantic and write policies | `<vault>/memory/policies/*.md` |
| Executable mechanisms and enforced invariants | this repository |
| Evidence retention and exact bot records | `<vault>/memory/policies/Note Deletion.md` and `Bot grants.md` |

Installed adapters and copied scripts are generated artifacts. They do not own
policy. Run `doctor.py` from the repository, or supply its canonical `SKILL.md`
with `--source`, to check drift against that independent source. An installed
doctor without a canonical source checks local structure and ownership and
reports source comparison as unavailable. These checks do not judge an
argument or whether a draft answered the task. Retained execution evidence
supports claims about past work.

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

There is no hidden daemon. Editing vault instructions changes the next run;
changing copied repository tools requires reinstalling the affected target.

## Install and verify

Python 3 is required. From this repository:

```bash
python install.py --target All --vault "/path/to/vault"
python doctor.py --target All --vault "/path/to/vault"
```

Targets are `ClaudeCode` (default), `Codex`, `All`, and `Custom`. `Custom`
requires `--destination`; directory overrides are available through
`--claude-dir`, `--codex-dir`, and `--destination`.

Installation replaces only files named in Perspirator's generated manifest. It
does not remove unrelated agent files. The adapter declaration in `adapters.py`
owns host names, aliases, directories, invocation text, and validation options.
The same module owns the shared relative-file install surface, including
contract declarations and canonical fixtures.

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
not download a model. After changing copied tools, reinstall the affected
target and run `doctor.py` so the installed enactment cannot silently diverge.

## Repository entry points

- [SKILL.md](SKILL.md) — minimal host-neutral locator template;
- [install.py](install.py) and [adapters.py](adapters.py) — generated host packaging;
- [doctor.py](doctor.py) — runtime, contract, lifecycle, and installed-copy validation;
- [role_harness.py](role_harness.py) — small role discovery surface, bounded exact
  handoff snapshots and response identity/freshness checks;
- [context_query.py](context_query.py) — task-derived, identity-labelled vault
  nominations using the shared batch rankers and original source readback;
- [artifact_lifecycle.py](artifact_lifecycle.py) — read-only inventory of explicitly
  selected declared artifact trees; hashes and metadata inform lifecycle review;
- [contracts.py](contracts.py) — shared minimal provider/source boundaries; and
- each tool's `--help` plus its tests — canonical executable interface.

For current behaviour, begin with the configured vault's `Bootstrap.md`, not
with repository prose.
