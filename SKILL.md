---
name: perspirate
description: >-
  Bootstrap for Perspirator, an agent-neutral research toolkit for an
  Obsidian vault of problem notes. Loads the canonical vault runtime at
  the start of every task; reasoning policy lives there, not here.
---

# Perspirator bootstrap

This is the one canonical bootstrap source. It contains no reasoning policy.
Installers render this same source into each agent's discovery format.

Path placeholders: `{{VAULT_PATH}}` is the Obsidian vault root and
`{{TOOLS_DIR}}` contains the structural scripts. When reading this file
directly from the repository, resolve the vault root explicitly (with
`obsidian vault info=path` when that capability is available) and treat the
repository root as `{{TOOLS_DIR}}`.

## 1. Locate the canonical runtime

```
{{VAULT_PATH}}/memory/perspirator/Perspirator.md
```

If the configured path is missing and the Obsidian CLI is available, resolve
the vault root with `obsidian vault info=path` and look for
`memory/perspirator/Perspirator.md` below it.

## 2. Load it or refuse

Read the whole file at the beginning of every Perspirator task. Check that its
frontmatter says `status: active` and note its `version`. Its body is the
active operating instruction and supersedes remembered or repository-level
summaries of Perspirator behaviour.

**If the runtime cannot be found or read, STOP.** Report the expected path and
do not improvise Perspirator behaviour from memory or from this bootstrap.

## 3. Structural tools

The shared Python 3 scripts in `{{TOOLS_DIR}}` read the filesystem directly
and do not require Obsidian to be running:

- `problem_half.py "<note path>"` returns frontmatter and the problem side.
  `--json` reports `problem-note`, `empty-problem`, `no-separator`,
  `missing-file`, or `unreadable` using the stable machine contract.
- `problem_index.py "<vault-root>" --out "<scratch>.json"` creates a derived,
  disposable index outside the vault. Regenerate it if it disagrees with the
  vault.
- `doctor.py` validates the runtime, shared scripts, and the requested agent
  adapter. Use `python doctor.py --help` for target options.

Obsidian CLI commands can add search, backlink, property, and write
capabilities when the desktop application and CLI are available. The runtime's
capability preflight decides whether a particular run is full, degraded, or
refused. Structural tools establish facts about files; the runtime assigns
semantic judgement to the agent.

## 4. Run report

After a substantial traversal or write, write the report required by the
runtime and `runs/README.md` to:

```
{{VAULT_PATH}}/memory/perspirator/runs/YYYY-MM-DD-<slug>.md
```

## 5. Authority rule

- A direct edit by Nimeesh to `Perspirator.md` is approved and immediately
  active.
- An agent must not edit `Perspirator.md` on its own initiative. Proposed
  changes go in `{{VAULT_PATH}}/memory/perspirator/proposals/`.
- An agent may edit the runtime only when Nimeesh explicitly asks it to apply
  a change; then it must bump the version and update
  `{{VAULT_PATH}}/memory/perspirator/CHANGELOG.md`.
