<#
  Target-aware Perspirator installer for PowerShell.

  The root SKILL.md is the canonical bootstrap source. Each selected target
  receives a rendered discovery adapter plus the shared structural scripts.

  Examples:
    .\install.ps1 -Target ClaudeCode
    .\install.ps1 -Target Codex
    .\install.ps1 -Target All -VaultDir "D:\My Vault"
    .\install.ps1 -Target Custom -Destination "D:\agent prompts"

  Omitting -Target retains the historical Claude Code default, but the
  selected target and destination are printed before any files are written.
  The legacy -CommandsDir option remains a Claude Code destination override.
#>
param(
  [ValidateSet("ClaudeCode", "Codex", "All", "Custom")]
  [string]$Target = "ClaudeCode",
  [string]$VaultDir = (Join-Path $HOME "nimeesh vault"),
  [string]$Destination,
  [string]$CommandsDir,
  [string]$ClaudeDir = (Join-Path $HOME ".claude\commands"),
  [string]$CodexDir = (Join-Path $HOME ".agents\skills\perspirate")
)

$ErrorActionPreference = "Stop"
$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Resolve-FullPath([string]$Path) {
  $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
}

if ($CommandsDir) {
  if ($Destination) { throw "Use either -CommandsDir or -Destination, not both." }
  if ($PSBoundParameters.ContainsKey("Target") -and $Target -ne "ClaudeCode") {
    throw "-CommandsDir is a ClaudeCode compatibility option."
  }
  $Target = "ClaudeCode"
  $ClaudeDir = $CommandsDir
}

if ($Target -eq "Custom" -and -not $Destination) {
  throw "Custom target requires -Destination."
}
if ($Target -eq "All" -and $Destination) {
  throw "-Destination is valid only with Custom (or as a single-target override)."
}

$VaultDir = Resolve-FullPath $VaultDir
$ClaudeDir = Resolve-FullPath $ClaudeDir
$CodexDir = Resolve-FullPath $CodexDir
if ($Destination) { $Destination = Resolve-FullPath $Destination }

$jobs = @()
switch ($Target) {
  "ClaudeCode" {
    $dir = if ($Destination) { $Destination } else { $ClaudeDir }
    $jobs += [pscustomobject]@{ Name = "Claude Code"; Dir = $dir; File = "perspirate.md" }
  }
  "Codex" {
    $dir = if ($Destination) { $Destination } else { $CodexDir }
    $jobs += [pscustomobject]@{ Name = "Codex"; Dir = $dir; File = "SKILL.md" }
  }
  "All" {
    $jobs += [pscustomobject]@{ Name = "Claude Code"; Dir = $ClaudeDir; File = "perspirate.md" }
    $jobs += [pscustomobject]@{ Name = "Codex"; Dir = $CodexDir; File = "SKILL.md" }
  }
  "Custom" {
    $jobs += [pscustomobject]@{ Name = "Custom"; Dir = $Destination; File = "SKILL.md" }
  }
}

Write-Host "Perspirator installer"
if (-not $PSBoundParameters.ContainsKey("Target") -and -not $CommandsDir) {
  Write-Host "  target: ClaudeCode (backward-compatible default)"
} else {
  Write-Host "  target: $Target"
}
Write-Host "  vault:  $VaultDir"
foreach ($job in $jobs) { Write-Host "  $($job.Name): $($job.Dir)\$($job.File)" }
Write-Host ""

$template = Get-Content -Raw -Encoding UTF8 (Join-Path $RepoDir "SKILL.md")
foreach ($job in $jobs) {
  New-Item -ItemType Directory -Force -Path $job.Dir | Out-Null
  foreach ($script in "problem_half.py", "problem_index.py", "doctor.py") {
    Copy-Item (Join-Path $RepoDir $script) (Join-Path $job.Dir $script) -Force
  }

  $rendered = $template.Replace("{{TOOLS_DIR}}", ($job.Dir -replace '\\', '/'))
  $rendered = $rendered.Replace("{{VAULT_PATH}}", ($VaultDir -replace '\\', '/'))
  $adapter = Join-Path $job.Dir $job.File
  [System.IO.File]::WriteAllText(
    $adapter, $rendered, [System.Text.UTF8Encoding]::new($false)
  )
}

Write-Host "Installed without removing any existing unrelated files."
$doctor = Join-Path $RepoDir "doctor.py"
foreach ($job in $jobs) {
  switch ($job.Name) {
    "Claude Code" {
      Write-Host "  Claude Code: invoke /perspirate"
      Write-Host ('  Validate Claude Code: python "{0}" --target ClaudeCode --vault "{1}" --claude-dir "{2}"' -f $doctor, $VaultDir, $job.Dir)
    }
    "Codex" {
      Write-Host "  Codex: invoke the perspirate skill by name or request"
      Write-Host ('  Validate Codex: python "{0}" --target Codex --vault "{1}" --codex-dir "{2}"' -f $doctor, $VaultDir, $job.Dir)
    }
    "Custom" {
      Write-Host "  Custom: load $($job.Dir)\SKILL.md in the agent"
      Write-Host ('  Validate Custom: python "{0}" --target Custom --vault "{1}" --custom-dir "{2}"' -f $doctor, $VaultDir, $job.Dir)
    }
  }
}
if ($Target -eq "All") {
  Write-Host ('  Validate both: python "{0}" --target All --vault "{1}" --claude-dir "{2}" --codex-dir "{3}"' -f $doctor, $VaultDir, $jobs[0].Dir, $jobs[1].Dir)
}
