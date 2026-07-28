# Agent project instructions

Read `README.md` for the architecture and packaging model. Perspirator's active
behaviour is defined by the configured vault runtime at
`memory/perspirator/Perspirator.md`; every agent enters through
`memory/perspirator/Bootstrap.md`.

Keep executable mechanisms, adapters, invariants, and structural validation in
repository code. Keep criticisable theory, semantic instructions, criteria,
configuration, and run reports in vault Markdown so they can change on the next
run. This is a feedback-speed boundary, not a claim that code is theory-free.
Do not duplicate a current semantic rule in code and Markdown: code should load
and validate the Markdown-owned rule.

Install with `python install.py --target <ClaudeCode|Codex|All|Custom>`;
`Custom` also needs `--destination`. The adapter table lives in `adapters.py`,
so adding an agent is one row there.

Python 3 is required for the structural tools. `neighbour.py` additionally
needs numpy, torch, and transformers, which nothing else imports. Tests are
grouped by contract: run `test_note_chunks.py`, `test_neighbour.py`, and
`test_sources.py` for affected surfaces. Obsidian CLI availability is a
capability, not a universal installation prerequisite. Run `doctor.py` with
the target that was installed.

After changing repository-owned files, validate the affected targets, commit
only the intended repository changes, and push the commit to the configured
remote. If validation or push fails, report the exact blocker instead of
leaving the repository's publication state ambiguous.
