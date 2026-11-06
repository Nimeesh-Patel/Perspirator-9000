# Agent project instructions

Read `README.md` for the architecture and packaging model. Perspirator's active
behaviour is defined by the configured vault runtime at
`memory/perspirator/Perspirator.md`; every agent enters through
`memory/perspirator/Bootstrap.md`.

Keep executable mechanisms, adapters, invariants, and structural validation in
repository code. Keep criticisable theory, semantic instructions, criteria,
configuration, and working accounts in vault Markdown so they can change on
the next run. Code still embodies theory in its mechanisms.
Do not duplicate a current semantic rule in code and Markdown: code should load
and validate the Markdown-owned rule.

Install with `python install.py --target <ClaudeCode|Codex|All|Custom> --vault
<path>` or set `PERSPIRATOR_VAULT`; `Custom` also needs `--destination`. The
adapter table lives in `adapters.py`, so adding a first-class host is one
declaration there.

Python 3 is required for the structural tools. `neighbour.py` additionally
needs numpy, torch, and transformers, which nothing else imports. Tests are
grouped by contract: use `python -m unittest <affected_module>` for an affected
surface or the full discovery command in README.md. Obsidian CLI availability is a
capability, not a universal installation prerequisite. Run `doctor.py` with
the target that was installed.

After changing repository-owned files, validate the affected targets, commit
only the intended repository changes, and push the commit to the configured
remote. If validation or push fails, report the exact blocker instead of
leaving the repository's publication state ambiguous.
