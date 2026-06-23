<#
  Perspirator 9000 installer (Windows / PowerShell).

  Deploys the skill so an agent can run it:
    - copies problem_half.py and problem_index.py into the commands dir
    - renders SKILL.md -> <commands-dir>\perspirate.md with the real path
      substituted for the {{COMMANDS_DIR}} placeholder

  Usage:
    .\install.ps1                      # installs to ~\.claude\commands (Claude Code)
    .\install.ps1 -CommandsDir D:\dir  # installs to a custom commands dir

  Idempotent: re-running overwrites the deployed copies only.
#>
param(
  [string]$CommandsDir = (Join-Path $HOME ".claude\commands")
)

$ErrorActionPreference = "Stop"
$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "Perspirator 9000 -> installing into: $CommandsDir"
New-Item -ItemType Directory -Force -Path $CommandsDir | Out-Null

# 1. Helper scripts (verbatim).
Copy-Item (Join-Path $RepoDir "problem_half.py")  (Join-Path $CommandsDir "problem_half.py")  -Force
Copy-Item (Join-Path $RepoDir "problem_index.py") (Join-Path $CommandsDir "problem_index.py") -Force

# 2. Skill file with the placeholder resolved to the real absolute path.
$skill = Get-Content -Raw -Encoding UTF8 (Join-Path $RepoDir "SKILL.md")
$skill = $skill.Replace("{{COMMANDS_DIR}}", ($CommandsDir -replace '\\','/'))
$dst = Join-Path $CommandsDir "perspirate.md"
[System.IO.File]::WriteAllText($dst, $skill, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "  copied:   problem_half.py, problem_index.py"
Write-Host "  rendered: $dst"
Write-Host ""
Write-Host "Done. Next:"
Write-Host "  1. Enable the Obsidian CLI (Obsidian 1.12+): Settings -> General ->"
Write-Host "     Command line interface, and add 'obsidian' to your PATH."
Write-Host "  2. Keep Obsidian running with your vault open."
Write-Host "  3. In Claude Code, invoke: /perspirate"
Write-Host "     (other agents: point them at SKILL.md in this repo)."
