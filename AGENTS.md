# Agent project instructions

Read `EPISTEMIC_METHOD.md` for the shared epistemic method and `README.md` for
the packaging model. Perspirator's active behaviour is defined only by the
configured vault runtime at `memory/perspirator/Perspirator.md`.

Use the installer target matching the current environment:

- Claude Code: `ClaudeCode` renders `~/.claude/commands/perspirate.md`.
- Codex: `Codex` renders `~/.agents/skills/perspirate/SKILL.md`.
- Both: `All` renders both adapters from root `SKILL.md`.
- Unsupported agent: `Custom` renders a generic `SKILL.md` into an explicit
  destination, or read root `SKILL.md` directly and resolve its two path
  placeholders.

Python 3 is required for the structural tools. Obsidian CLI availability is a
capability, not a universal installation prerequisite. Run `doctor.py` with
the target that was installed.
