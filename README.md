# Perspirator 9000

Perspirator is an agent-neutral research toolkit for an Obsidian vault of
`***` Problem Notes. It helps an agent recover context, traverse a problem web,
surface possible relations and conflicts, and make only authorised changes.

The repository supplies mechanisms. The vault supplies the current theory and
instructions. This README explains that architecture; it is not runtime policy.

## The code–Markdown boundary

Perspirator is organised to shorten the criticism-and-revision cycle:

- **Code owns executable mechanisms:** parsers, indexes, adapters, installers,
  validators, stable data contracts, and invariants that must be enforced the
  same way on every run.
- **Markdown owns editable theory:** the runtime, policies, semantic criteria,
  thresholds, instructions, research state, and run reports. Editing an active
  vault note changes the next run without rebuilding or reinstalling code.
- **Runs join them:** tools report structural facts; the active Markdown theory
  tells the agent what questions to ask of those facts; the run records what
  was actually done so a bad result can be criticised.

This is a design boundary, not a claim that code contains no theory. Parsers,
schemas, ranking functions, and tests all embody conjectures too. Code is where
a conjecture goes when it needs deterministic repeated execution; Markdown is
where it stays when it needs fast criticism, semantic interpretation, or
frequent revision. Do not state the same rule in both places: code should load
and validate Markdown configuration instead of copying its meaning.

## Architecture

### Authority and deployment

```text
Perspirator repository
  SKILL.md + install.py + adapters.py + Python tools
                          │
                          │ install copies the toolkit and resolves two paths
             ┌────────────┴────────────┐
             ▼                         ▼
        Claude Code                  Codex
   ~/.claude/commands/...    ~/.agents/skills/perspirate/...
             └────────────┬────────────┘
                          │ installed adapter is only a locator
                          ▼
      <vault>/memory/perspirator/Bootstrap.md
                          │
             ┌────────────┼─────────────────────┐
             ▼            ▼                     ▼
       Perspirator.md   active policies    copied toolkit path
       active runtime  via policy_index.py
             └────────────┼─────────────────────┘
                          ▼
                       agent run
             ┌────────────┼─────────────────────┐
             ▼            ▼                     ▼
        full vault   Basic Memory MCP      Python tools
        Markdown     memory/ only          structural facts
             └────────────┼─────────────────────┘
                          ▼
       explanatory interpretation, authorised writes, run report
```

There is no hidden daemon. Each adapter contains the vault and toolkit paths,
then delegates to the canonical `Bootstrap.md`. The bootstrap refuses to run
without the active runtime. Repository prose cannot override vault policy.

A vault theory/configuration edit is live on the next run. A repository tool or
path change requires reinstalling because the installer copies script bytes.
`doctor.py` detects drift between repository source and installed copies.

Basic Memory and full-vault access are distinct. Basic Memory provides
cross-application recall over `<vault>/memory/`; filesystem or Obsidian access
is needed for the root Problem Note corpus.

### Structural dataflow

```text
                         <vault>/**/*.md
                                │
                         note_chunks.py
                ┌───────────────┼────────────────┐
                ▼               ▼                ▼
        problem_half.py  problem_index.py  neighbour.py index
        one problem side disposable map    disposable vectors
                                                │
                               ┌────────────────┴──────────────┐
                               ▼                               ▼
                    neighbour.py match              problem_candidates.py
                    ranked nearby chunks            recurring/neglected
                               └──────────────┬────────────────┘
                                              ▼
                                            agent

external source URLs → source adapter → {id, text, url} bundle
                                              │
                         agent + neighbour → grouping plan
                                              │
                                              ▼
                                  source_to_notes.py
                                  validated staged notes
```

`note_chunks.py` owns shared Markdown chunking; `problem_half.py` owns the
`***` split. The neighbour index is reused by direct matching and recurrence
candidate retrieval. Embedding and lexical scores nominate passages to inspect;
they do not establish identity, relevance, placement, criticism, or truth.

`policy_index.py` performs the analogous structural job for policy: it exposes
the problems stated by well-formed active policy notes and refuses an ambiguous
surface. The agent still explains which policies bear on the present task and
reads those policies in full. The tool does not rank policy relevance.

Source adapters recover facts only. `x_posts.py` is one adapter, not the
architecture. `source_to_notes.py` checks a plan's mechanical consequences—
coverage, unique assignment, resolvable `up:` links, ordinary Problem Note
structure, exact source text and URLs, and no overwrite. The agent remains
responsible for explaining the problems, grouping sources, choosing relations,
and preserving conflicts.

## Canonical Markdown

| Vault note | Authority |
|---|---|
| `memory/perspirator/Bootstrap.md` | Loading, refusal, tool location, reporting, write authority |
| `memory/perspirator/Perspirator.md` | Active research behaviour and explanatory method |
| `memory/policies/*.md` | Criticisable semantic and write policies |
| `memory/perspirator/Candidate Selection.md` | Candidate signals, thresholds, exemptions, draft form |
| `memory/perspirator/Neighbour Retrieval.md` | Embedding model, indexed areas, exemptions, index location |
| `memory/perspirator/runs/README.md` | Inspectable run-report contract |

## Repository tools

- `install.py` renders an adapter and copies the shared toolkit.
- `doctor.py` validates the active vault contract, source scripts, installed
  adapters, byte equality, and cross-adapter consistency.
- `problem_half.py` reads one Problem Note's frontmatter and problem side.
- `note_chunks.py` emits paragraph/list/fence chunks with provenance and side.
- `problem_index.py` creates a disposable map of root-vault Problem Notes.
- `neighbour.py` incrementally builds and queries the local embedding index.
- `problem_candidates.py` surfaces recurring, undeveloped, or unwritten
  candidates using criteria from `Candidate Selection.md`.
- `policy_index.py` exposes and structurally validates the active policy surface.
- `x_posts.py` canonicalises and fetches public X/Twitter status sources.
- `source_to_notes.py` validates an explanatory grouping plan and stages or
  writes source-grounded Problem Notes without overwriting existing files.

Every CLI documents its detailed arguments and output with `--help`. The code
is the canonical data-contract definition; duplicating every schema here made
the README harder to correct.

## Install and validate

```bash
python install.py --target All --vault "/path/to/vault"
python doctor.py --target All --vault "/path/to/vault"
```

Targets are `ClaudeCode` (default), `Codex`, `All`, and `Custom`. `Custom`
requires `--destination`. Directory overrides are available through
`--claude-dir`, `--codex-dir`, and `--destination`.

Installation replaces only Perspirator's generated adapter and copied toolkit;
it does not remove unrelated agent files.

## Common operations

```bash
# Read a problem before its conjecture.
python problem_half.py "/vault/problem.md" --json

# Build a disposable root-vault problem map.
python problem_index.py "/vault" --out "/scratch/problems.json"

# Query or investigate possible recurrence.
python neighbour.py match --vault "/vault" --text "a problem formulation" \
  --corpus all --side all --k 10
python problem_candidates.py --vault "/vault" --json

# Expose the structurally valid active-policy surface.
python policy_index.py --vault "/vault" --json

# Recover external sources, then validate an agent-authored grouping plan.
python x_posts.py --file "/scratch/x-links.txt" --out "/scratch/sources.json"
python source_to_notes.py "/scratch/sources.json" "/scratch/plan.json" \
  --vault "/vault" --stage "/scratch/notes"
```

For source-to-Problem-Note work, query neighbours twice: first with source
content to recover the existing problem situation, then with each drafted
problem to find candidate parents, rivals, and conflicts. Read problem sides
before conjectures. Group sources only when they contribute to the same
explanatory problem. Conflict is a candidate problem, not untidiness to erase.

## Data lifetime and authority

- Vault Markdown is authoritative research material.
- Repository Python and `SKILL.md` are authoritative executable/packaging
  source; repository changes use Git.
- Installed adapters and scripts are generated copies; reinstall to update.
- Problem-index JSON and `.perspirator/neighbours.npz` are disposable and must
  be regenerated when they disagree with Markdown.
- Match/candidate output is transient retrieval evidence for agent judgment.
- Run reports are retained only while they contain a unique criticism,
  unresolved problem, or replay requirement.

## Dependencies and verification

Python 3 is sufficient for the structural and source tools. The neighbour
subsystem additionally uses `numpy`, `torch`, and `transformers`. `x_posts.py`
uses X's public syndication endpoint and the payload's structural Note Tweet
marker—not text length or appearance—to invoke a status-ID-validated public
long-text fallback. It records the chosen text route and refuses
partial Note Tweet text when that recovery fails. Source fetching therefore
needs network access; the other structural tools operate directly on files.

Tests are grouped by independently breakable contract rather than by module:

```bash
python test_note_chunks.py   # Markdown structure and Problem Note boundaries
python test_neighbour.py     # index lifecycle, retrieval, recurrence consumers
python test_sources.py       # source adapters and source-to-note invariants
```

The suites use synthetic fixtures; neighbour tests use a stub embedder and need
no model download or network. After changing copied repository tools, reinstall
and run `doctor.py` for the affected target.
