#!/usr/bin/env bash
# Perspirator 9000 installer (POSIX / Git-Bash / WSL / macOS / Linux).
#
# Deploys the skill so an agent can run it:
#   - copies problem_half.py and problem_index.py into the commands dir
#   - renders SKILL.md -> <commands-dir>/perspirate.md with the real path
#     substituted for the {{COMMANDS_DIR}} placeholder
#
# Usage:
#   ./install.sh                 # installs to ~/.claude/commands (Claude Code)
#   ./install.sh /custom/dir     # installs to a custom commands dir
#
# Idempotent: re-running overwrites the deployed copies only.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
COMMANDS_DIR="${1:-$HOME/.claude/commands}"

# Find a Python 3 interpreter (python3 on most systems, python on Windows).
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
else echo "error: Python 3 not found on PATH" >&2; exit 1
fi

echo "Perspirator 9000 -> installing into: $COMMANDS_DIR"
mkdir -p "$COMMANDS_DIR"

# 1. Helper scripts (verbatim).
cp "$REPO_DIR/problem_half.py"  "$COMMANDS_DIR/problem_half.py"
cp "$REPO_DIR/problem_index.py" "$COMMANDS_DIR/problem_index.py"

# 2. Skill file with the placeholder resolved to the real absolute path.
#    Use python for a safe substitution (no sed escaping headaches).
"$PY" - "$REPO_DIR/SKILL.md" "$COMMANDS_DIR/perspirate.md" "$COMMANDS_DIR" <<'PY'
import sys
src, dst, cmd = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(src, encoding="utf-8").read()
text = text.replace("{{COMMANDS_DIR}}", cmd.replace("\\", "/"))
open(dst, "w", encoding="utf-8").write(text)
print("  rendered:", dst)
PY

echo "  copied:   problem_half.py, problem_index.py"
echo
echo "Done. Next:"
echo "  1. Enable the Obsidian CLI (Obsidian 1.12+): Settings -> General ->"
echo "     Command line interface, and add 'obsidian' to your PATH."
echo "  2. Keep Obsidian running with your vault open."
echo "  3. In Claude Code, invoke: /perspirate"
echo "     (other agents: point them at SKILL.md in this repo)."
