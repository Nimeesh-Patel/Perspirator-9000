---
name: perspirate
description: >-
  Bootstrap for Perspirator: the agentic interface to Nimeesh's Obsidian
  vault of Popperian/Deutschian problem notes. Loads the canonical
  runtime from the vault (memory/perspirator/Perspirator.md) at the start
  of every task and follows it — implications, assumptions, conflicts,
  ingest/connect/write-back, and basic-memory bridge modes are all
  defined there, not here.
---

# Perspirator bootstrap

This file is deliberately small and stable. It contains **no reasoning
policy**. The active operating instructions live in the vault, are
editable there by Nimeesh, and are loaded fresh at the start of every
Perspirator task — an edit to the runtime changes the next run with no
redeployment.

(Path placeholders: `{{VAULT_PATH}}` is the Obsidian vault root and
`{{COMMANDS_DIR}}` is where the helper scripts live. The installers
substitute real absolute paths; if you are reading this file raw from the
repo, resolve the vault root via `obsidian vault info=path` and treat
`{{COMMANDS_DIR}}` as this repo's directory.)

## 1. Locate the canonical runtime

```
{{VAULT_PATH}}/memory/perspirator/Perspirator.md
```

If that exact path is missing, resolve the vault root with
`obsidian vault info=path` and look for
`memory/perspirator/Perspirator.md` under it.

## 2. Load it — or refuse

Read the whole file at the beginning of every Perspirator task. Check its
frontmatter says `status: active` and note its `version`. Treat its body
as the active operating instructions for the task — it supersedes
anything you remember about how Perspirator used to work.

**If the runtime cannot be found or read, STOP.** Tell the user the
canonical runtime is unavailable, give the expected path, and do not
improvise Perspirator behaviour from memory or from this file.

## 3. Structural tools

Helper scripts (Python 3, no dependencies, in `{{COMMANDS_DIR}}`; they
read the filesystem directly and do not need Obsidian running):

- `problem_half.py "<note path>"` — frontmatter + problem side (above the
  first `***`). `--json` gives the stable machine contract: a `status`
  of `problem-note`, `empty-problem`, `no-separator`, `missing-file`, or
  `unreadable`, plus `frontmatter` and `problem`. Text mode prints the
  same status on stderr; `--full-on-miss` includes the whole body of
  non-problem notes. Statuses are structural facts only — relevance
  stays your judgement.
- `problem_index.py "<vault-root>" --out "<scratch>.json"` — derived,
  disposable index of all problem notes (name, path, problem side,
  category, `up:`, links, stub flag). Write it OUTSIDE the vault; if it
  disagrees with the vault, regenerate it.
- `doctor.py` — validates this installation (runtime present and active,
  dirs, scripts, writability). Run it when anything seems miswired.

Obsidian CLI (desktop, Obsidian running; run `obsidian help` first —
flags change): `search:context`, `backlinks`, `links`, `read`, `file`,
`files`, `properties`, `create`, `append`, `vault info=path`.

These tools answer structural questions only. The runtime defines what
they may decide and what stays semantic judgement.

## 4. Run report

After any substantial traversal or write, write a run report to

```
{{VAULT_PATH}}/memory/perspirator/runs/YYYY-MM-DD-<slug>.md
```

with the contents the runtime and `runs/README.md` specify. No
substantial run finishes without one.

## 5. Authority rule

- A direct edit by Nimeesh to `Perspirator.md` is approved and
  immediately active.
- You must NOT edit `Perspirator.md` on your own initiative. If you think
  the policy should improve, write a proposal file to
  `{{VAULT_PATH}}/memory/perspirator/proposals/` (format in its README).
- You may edit the runtime only when Nimeesh explicitly asks you to
  implement or apply a change — then bump its `version` and record the
  change in `{{VAULT_PATH}}/memory/perspirator/CHANGELOG.md`.
