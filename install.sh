#!/usr/bin/env bash
# Perspirator 9000 installer (POSIX / Git-Bash / WSL / macOS / Linux).
#
# Deploys the bootstrap so an agent can run it:
#   - copies problem_half.py, problem_index.py and doctor.py into the
#     commands dir
#   - renders SKILL.md -> <commands-dir>/perspirate.md with real paths
#     substituted for the {{COMMANDS_DIR}} and {{VAULT_PATH}} placeholders
#
# The semantic runtime is NOT deployed by this script: it lives in the
# vault at <vault>/memory/perspirator/Perspirator.md and is read at the
# start of every run.
#
# Usage:
#   ./install.sh                          # installs to ~/.claude/commands
#   ./install.sh /custom/dir              # custom commands dir
#   ./install.sh /custom/dir /vault/root  # non-default Obsidian vault root
#
# Idempotent: re-running overwrites the deployed copies only.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
COMMANDS_DIR="${1:-$HOME/.claude/commands}"
VAULT_DIR="${2:-$HOME/nimeesh vault}"

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
cp "$REPO_DIR/doctor.py"        "$COMMANDS_DIR/doctor.py"

# 2. Bootstrap with the placeholders resolved to real absolute paths.
#    Use python for a safe substitution (no sed escaping headaches).
"$PY" - "$REPO_DIR/SKILL.md" "$COMMANDS_DIR/perspirate.md" "$COMMANDS_DIR" "$VAULT_DIR" <<'PY'
import sys
src, dst, cmd, vault = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
text = open(src, encoding="utf-8").read()
text = text.replace("{{COMMANDS_DIR}}", cmd.replace("\\", "/"))
text = text.replace("{{VAULT_PATH}}", vault.replace("\\", "/"))
open(dst, "w", encoding="utf-8").write(text)
print("  rendered:", dst)
PY

echo "  copied:   problem_half.py, problem_index.py, doctor.py"
echo "  runtime:  $VAULT_DIR/memory/perspirator/Perspirator.md (canonical, in the vault)"
echo
echo "Done. Next:"
echo "  1. Enable the Obsidian CLI (Obsidian 1.12+): Settings -> General ->"
echo "     Command line interface, and add 'obsidian' to your PATH."
echo "  2. Keep Obsidian running with your vault open."
echo "  3. In Claude Code, invoke: /perspirate"
echo "     (other agents: point them at SKILL.md in this repo)."
