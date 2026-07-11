#!/usr/bin/env bash
# Target-aware Perspirator installer for POSIX shells, Git Bash, and WSL.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd -P)"
TARGET="ClaudeCode"
TARGET_EXPLICIT=0
VAULT_DIR="$HOME/nimeesh vault"
DESTINATION=""
CLAUDE_DIR="$HOME/.claude/commands"
CODEX_DIR="$HOME/.agents/skills/perspirate"

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]
  --target ClaudeCode|Codex|All|Custom
  --vault PATH
  --destination PATH       Required for Custom; overrides a single target.
  --claude-dir PATH        Override Claude Code destination (useful for tests).
  --codex-dir PATH         Override Codex destination (useful for tests).

Legacy: ./install.sh [commands-dir] [vault-root]
This retains the historical Claude Code target.
EOF
}

# Backward-compatible positional form.
if [[ $# -gt 0 && "$1" != -* ]]; then
  CLAUDE_DIR="$1"
  shift
  if [[ $# -gt 0 ]]; then VAULT_DIR="$1"; shift; fi
  if [[ $# -gt 0 ]]; then usage >&2; exit 2; fi
else
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --target) TARGET="$2"; TARGET_EXPLICIT=1; shift 2 ;;
      --vault) VAULT_DIR="$2"; shift 2 ;;
      --destination) DESTINATION="$2"; shift 2 ;;
      --claude-dir) CLAUDE_DIR="$2"; shift 2 ;;
      --codex-dir) CODEX_DIR="$2"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) echo "error: unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
  done
fi

case "$TARGET" in
  ClaudeCode|claudecode|Claude|claude) TARGET="ClaudeCode" ;;
  Codex|codex) TARGET="Codex" ;;
  All|all) TARGET="All" ;;
  Custom|custom) TARGET="Custom" ;;
  *) echo "error: invalid target: $TARGET" >&2; exit 2 ;;
esac

if [[ "$TARGET" == "Custom" && -z "$DESTINATION" ]]; then
  echo "error: Custom target requires --destination" >&2; exit 2
fi
if [[ "$TARGET" == "All" && -n "$DESTINATION" ]]; then
  echo "error: --destination cannot be used with All" >&2; exit 2
fi

if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
else echo "error: Python 3 not found on PATH" >&2; exit 1
fi

absolute_path() {
  local path="$1"
  path="${path/#\~/$HOME}"
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -am "$path"
  elif [[ "$path" = /* ]]; then
    printf '%s\n' "$path"
  else
    printf '%s/%s\n' "$(pwd -P)" "$path"
  fi
}

VAULT_DIR="$(absolute_path "$VAULT_DIR")"
CLAUDE_DIR="$(absolute_path "$CLAUDE_DIR")"
CODEX_DIR="$(absolute_path "$CODEX_DIR")"
if [[ -n "$DESTINATION" ]]; then DESTINATION="$(absolute_path "$DESTINATION")"; fi

names=()
dirs=()
files=()
case "$TARGET" in
  ClaudeCode)
    names+=("Claude Code"); dirs+=("${DESTINATION:-$CLAUDE_DIR}"); files+=("perspirate.md") ;;
  Codex)
    names+=("Codex"); dirs+=("${DESTINATION:-$CODEX_DIR}"); files+=("SKILL.md") ;;
  All)
    names+=("Claude Code" "Codex"); dirs+=("$CLAUDE_DIR" "$CODEX_DIR"); files+=("perspirate.md" "SKILL.md") ;;
  Custom)
    names+=("Custom"); dirs+=("$DESTINATION"); files+=("SKILL.md") ;;
esac

echo "Perspirator installer"
if [[ $TARGET_EXPLICIT -eq 0 && "$TARGET" == "ClaudeCode" ]]; then
  echo "  target: ClaudeCode (backward-compatible default)"
else
  echo "  target: $TARGET"
fi
echo "  vault:  $VAULT_DIR"
for i in "${!names[@]}"; do echo "  ${names[$i]}: ${dirs[$i]}/${files[$i]}"; done
echo

for i in "${!names[@]}"; do
  dir="${dirs[$i]}"
  mkdir -p "$dir"
  cp "$REPO_DIR/problem_half.py" "$dir/problem_half.py"
  cp "$REPO_DIR/problem_index.py" "$dir/problem_index.py"
  cp "$REPO_DIR/doctor.py" "$dir/doctor.py"
  "$PY" - "$REPO_DIR/SKILL.md" "$dir/${files[$i]}" "$dir" "$VAULT_DIR" <<'PY'
import sys
src, dst, tools_dir, vault = sys.argv[1:]
text = open(src, encoding="utf-8").read()
text = text.replace("{{TOOLS_DIR}}", tools_dir.replace("\\", "/"))
text = text.replace("{{VAULT_PATH}}", vault.replace("\\", "/"))
with open(dst, "w", encoding="utf-8", newline="\n") as handle:
    handle.write(text)
PY
done

echo "Installed without removing any existing unrelated files."
for i in "${!names[@]}"; do
  name="${names[$i]}"
  dir="${dirs[$i]}"
  case "$name" in
    "Claude Code")
      echo "  Claude Code: invoke /perspirate"
      printf '  Validate Claude Code: %s "%s/doctor.py" --target ClaudeCode --vault "%s" --claude-dir "%s"\n' "$PY" "$REPO_DIR" "$VAULT_DIR" "$dir" ;;
    "Codex")
      echo "  Codex: invoke the perspirate skill by name or request"
      printf '  Validate Codex: %s "%s/doctor.py" --target Codex --vault "%s" --codex-dir "%s"\n' "$PY" "$REPO_DIR" "$VAULT_DIR" "$dir" ;;
    "Custom")
      echo "  Custom: load $dir/SKILL.md in the agent"
      printf '  Validate Custom: %s "%s/doctor.py" --target Custom --vault "%s" --custom-dir "%s"\n' "$PY" "$REPO_DIR" "$VAULT_DIR" "$dir" ;;
  esac
done
if [[ "$TARGET" == "All" ]]; then
  printf '  Validate both: %s "%s/doctor.py" --target All --vault "%s" --claude-dir "%s" --codex-dir "%s"\n' "$PY" "$REPO_DIR" "$VAULT_DIR" "${dirs[0]}" "${dirs[1]}"
fi
