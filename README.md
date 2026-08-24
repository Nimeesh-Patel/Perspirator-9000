# Perspirator 9000

Perspirator is a portable institution for criticism, error correction, and
delegating mechanical work to tools around an Obsidian vault of `***` Problem
Notes. Its explanatory heart is the **Theory of LLMs** in the vault runtime:
Nimeesh supplies problems, criticism, and new explanatory knowledge; agents and
scripts perform perspiration over the knowledge and criteria he supplies.

This repository is not the institution's current theory. It packages its
repeatable mechanisms and thin host adapters. The vault supplies the live
runtime, policies, project explanations, and run reports.

## One owner for each role

| Role | Canonical owner |
|---|---|
| Host discovery | installed adapter generated from `SKILL.md` |
| Run loading and question-to-tool routing | `<vault>/memory/perspirator/Bootstrap.md` |
| Explanatory method and Theory of LLMs | `<vault>/memory/perspirator/Perspirator.md` |
| Editable semantic and write policies | `<vault>/memory/policies/*.md` |
| Executable mechanisms and enforced invariants | this repository |
| Past execution evidence | `<vault>/memory/perspirator/runs/` |

Installed adapters and copied scripts are generated artifacts. They do not own
policy, and `doctor.py` detects drift from the repository source. Run reports
are evidence about past work, never a second current runtime.

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
Nimeesh criticises or creates knowledge; permitted changes are validated
```

There is no hidden daemon. Editing the vault runtime or a policy changes the
next run immediately. Changing repository code or an installed path requires a
reinstall because installation copies the toolkit.

## The open-system boundary

Whole-computer scope is a federation of bounded native providers, not one
universal database or global scan. Calibre, Anki, Obsidian, a filesystem, a
browser session, and a source platform retain their distinct facts and failure
modes. A provider result makes its target, scope, freshness, completeness,
status, records, and unobserved boundary inspectable; it does not decide which
fact matters or create an explanatory relation.

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
does not remove unrelated agent files. Adding a host adapter is one entry in
`adapters.py`, which is shared by installation and validation.

For the local Calibre provider, configure Calibre's built-in Content Server on
a literal loopback address with local write disabled. The adapter defaults to
`http://127.0.0.1:8081`, refuses hostnames and non-loopback IPs, never opens
`metadata.db`, and exposes no mutation command. It can read metadata, run a
book-bounded query against Calibre's separately enabled full-text index, and
recover desktop-viewer records embedded in a bounded EPUB copy, or standard
page annotations embedded in a bounded PDF copy. PDF annotations are read-only
evidence: the PDF remains their canonical owner, page rectangles and
quadrilaterals remain native anchors, and every annotation gets a unique
`calibre-pdf://` source locator plus a separate book-level `calibre://` reader
locator. The provider does not pretend Calibre's reflow viewer can reopen those
coordinates. A geometry-only mark stays visible as partial provider evidence
instead of becoming invented source prose. Full-text results are
representative book-format snippets, not every occurrence or a whole-book
extraction; embedded records do not stand for every Calibre or external-reader
annotation namespace. Run
`python calibre_query.py --help` for setup requirements and query syntax.

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

The tests use synthetic fixtures; neighbour tests use a stub embedder and do
not download a model. After changing copied tools, reinstall the affected
target and run `doctor.py` so the installed enactment cannot silently diverge.

## Repository entry points

- `SKILL.md` — minimal host-neutral locator template;
- `install.py` and `adapters.py` — generated host packaging;
- `doctor.py` — runtime, contract, lifecycle, and installed-copy validation;
- `contracts.py` — shared minimal provider/source boundaries; and
- each tool's `--help` plus its tests — canonical executable interface.

For current behaviour, begin with the configured vault's `Bootstrap.md`, not
with repository prose.
