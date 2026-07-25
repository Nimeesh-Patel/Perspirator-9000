# Agent project instructions

Read `README.md` for the packaging model. Perspirator's active behaviour is
defined only by the configured vault runtime at
`memory/perspirator/Perspirator.md`, and the bootstrap contract every agent
follows is the vault note `memory/perspirator/Bootstrap.md`. This repository
owns packaging and validation, not instruction text.

Install with `python install.py --target <ClaudeCode|Codex|All|Custom>`;
`Custom` also needs `--destination`. The adapter table lives in `adapters.py`,
so adding an agent is one row there.

Python 3 is required for the structural tools. Obsidian CLI availability is a
capability, not a universal installation prerequisite. Run `doctor.py` with
the target that was installed.

After changing repository-owned files, validate the affected targets, commit
only the intended repository changes, and push the commit to the configured
remote. If validation or push fails, report the exact blocker instead of
leaving the repository's publication state ambiguous.
