# Perspirator 9000

Perspirator is a portable institution for criticism, error correction, and
delegating mechanical work to tools around an Obsidian vault of `***` Problem
Notes. Its explanatory heart is the **Theory of LLMs** in the vault runtime:
the human collaborator supplies problems, criticism, and new explanatory
knowledge; agents and scripts perform perspiration over that knowledge and
those criteria.

This repository is not the institution's current theory. It packages its
repeatable mechanisms and thin host adapters. The vault supplies the live
runtime, policies, project explanations, and run reports.

## One owner for each role

| Role | Canonical owner |
|---|---|
| Host discovery | installed adapter generated from `SKILL.md` |
| Run loading and question-to-tool routing | `<vault>/memory/perspirator/Bootstrap.md` |
| Explanatory method and Theory of LLMs | `<vault>/memory/perspirator/Perspirator.md` |
| Pre-delivery draft criticism | `<vault>/memory/perspirator/Error Correction role.md` |
| Editable semantic and write policies | `<vault>/memory/policies/*.md` |
| Executable mechanisms and enforced invariants | this repository |
| Past execution evidence | `<vault>/memory/perspirator/runs/` |

Installed adapters and copied scripts are generated artifacts. They do not own
policy. Run `doctor.py` from the repository, or supply its canonical `SKILL.md`
with `--source`, to check drift against that independent source. An installed
doctor without a canonical source checks local structure and ownership and
reports source comparison as unavailable. These checks do not judge an
argument or whether a draft answered the task. Run reports are evidence about
past work, never a second current runtime.

## How a run starts

```text
host invokes Perspirator
        |
        v
thin installed adapter
        |
        v
Bootstrap.md ──> active runtime + relevant policies
        |
        +──> vault and Basic Memory context
        +──> bounded native providers
        +──> structural and transactional tools
        |
        v
agent draws out implications, assumptions, consequences, and conflicts
        |
        v
self-pass under Error Correction role, as the active runtime requires
        |
        v
human collaborator criticises or creates knowledge; permitted changes are validated
```

There is no hidden daemon. Editing the vault runtime or a policy changes the
next run immediately. Changing repository code or an installed path requires a
reinstall because installation copies the toolkit.

## The open-system boundary

Whole-computer scope is a federation of bounded native providers, not one
universal database or global scan. Calibre, Anki, Obsidian, a filesystem, a
browser session, and a source platform retain their distinct facts and failure
modes. Shared provider results expose target, scope, freshness, completeness,
status, records, and unobserved boundaries. Adapters with native-specific
result shapes keep their observed data and failure state explicit; no provider
decides which fact matters or creates an explanatory relation.

Repository mechanisms currently cover four broad jobs:

- structural parsing, retrieval, and vault/application context;
- read-only native observations such as Anki and loopback-only Calibre;
- exact source recovery and guarded source-to-note formation; and
- identity-checked rename, staging, cleanup, and transaction validation.

`Bootstrap.md` names the question each mechanism can answer. Every command owns
its detailed interface in `--help`, and its code and tests own the exact data
contract. The README deliberately does not duplicate either surface.

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
ownership are explained in `<vault>/memory/projects/Reading Integration.md`;
the code, emitted scope and tests own exact coverage and locator contracts.

## Development boundary

Put a rule in code when it needs deterministic repeated execution or enforced
validation. Keep it in vault Markdown when it is editable theory, a semantic
criterion, current project knowledge, or run evidence. Do not maintain the same
current rule in both places: code should load or validate Markdown-owned
configuration instead of paraphrasing it.

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
- [run_check.py](run_check.py) — advisory report headings loaded from the vault README;
- [contracts.py](contracts.py) — shared minimal provider/source boundaries; and
- each tool's `--help` plus its tests — canonical executable interface.

For current behaviour, begin with the configured vault's `Bootstrap.md`, not
with repository prose.

Native Anki read responses must carry the expected envelope and one exact
record per requested note identity. Null, truncated or mismatched observations
are errors, not empty collections or absent notes. Structural Markdown readers
refuse invalid UTF-8 instead of replacing bytes inside an apparent explanation;
a failed root-note scan cannot overwrite a previously derived index.
