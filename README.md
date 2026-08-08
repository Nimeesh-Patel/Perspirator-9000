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
        |
        v
problem_half.py: one normalized frontmatter/problem/conjecture structure
        |
        v
note_chunks.py: select formation by structure
        |
        +-- Problem Note --> complete problem_identity + contextual conjecture
        |                    (authored boundaries, then token windows if needed)
        |
        +-- other note ----> heading-aware authored blocks
        |
        v
neighbour.py index: embed common RetrievalUnit records
        |
        v
neighbour.py match: rank units --> collapse best unit per note
neighbour.py pairs: rank mutually near filtered units --> collapse note pairs
        |
        v
obsidian_cli.py: bounded links/backlinks/properties after retrieval
        |
        v
agent reads candidate notes and judges identity, relation, conflict, placement
```

anki_query.py is the read-only boundary onto the external consumer. Anki
holds facts no other provider has — which cards exist, what a card's fields
currently say, and what its review history is — and those facts have already
refuted vault-side conjectures. It owns no rendering, and writes are
whitelisted out, so a destructive Anki operation stays an explicit approved
act rather than a side effect of a query.

Rendering a Problem Note into card HTML has exactly one implementation,
Interest's `AnkiSyncService`, reachable from a shell through
`dart run tool/sync_anki_notes.dart`. A second renderer here was retired on
2026-08-05: it had silently diverged, dropping the hard line breaks Interest
promotes, so the same note produced different cards depending on which tool
touched it last. One current rule, one implementation.

Formation is specialised because authored structures explain different units;
processing is shared because every unit has the same provenance, embedding,
filtering, ranking, note-collapse, and context-expansion contract. Generic token
windows are a last formation fallback, not the default. A logical unit whose
embedding context is still long is covered by token windows and mean-pooled
back to one vector rather than silently truncated. Cheap traversal and embedding
retrieval consume the same full parser record, so they cannot disagree merely
because they parsed `***` separately.

Obsidian context is post-retrieval evidence, not embedding input. The read-only
adapter probes the running app with bare `obsidian`, targets exact `path=`
arguments, and exposes links, backlinks, properties, search, contextual search,
Bases queries, orphans, dead ends, and unresolved links. `neighbour.py` uses the
path-local evidence and reports filesystem fallback per note when configured, so
one transient lookup failure does not discard successful Obsidian contexts.
Neither provider changes vector scores. Repeated `match --file` or `match
--text` arguments share one loaded index, embedding model, link map, and
Obsidian context cache.

The neighbour index is reused by direct matching and recurrence candidate
retrieval. Embedding and lexical scores nominate notes to inspect; they do not
establish identity, relevance, placement, criticism, redundancy, or truth.

```text
external source URLs -> source adapter -> {id, text, url} bundle
                                             |
                         agent + neighbours -> explanatory grouping plan
                                             |
                                             v
                                  source_to_notes.py
                                  staged new or patched existing notes
```

`policy_index.py` performs the analogous structural job for policy: it exposes
the problems stated by well-formed active policy notes and refuses an ambiguous
surface. The agent still explains which policies bear on the present task and
reads those policies in full.

Source adapters recover facts only. `x_posts.py` is one adapter, not the
architecture. `source_to_notes.py` checks a plan's mechanical consequences:
coverage, unique assignment, resolvable and YAML-safe `up:` links, ordinary
Problem Note structure, and exact source text and URLs. Existing-note appends
remain explicit, staged, stale-checked, and append-only. The agent—not a
neighbour score—remains responsible for explanatory identity, grouping,
relations, and conflicts.

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
- `problem_half.py` parses frontmatter and both sides into one structural record.
- `note_chunks.py` forms inspectable retrieval units using the configured
  structural strategy.
- `problem_index.py` creates a disposable map of root-vault Problem Notes.
- `neighbour.py` indexes units, ranks query neighbours or bounded filtered
  note pairs, collapses by note identity, and expands configured context for
  direct matches.
- `obsidian_cli.py` provides bounded read-only Obsidian search, graph,
  properties, Bases, and vault-shape context.
- `note_rename.py` owns the guarded Obsidian note-identity transaction.
- `problem_candidates.py` surfaces recurring, undeveloped, or unwritten
  candidates using criteria from `Candidate Selection.md`.
- `policy_index.py` exposes and structurally validates the active policy surface.
- `anki_query.py` provides bounded read-only AnkiConnect facts without owning rendering.
- `x_posts.py` canonicalises public X/Twitter statuses and recovers complete
  Note Tweets and attached X Articles when structurally present.
- `readera_highlights.py` recovers complete highlights, annotations, and withdrawals
  from ReadEra snapshot backups.
- `highlights_to_notes.py` append-only files recovered ReadEra records into their
  root `collection: Books` entity.
- `video_sources.py` anchors selected spoken passages to caption timestamps.
- `source_to_notes.py` validates an explanatory grouping plan, creates new
  Problem Notes, or applies explicit guarded append-only source blocks.
- `evaluate_retrieval.py` measures retrieval recall against authored wikilinks.
- `adapters.py` is the single install/validation manifest for agent targets and scripts.

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
  --corpus all --side all --unit all --graph configured --k 10
# Query several notes or source passages without repeatedly loading the model and index.
python neighbour.py match --vault "/vault" --file "/vault/a.md" \
  --file "/vault/b.md" --graph configured --json
python neighbour.py match --vault "/vault" --text "source passage one" \
  --text "source passage two" --graph configured --json
python obsidian_cli.py --vault "/vault" context "path/to/note.md"
python problem_candidates.py --vault "/vault" --json

# Inspect first; --apply invokes the guarded rename once.
python note_rename.py "old title.md" "new title" --vault "/vault"
python note_rename.py "old title.md" "new title" --vault "/vault" --apply

# Nominate mutually near root problem identities for explanatory inspection.
python neighbour.py pairs --vault '/vault' --corpus vault --side problem \
  --unit problem_identity --k 50

# Expose the structurally valid active-policy surface.
python policy_index.py --vault "/vault" --json

# Recover external sources, then validate an agent-authored grouping plan.
python x_posts.py --file "/scratch/x-links.txt" --out "/scratch/sources.json"
python source_to_notes.py "/scratch/sources.json" "/scratch/plan.json" \
  --vault "/vault" --stage "/scratch/notes"

# After auditing a stage that contains explicit existing:true operations.
python source_to_notes.py "/scratch/sources.json" "/scratch/plan.json" \
  --vault "/vault" --write --append-existing
```

For source-to-Problem-Note work, query neighbours twice: first with source
content to recover the existing problem situation, then with each drafted
problem to find candidate parents, rivals, and conflicts. Read problem sides
before conjectures. A source belongs in an existing note only when it addresses
almost exactly the same conflict under the same criterion; topic, vocabulary,
parentage, and a high score are insufficient. Otherwise form a new problem and
relate it. Conflict is a candidate problem, not untidiness to erase.

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

Python 3 and the standard library are sufficient for every structural and
source tool. The neighbour subsystem is the sole exception, adding `numpy`,
`torch`, and `transformers` for the embedding model. Obsidian CLI
is optional for application-indexed context; bare `obsidian` is its availability
probe, and configured filesystem fallback keeps vector retrieval usable without
it. `x_posts.py` uses X's public syndication endpoint and the payload's
structural Note Tweet
marker—not text length or appearance—to invoke a status-ID-validated public
long-text fallback. It records the chosen text route and refuses
partial Note Tweet text when that recovery fails. Source fetching therefore
needs network access; the other structural tools operate directly on files.
An attached `/i/article/` URL invokes article metadata recovery that validates
both the status ID and article ID and preserves the exact title and non-empty
text blocks. The adapter refuses partial or mismatched long-form content.


Tests are grouped by independently breakable contract rather than by module:

```bash
python test_note_chunks.py   # Markdown structure and Problem Note boundaries
python test_neighbour.py     # index lifecycle, retrieval, recurrence consumers
python test_sources.py       # source adapters and source-to-note invariants
python test_note_rename.py   # guarded Obsidian rename identity transaction
```

The suites use synthetic fixtures; neighbour tests use a stub embedder and need
no model download or network. After changing copied repository tools, reinstall
and run `doctor.py` for the affected target.
