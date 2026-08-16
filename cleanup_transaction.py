#!/usr/bin/env python3
"""Apply an approved cleanup manifest through a recoverable platform adapter.

The manifest owns the semantic retention judgment.  This mechanism owns the
transaction boundary: exact approval identity, complete preflight validation,
fresh per-target preconditions, recoverable mutation, durable checkpoints, and
observed outcomes.  It never infers that a path is redundant.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from cleanup_manifest import tree_state, validate_manifest
from directory_audit import sha256_file


SCHEMA_VERSION = 1
SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _exists(path: Path) -> bool:
    return os.path.lexists(path)


def _within_or_equal(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def _write_checkpoint(path: Path, record: dict) -> None:
    """Replace a transaction checkpoint atomically on the same filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _declared_targets(manifest_path: Path) -> list[dict]:
    """Read only the approval-bound fields needed for cheap feasibility checks."""
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    groups = payload.get("groups", []) if isinstance(payload, dict) else []
    targets = []
    for group in groups:
        for item in group.get("items", []) if isinstance(group, dict) else []:
            if (not isinstance(item, dict)
                    or not isinstance(item.get("path"), str)
                    or not isinstance(item.get("bytes"), int)
                    or item["bytes"] < 0):
                return []
            targets.append({"path": item["path"], "bytes": item["bytes"],
                            "type": item.get("type")})
    return targets


def windows_recycle(path: Path, *, run=subprocess.run) -> None:
    """Move one exact file or directory to the Windows Recycle Bin.

    The target travels over stdin rather than entering command text, keeping
    the platform-specific adapter independent of path quoting and shell
    interpretation.
    """
    script = r"""
Add-Type -AssemblyName Microsoft.VisualBasic
$target = [Console]::In.ReadToEnd()
$ui = [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs
$recycle = [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin
$cancel = [Microsoft.VisualBasic.FileIO.UICancelOption]::ThrowException
if ([System.IO.File]::Exists($target)) {
  [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($target, $ui, $recycle, $cancel)
} elseif ([System.IO.Directory]::Exists($target)) {
  [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($target, $ui, $recycle, $cancel)
} else {
  throw "cleanup target is unavailable: $target"
}
"""
    completed = run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        input=str(path), text=True, capture_output=True, check=False)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or
                  f"PowerShell exited {completed.returncode}").strip()
        raise OSError(detail)


def windows_recycle_capacity(targets: list[dict], *, run=subprocess.run) -> dict:
    """Report whether every target group fits its volume's Recycle Bin quota.

    Windows may permanently delete or evict existing recoverable items when a
    request exceeds the configured quota.  Capacity is therefore a mutation
    precondition, not an implementation detail of the delete call.
    """
    drives = {}
    for target in targets:
        path = Path(target["path"])
        drive = path.drive.upper()
        if not drive:
            return {"status": "unavailable", "problems": [
                f"target has no drive-letter volume: {path}"]}
        drives[drive] = drives.get(drive, 0) + target["bytes"]
    requests = [{"drive": drive, "required_bytes": required}
                for drive, required in sorted(drives.items())]
    script = r"""
$requests = @([Console]::In.ReadToEnd() | ConvertFrom-Json)
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$results = foreach ($request in $requests) {
  $letter = ([string]$request.drive).TrimEnd(':')
  $volume = Get-Volume -DriveLetter $letter -ErrorAction Stop
  $guid = [regex]::Match([string]$volume.UniqueId, '\{[0-9A-Fa-f-]+\}').Value
  if (-not $guid) { throw "cannot map Recycle Bin quota for drive $letter" }
  $key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume\$guid"
  $setting = Get-ItemProperty -LiteralPath $key -ErrorAction Stop
  $bin = "$letter`:\`$Recycle.Bin\$sid"
  $current = 0
  if (Test-Path -LiteralPath $bin) {
    $measured = Get-ChildItem -LiteralPath $bin -File -Recurse -Force -ErrorAction Stop |
      Measure-Object -Property Length -Sum
    if ($null -ne $measured.Sum) { $current = [int64]$measured.Sum }
  }
  $quota = [int64]$setting.MaxCapacity * 1MB
  $required = [int64]$request.required_bytes
  [pscustomobject]@{
    drive = "$letter`:"
    filesystem = [string]$volume.FileSystemType
    nuke_on_delete = [int]$setting.NukeOnDelete
    quota_bytes = $quota
    current_bytes = $current
    available_bytes = [Math]::Max([int64]0, $quota - $current)
    required_bytes = $required
    sufficient = (($setting.NukeOnDelete -eq 0) -and ($required -le ($quota - $current)))
  }
}
@($results) | ConvertTo-Json -Depth 4 -Compress
"""
    completed = run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        input=json.dumps(requests), text=True, capture_output=True, check=False)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or
                  f"PowerShell exited {completed.returncode}").strip()
        return {"status": "unavailable", "problems": [detail],
                "required_bytes": sum(drives.values())}
    try:
        volumes = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as error:
        return {"status": "unavailable",
                "problems": [f"invalid capacity response: {error}"],
                "required_bytes": sum(drives.values())}
    if isinstance(volumes, dict):
        volumes = [volumes]
    sufficient = (len(volumes) == len(requests)
                  and all(item.get("sufficient") for item in volumes))
    return {
        "status": "available" if sufficient else "insufficient",
        "required_bytes": sum(drives.values()),
        "volumes": volumes,
        "problems": ([] if sufficient else [
            "approved targets exceed available Recycle Bin quota or the volume permanently deletes by policy"]),
    }


def _fresh_target(observed: dict, *, progress=None,
                  progress_every: int = 10_000,
                  hash_workers: int = 8) -> tuple[bool, dict]:
    """Re-observe one preflight target immediately before mutation."""
    path = Path(observed["path"])
    kind = observed["type"]
    if kind == "file":
        if not path.is_file():
            return False, {"exists": _exists(path)}
        current = {
            "path": str(path), "type": kind, "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        fields = ("bytes", "sha256")
    elif kind == "directory_tree":
        if not path.is_dir():
            return False, {"exists": _exists(path)}

        def report(event):
            if progress:
                progress({"target": str(path), **event})

        current = {
            "path": str(path), "type": kind,
            **tree_state(path, progress=report,
                         progress_every=progress_every,
                         hash_workers=hash_workers),
        }
        fields = ("bytes", "file_count", "directory_count", "tree_sha256",
                  "reparse_boundaries")
    else:
        return False, {"exists": _exists(path), "unsupported_type": kind}
    return all(current.get(field) == observed.get(field) for field in fields), current


def apply_cleanup(manifest_path: Path, approved_sha256: str, record_path: Path,
                  *, recycler=windows_recycle, validator=validate_manifest,
                  capacity_checker=windows_recycle_capacity,
                  progress=None, progress_every: int = 10_000,
                  hash_workers: int = 8) -> dict:
    """Validate and apply one content-addressed cleanup transaction."""
    manifest_path = manifest_path.resolve(strict=True)
    record_path = record_path.resolve(strict=False)
    if not SHA256_PATTERN.fullmatch(approved_sha256):
        raise ValueError("approved SHA-256 must contain exactly 64 hex characters")
    approved_sha256 = approved_sha256.casefold()
    if record_path.exists() or record_path.with_name(record_path.name + ".tmp").exists():
        raise FileExistsError(
            f"transaction record already exists; inspect it rather than retry: {record_path}")
    if sha256_file(manifest_path) != approved_sha256:
        raise ValueError("manifest does not match the explicit approval SHA-256")

    # Approval binds these declarations before they are trusted for mutation.
    # Capacity cannot justify deletion, so it is safe and cheaper to reject an
    # impossible Recycle Bin plan before hashing every target.
    declared = _declared_targets(manifest_path)
    for target in declared:
        if target["type"] != "directory_tree":
            continue
        target_path = Path(target["path"]).resolve(strict=False)
        if _within_or_equal(manifest_path, target_path):
            raise ValueError("manifest cannot be inside a nominated directory")
        if _within_or_equal(record_path, target_path):
            raise ValueError("transaction record cannot be inside a nominated directory")
    if declared:
        feasibility = capacity_checker(declared)
        if feasibility.get("status") != "available":
            refusal = {
                "schema_version": SCHEMA_VERSION,
                "operation": "recycle-bin-cleanup",
                "status": "refused",
                "started_at": _now(), "ended_at": _now(),
                "manifest": str(manifest_path),
                "approved_manifest_sha256": approved_sha256,
                "reason": "recycle-capacity-precondition",
                "expected_total_bytes": sum(item["bytes"] for item in declared),
                "targets_total": len(declared),
                "capacity": feasibility,
                "operations": [],
            }
            _write_checkpoint(record_path, refusal)
            return refusal

    validation = validator(
        manifest_path, progress=progress, progress_every=progress_every,
        hash_workers=hash_workers)
    if validation.get("status") != "ready":
        return {
            "schema_version": SCHEMA_VERSION,
            "operation": "recycle-bin-cleanup",
            "status": "refused",
            "manifest": str(manifest_path),
            "approved_manifest_sha256": approved_sha256,
            "validation": validation,
            "operations": [],
        }
    if validation.get("manifest_sha256", "").casefold() != approved_sha256:
        raise RuntimeError("validator observed a different manifest identity")
    if sha256_file(manifest_path) != approved_sha256:
        raise RuntimeError("manifest changed during validation")

    targets = sorted(validation["targets"],
                     key=lambda item: (-item["bytes"], item["path"].casefold()))
    for target in targets:
        target_path = Path(target["path"])
        if target["type"] != "directory_tree":
            continue
        if _within_or_equal(manifest_path, target_path):
            raise ValueError("manifest cannot be inside a nominated directory")
        if _within_or_equal(record_path, target_path):
            raise ValueError("transaction record cannot be inside a nominated directory")
    # Re-observe capacity after the potentially long exact validation because
    # another process may have changed Recycle Bin state in the meantime.
    capacity = capacity_checker(targets)
    if capacity.get("status") != "available":
        refusal = {
            "schema_version": SCHEMA_VERSION,
            "operation": "recycle-bin-cleanup",
            "status": "refused",
            "started_at": _now(), "ended_at": _now(),
            "manifest": str(manifest_path),
            "approved_manifest_sha256": approved_sha256,
            "root": validation["root"],
            "expected_total_bytes": validation["expected_total_bytes"],
            "targets_total": len(targets),
            "capacity": capacity,
            "operations": [],
        }
        _write_checkpoint(record_path, refusal)
        return refusal
    record = {
        "schema_version": SCHEMA_VERSION,
        "operation": "recycle-bin-cleanup",
        "status": "applying",
        "started_at": _now(),
        "manifest": str(manifest_path),
        "approved_manifest_sha256": approved_sha256,
        "root": validation["root"],
        "expected_total_bytes": validation["expected_total_bytes"],
        "targets_total": len(targets),
        "capacity": capacity,
        "validation": {
            "status": validation["status"],
            "observed_total_bytes": validation["observed_total_bytes"],
            "targets_observed": validation["targets_observed"],
            "problems": validation["problems"],
        },
        "current": None,
        "operations": [],
    }
    _write_checkpoint(record_path, record)

    for target in targets:
        if sha256_file(manifest_path) != approved_sha256:
            record.update({
                "status": "partial" if record["operations"] else "error",
                "ended_at": _now(),
                "current": {"path": target["path"],
                            "status": "manifest-changed"},
                "do_not_retry": True,
            })
            _write_checkpoint(record_path, record)
            return record

        fresh, current = _fresh_target(
            target, progress=progress, progress_every=progress_every,
            hash_workers=hash_workers)
        if not fresh:
            record.update({
                "status": "partial" if record["operations"] else "error",
                "ended_at": _now(),
                "current": {"path": target["path"],
                            "status": "precondition-changed",
                            "observed": current},
                "do_not_retry": True,
            })
            _write_checkpoint(record_path, record)
            return record

        intent = {
            "path": target["path"], "type": target["type"],
            "bytes": target["bytes"], "status": "attempting",
            "started_at": _now(),
        }
        record["current"] = intent
        _write_checkpoint(record_path, record)
        try:
            recycler(Path(target["path"]))
        except Exception as error:  # platform adapter failures require observation
            intent.update({
                "status": "indeterminate", "ended_at": _now(),
                "source_exists": _exists(Path(target["path"])),
                "error": str(error),
            })
            record.update({
                "status": "indeterminate", "ended_at": _now(),
                "current": intent, "do_not_retry": True,
            })
            _write_checkpoint(record_path, record)
            return record

        if _exists(Path(target["path"])):
            intent.update({"status": "not-applied", "ended_at": _now(),
                           "source_exists": True})
            record.update({
                "status": "partial" if record["operations"] else "error",
                "ended_at": _now(), "current": intent,
                "do_not_retry": True,
            })
            _write_checkpoint(record_path, record)
            return record
        intent.update({"status": "applied", "ended_at": _now(),
                       "source_exists": False})
        record["operations"].append(intent)
        record["current"] = None
        _write_checkpoint(record_path, record)

    record.update({
        "status": "applied", "ended_at": _now(), "current": None,
        "applied_targets": len(record["operations"]),
        "applied_bytes": sum(item["bytes"] for item in record["operations"]),
        "do_not_retry": False,
    })
    _write_checkpoint(record_path, record)
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--approved-sha256", required=True)
    parser.add_argument("--record", required=True, type=Path)
    parser.add_argument("--apply", action="store_true",
                        help="perform the recoverable transaction; otherwise refuse")
    parser.add_argument("--progress", action="store_true",
                        help="emit JSON validation and precondition progress to stderr")
    parser.add_argument("--progress-every", type=int, default=10_000)
    parser.add_argument("--hash-workers", type=int, default=8)
    args = parser.parse_args(argv)
    if not args.apply:
        parser.error("--apply is required; validation alone belongs to cleanup_manifest.py")
    if args.progress_every <= 0 or args.hash_workers <= 0:
        parser.error("progress interval and hash workers must be positive")

    def emit(record):
        print(json.dumps(record, ensure_ascii=False), file=sys.stderr, flush=True)

    try:
        result = apply_cleanup(
            args.manifest, args.approved_sha256, args.record,
            progress=emit if args.progress else None,
            progress_every=args.progress_every, hash_workers=args.hash_workers)
    except (OSError, RuntimeError, ValueError) as error:
        result = {"status": "refused", "error": str(error)}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("status") == "applied" else 2


if __name__ == "__main__":
    raise SystemExit(main())
