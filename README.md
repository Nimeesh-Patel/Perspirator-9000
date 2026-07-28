# Perspirator 9000

Perspirator is an agent-neutral research toolkit for an Obsidian vault of
Popperian/Deutschian problem notes. It helps an agent recover relevant context,
traverse a problem graph selectively, and distinguish deterministic facts about
files from explanatory judgments about ideas.

Perspirator is deliberately split in two. The repository owns packaging,
validation, and deterministic structural tools. The vault owns the active
runtime, policies, configuration, research notes, and other semantic authority.

## Architecture

### Runtime and deployment

```text
Perspirator 9000 repository                         (packaging source)
SKILL.md + adapters.py + install.py + doctor.py + structural tools
                         │
                         │ install.py resolves <vault> and <tools-dir>,
                         │ then copies the adapter and toolkit
          ┌──────────────┼────────────────────┐
          │              │                    │
          ▼              ▼                    ▼
 Claude Code          Codex                Custom agent
 ~/.claude/...        ~/.agents/...        --destination ...
 perspirate.md        perspirate/SKILL.md   rendered SKILL.md
          └──────────────┼────────────────────┘
                         │ each adapter is only a locator
                         ▼
       <vault>/memory/perspirator/Bootstrap.md       (canonical bootstrap)
                         │
             ┌───────────┼──────────────────────┐
             │           │                      │
             ▼           ▼                      ▼
  Perspirator.md    reporting and           copied structural
  active runtime    authority duties         toolkit path
             └───────────┼──────────────────────┘
                         ▼
                     agent run
       ┌─────────────────┼────────────────────────┐
       │                 │                        │
       ▼                 ▼                        ▼
 full-vault reads   Basic Memory MCP         structural tools
 all Markdown       optional; memory/ only   deterministic facts
       └─────────────────┼────────────────────────┘
                         ▼
       explanatory judgment, response, approved writes,
       and a run report after substantial traversal/write
```

The adapter contains two resolved paths, not a second copy of Perspirator's
reasoning policy. Every run follows the adapter to `Bootstrap.md`, which loads
the current runtime and policies from the vault. Consequently, editing an
active vault instruction or configuration note takes effect on the next run;
changing repository tools or installed paths requires reinstalling the copied
adapter/toolkit.

Basic Memory and full-vault access are different paths. Basic Memory provides
cross-app recall over `<vault>/memory/` only. Direct filesystem or Obsidian
access exposes the rest of the vault. Neither substitutes for the other when a
task actually needs both bodies of knowledge.

### Internal structural dataflow

```text
                         <vault>/**/*.md
                                │
                    problem_half.split_note()
                     ┌──────────┼─────────────────────┐
                     │          │                     │
                     ▼          ▼                     ▼
          problem_half CLI   note_chunks.py      problem_index.py
          one note/status    side classification problem-note filter
                                │                     ▲
                                │ chunks + helpers    │ split + helpers
             ┌──────────────────┼─────────────────────┤
             │                  │                     │
             ▼                  ▼                     ▼
   note_chunks CLI    problem_candidates.py    problem_index JSON
   chunk records      + Candidate Selection    problem map
                             │                     │
                             ▼                     └──────► agent
                      ranked candidates
                             │
                             └────────────────────────────► agent

   note_chunks.py + Neighbour Retrieval.md
                    │
                    ▼
           neighbour.py index
                    │
                    ▼
   <vault>/.perspirator/neighbours.npz       (derived vectors + provenance)
                    ├──────────► problem_candidates recurrence
                    │
 query text/file ───┼── current vault link graph
                    ▼
           neighbour.py match
                    │
                    ▼
       ranked neighbouring chunks ───────────────────────► agent

   note_chunks.frontmatter_fields() ──► doctor.py ──► validation report
```

There is no hidden daemon and no automatic chain in which one derived result
becomes a conclusion. The scripts share parsing functions, then return
structural evidence to the agent:

- `problem_index.py` does not consume neighbour results. Its JSON is used by
  agent-side ingest/deduplication and connection work.
- `problem_candidates.py` consumes the neighbour index only for recurrence
  retrieval. It combines embedding and lexical candidate signals configured in
  `Candidate Selection.md`; it does not infer that a pair is the same problem.
- `neighbour.py match` and recurrence retrieval in `problem_candidates.py`
  consume the same `.npz` index. Both return proximity candidates for agent
  judgment; neither promotes proximity into a semantic conclusion.
- Similarity, recurrence, relevance, placement, criticism, and identity are not
  interchangeable. The tools produce candidates; the active runtime governs
  the explanatory judgment made from them.

## How a run works

1. The user or agent discovery invokes an installed adapter.
2. The adapter locates `memory/perspirator/Bootstrap.md` and the copied toolkit.
3. The bootstrap requires the active runtime and policies to be loaded, or the
   run stops rather than improvising Perspirator behaviour.
4. The agent recalls current project state, follows selected vault relations,
   and calls structural tools when their facts are useful.
5. The agent interprets those facts in the current problem situation. Scripts
   never decide which conjecture is true, relevant, novel, or well placed.
6. The agent returns the result, makes only authorized writes, and records the
   required run report after substantial traversal or writing.

## Components: inputs, outputs, and consumers

| Component | Main inputs | Output or write | Who consumes it |
|---|---|---|---|
| `SKILL.md` | `{{VAULT_PATH}}`, `{{TOOLS_DIR}}` | Rendered discovery adapter containing the two paths and the bootstrap instruction | Claude Code, Codex, or a custom agent |
| `adapters.py` | Target name, adapter template, vault path, toolkit path | Adapter metadata and rendered adapter text | `install.py` and `doctor.py` |
| `install.py` | Target, vault, destination overrides, repository files | A rendered adapter plus copies of every file in `SCRIPT_NAMES` | Agent discovery and later tool calls |
| `doctor.py` | Vault, target, source template, installed directories | Human-readable checks and exit code; no durable artifact | User, CI, or installer verification |
| `problem_half.py` | One Markdown file; optional `--json` and `--full-on-miss` | Problem side, frontmatter, and structural status on stdout/stderr | Agent; its `split_note()` function is imported by `note_chunks.py` and `problem_index.py` |
| `note_chunks.py` | Vault Markdown; optional corpus/side filters | Paragraph/list/fence chunks with provenance; CLI text or JSON | `problem_candidates.py`, `neighbour.py`, and users; parsing helpers are also imported by `problem_index.py` and `doctor.py` |
| `problem_index.py` | Vault root and exclusions | JSON array of non-`memory/` problem notes, to stdout or `--out` | Agent-side ingest/deduplication and connection work |
| `problem_candidates.py` | Vault, `Candidate Selection.md`, memory chunks, vault link graph | Ranked candidate report or JSON, including the configured draft template | Agent or human deciding whether to create a problem note |
| `neighbour.py index` | Vault chunks and `Neighbour Retrieval.md` | Disposable `.npz` containing vectors, chunk IDs, metadata, and a freshness header | `neighbour.py match` and recurrence retrieval in `problem_candidates.py` |
| `neighbour.py match` | Query text/file/stdin, `.npz`, filters, current link graph | Ranked chunk records as text or JSON; refreshes the index unless `--no-refresh` | Agent making a semantic judgment |
| `x_posts.py` | Arbitrary X/Twitter status URLs from arguments, files, or stdin | Canonical, ID-deduplicated source bundle with post text, parent/quote/media context, and possible repeated-text groups | Agent forming an explanatory grouping plan |
| `source_to_notes.py` | Any normalized source bundle plus an agent-authored grouping plan | Validated, no-overwrite Problem Notes staged outside the vault or written to its root | Agent after neighbour-assisted problem formation and write authorization |
| `test_note_chunks.py` | Synthetic Markdown fixtures | Pass/fail report and exit code | Development and CI |
| `test_neighbour.py` | Synthetic vault and stub embedder | Pass/fail report and exit code; no model or network | Development and CI |
| `test_x_posts.py` | Synthetic X syndication fixtures; no network | URL/token/parsing/deduplication pass/fail report | Development and CI |
| `test_source_to_notes.py` | Synthetic source bundles, plans, and vaults | Coverage, link, rendering, and no-overwrite pass/fail report | Development and CI |
| `test_problem_candidates.py` | Synthetic problem statements and neighbour vectors | Hybrid recurrence and signal-separation pass/fail report | Development and CI |

`problem_candidates.py` is also part of the copied toolkit even though it is not
in the abbreviated `problem_*.py` name. Adding a first-class agent is normally
one row in `adapters.py`; the installer and doctor both read that same table.

## Core data contracts

### Problem half

With `--json`, `problem_half.py` emits one object:

```text
{
  path,
  status: problem-note | empty-problem | no-separator |
          missing-file | unreadable,
  has_separator,
  frontmatter,
  problem,
  body?,       # only --full-on-miss on a no-separator note
  error?       # missing or unreadable file
}
```

A line exactly equal to `***` separates the problem above from the conjecture
below. This is the sole owner of that split. A readable non-problem note is a
structural finding, not an execution error.

### Chunk

`note_chunks.py` turns Markdown into paragraph-sized records:

```text
{
  note, stem, heading[], start, end, text,
  side: problem | answer | none,
  corpus: memory | vault,
  links[]
}
```

Offsets point back into the normalized file text. Multi-line list items and
fenced blocks stay whole. The parser reports structure only: it does not assign
importance, recurrence, candidacy, or meaning.

### Problem index

`problem_index.py` emits one record per note whose body contains the `***`
separator:

```text
{
  name, path, problem, category, up[], links[], stub
}
```

The default scan excludes Obsidian internals, attachments, trash, and
`memory/`. The output is a current map of problem notes, not a database. If it
disagrees with Markdown, regenerate it.

### Candidate result

`problem_candidates.py --json` emits the enabled signals, exemptions, draft
template, and candidate records shaped roughly as:

```text
{
  signal: recurrence,
  embedding_score,
  lexical_score,
  matched_by: [embedding | lexical],
  where[], text, also
}

or, for non-recurrence structural signals:

{
  signal: hub-stub | never-written,
  score,
  where[], text, also?
}
```

`recurrence` compares statements found under problem-like headings in
`memory/` through the shared neighbour index and an independent lexical signal.
`matched_by` says which threshold nominated the pair; it is not a claim that the
same explanatory problem recurs. `hub-stub` finds a small undeveloped note with
many referrers, and `never-written` finds repeated wikilink targets with no
file. Thresholds and exemptions come from the vault configuration note.

### Neighbour index and match

The `.npz` has four top-level arrays:

```text
vectors   float32 [chunk, embedding-dimension]
ids       JSON list of stable chunk IDs
meta      JSON list of chunk provenance and text
header    JSON object: model, index shape, dimensions, counts,
          per-note mtime/size stamps, build time
```

A JSON match contains `query_note`, the index `header`, and results carrying
`rank`, ordinal `score`, `note`, `heading`, `side`, `corpus`, `already_links`,
`shares_referrers`, and `snippet`. A score is proximity, not confidence or
correctness.

The index refresh is incremental: unchanged note vectors are reused, changed
chunks are re-embedded, and deleted notes disappear. A model change or a change
to indexed corpora, sides, or exemptions requires `index --rebuild`; vectors
from incompatible configurations are never silently mixed.

## Authority and data lifetime

| Thing | Status | Consequence |
|---|---|---|
| Vault Markdown and problem notes | Authoritative research material | Derived tools must yield when they disagree with the files |
| `memory/perspirator/Bootstrap.md` | Canonical loading and authority contract | Every adapter points here; a missing/inactive runtime stops the run |
| `memory/perspirator/Perspirator.md` and active policies | Canonical behaviour and reasoning policy | Repository summaries cannot override them |
| `Candidate Selection.md` / `Neighbour Retrieval.md` | Authoritative configuration for those mechanisms | Editing the note changes the next tool run or index build |
| Repository Python and `SKILL.md` | Canonical packaging and deterministic mechanism | Reinstall after changing copied repository files |
| Installed adapter and toolkit | Generated deployment copies | `doctor.py` detects drift from repository source |
| Problem-index JSON | Derived and disposable | Generate into scratch space; never edit it as authority |
| `.perspirator/neighbours.npz` | Derived and disposable | `neighbour.py` refreshes or rebuilds it from Markdown |
| CLI match/candidate output | Transient evidence | The agent must interpret it before any write or conclusion |
| `memory/perspirator/runs/` reports | Durable audit of substantial runs | Shared across applications through the memory layer |

Repository changes are tracked in Git. Vault-note history belongs to the vault's
sync/history mechanism. A Git clone reproduces the packaging source and
locator template; it does not reproduce the configured vault runtime or the
research corpus.

## Install and validate

```bash
python install.py --target All --vault "/path/to/vault"
python doctor.py  --target All --vault "/path/to/vault"
```

| Target | Default destination | Invocation |
|---|---|---|
| `ClaudeCode` (default) | `~/.claude/commands/perspirate.md` | `/perspirate` |
| `Codex` | `~/.agents/skills/perspirate/SKILL.md` | Invoke the `perspirate` skill by name or matching request |
| `All` | Both first-class destinations | Either of the above |
| `Custom` | `--destination` is required | Load the rendered `SKILL.md` in the agent |

`--claude-dir`, `--codex-dir`, and `--destination` override install locations;
`doctor.py` accepts the corresponding directories plus `--custom-dir`.
Installation replaces only Perspirator's generated adapter and toolkit copies
and leaves unrelated files alone.

No reinstall is needed after editing the runtime, policies, candidate rules, or
neighbour configuration in the same vault. Reinstall after moving the vault or
toolkit, changing `SKILL.md`, or pulling changes to scripts that are copied into
agent directories.

## Use the structural tools

```bash
# Read one problem side without reading the conjecture first.
python problem_half.py "/path/to/note.md" --json

# Inspect normalized structural chunks.
python note_chunks.py "/path/to/vault" --corpus vault --side problem --json

# Build a disposable map for ingest/deduplication or connection work.
python problem_index.py "/path/to/vault" --out "/path/to/scratch/problems.json"

# Rank material that may deserve a problem note.
python problem_candidates.py --vault "/path/to/vault" --json

# Build or update the embedding index, then query it.
python neighbour.py index --vault "/path/to/vault"
python neighbour.py match --vault "/path/to/vault" \
  --file "/path/to/vault/some note.md" --corpus vault --k 10 --json
python neighbour.py match --vault "/path/to/vault" \
  --text "a loose formulation" --corpus all --side all

# A source adapter recovers facts into a source-neutral bundle.
python x_posts.py --file "/path/to/x-links.txt" --out "/path/to/scratch/sources.json"

# After explanatory grouping, validate coverage and stage ordinary notes.
python source_to_notes.py "/path/to/scratch/sources.json" \
  "/path/to/scratch/plan.json" --vault "/path/to/vault" \
  --stage "/path/to/scratch/notes"
```

### From external sources to a problem web

The durable pipeline has three boundaries:

1. a source adapter recovers stable identity, exact content, URL, and available
   context without making thematic judgments;
2. the agent uses the active runtime and two neighbour passes to explain the
   problem situation and produce a grouping plan;
3. `source_to_notes.py` checks the plan's mechanical invariants and renders
   ordinary notes without overwriting existing files.

`x_posts.py` is only one source adapter. It removes repeated status IDs and
reports possible repeated text while keeping every distinct post. Another
platform needs another adapter into the same `{id, text, url}` source bundle,
not another note-creation implementation.

Query `neighbour.py` first with source content to recover the existing problem
situation, then with each drafted problem to find candidate parents, related
problems, rival formulations, and conflicts. Read surfaced problem sides before
their conjectures. Group sources only when they contribute to the same
explanatory problem. A conflict between sources or between a source and the
vault is itself a candidate problem; preserving it is part of coherence, not a
failure of tidiness. Neighbour scores nominate a frontier and never decide a
relationship or truth.

`source_to_notes.py` then enforces what can be decided structurally: every
source is assigned exactly once unless omission is explicitly allowed; no
source is assigned to two notes; every planned `up:` target resolves when a
vault is supplied; all exact source text and links appear on the idea side; and
no existing note is overwritten. It does not invent a problem, group sources,
or choose links. Those are explanatory judgments expressed in the plan.

This does not supersede `problem_candidates.py`. That tool answers a different
question: which recurring or neglected knowledge already inside `memory/` may
deserve a Problem Note. Its configured draft form remains useful; its unused
code-level renderer was removed because rendering an external source plan now
has one owner. Its recurrence signal now consumes the shared neighbour index
and keeps lexical overlap only as an explicitly separate retrieval signal;
neither score is treated as explanatory identity.

Neighbour queries also accept `--stdin`, `--folder`, `--side`, `--index`, and
`--no-refresh`. `--corpus all` and `--side all` explicitly mean no filter. The
mechanism is local-first: it uses a complete cached Hugging Face model without
a network request, falls back to downloading on cache miss, and stops with a
bounded error if neither is available.

## Vault-owned configuration

| Vault note | What it controls |
|---|---|
| `memory/perspirator/Bootstrap.md` | Runtime loading, tool discovery, reporting duty, and authority |
| `memory/perspirator/Perspirator.md` | Active research behaviour and explanatory method |
| `memory/policies/Policy Loader.md` | Which policies are active for a run |
| `memory/perspirator/Candidate Selection.md` | Candidate signals, thresholds, exemptions, and draft form |
| `memory/perspirator/Neighbour Retrieval.md` | Embedding model, indexed corpora/sides, exclusions, and index path |
| `memory/perspirator/runs/README.md` | Required structure of substantial-run reports |

The code reads these notes rather than duplicating their editable choices.
`doctor.py` verifies that required configuration notes exist and are active.

## Capabilities and boundaries

| Capability | What it enables | Universal requirement? |
|---|---|---|
| Basic Memory MCP | Cross-app recall and shared working notes under `memory/` | No; direct `memory/` access can substitute |
| Direct `memory/` access | Filesystem fallback for the same working set | No; Basic Memory MCP can substitute |
| Full-vault filesystem | Problem-note reads and structural traversal | Required only when the task needs the wider vault |
| Obsidian CLI | Search context, backlinks, properties, and CLI writes | No; it requires Obsidian running when used |
| Structural scripts | Deterministic note structure and derived retrieval artifacts | Required only when those facts are needed |
| Write access | Approved note changes and required run reports | Required only for the relevant write |

All structural scripts operate without Obsidian. The vault tools read Markdown
directly; `x_posts.py` reads X's public syndication endpoint and therefore
requires network access. Python 3 is sufficient for every tool except the neighbour subsystem:
`neighbour.py` imports `numpy`, `torch`, and `transformers` on its embedding
paths, and recurrence retrieval in `problem_candidates.py` reuses that module
and index rather than implementing a second ranker.

## Development and verification

```bash
python -m py_compile adapters.py install.py doctor.py problem_half.py \
  note_chunks.py problem_index.py problem_candidates.py neighbour.py \
  source_to_notes.py x_posts.py
python test_note_chunks.py
python test_neighbour.py
python test_problem_candidates.py
python test_source_to_notes.py
python test_x_posts.py
python doctor.py --target All --vault "/path/to/vault"
```

The tests use synthetic fixtures. `test_neighbour.py` supplies a stub embedder,
so it validates freshness, configuration changes, and local-first model loading
without reading the real vault, downloading a model, or requiring network
access.

`doctor.py` validates the active runtime/configuration notes, memory invariants,
repository scripts, rendered adapters, installed script bytes, and
cross-adapter consistency. `Auto` validates the first-class adapters it can
actually discover; an explicit target validates only that target.
