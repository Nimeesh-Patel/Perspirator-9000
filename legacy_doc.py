"""Optional local Word reader for legacy DOC bytes; original source untouched."""

import json
import os
import subprocess
import tempfile
from pathlib import Path


def cleanup_owned_worker(output):
    """Retire only a checkpointed worker if its PID AND birth time still match."""
    if not output:
        return
    try:
        header = json.loads(output.decode("utf-8-sig").splitlines()[0])
        process_id, ticks = header["owned_process_pid"], header["owned_process_start_ticks"]
        if type(process_id) is not int or type(ticks) is not int or process_id <= 0 or ticks <= 0:
            return
    except (ValueError, KeyError, UnicodeError, IndexError):
        return
    command = (f"$docWorkerProcess = Get-Process -Id {process_id} -ErrorAction SilentlyContinue; "
               f"if ($docWorkerProcess -and $docWorkerProcess.ProcessName -eq 'WINWORD' -and "
               f"$docWorkerProcess.StartTime.ToUniversalTime().Ticks -eq {ticks}) "
               "{ $docWorkerProcess.Kill(); $docWorkerProcess.WaitForExit(3000) | Out-Null }")
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                   capture_output=True, timeout=10, check=False,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def read_doc(data, timeout=45):
    if os.name != "nt":
        raise ValueError("legacy DOC requires Windows and installed Microsoft Word")
    script = Path(__file__).with_name("windows_doc.ps1")
    if not script.is_file():
        raise ValueError("legacy DOC helper missing; reinstall Perspirator")
    with tempfile.TemporaryDirectory(prefix="perspirator-doc-") as directory:
        path = Path(directory) / "source.doc"
        path.write_bytes(data)
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script),
                 "-DocumentPath", str(path)], capture_output=True, timeout=timeout,
                check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            cleanup_owned_worker(exc.stdout)
            raise ValueError(f"Microsoft Word read timed out after {timeout}s") from exc
        if completed.returncode:
            cleanup_owned_worker(completed.stdout)
            detail = completed.stderr.decode("utf-8", errors="backslashreplace")[:1500]
            raise ValueError(f"Microsoft Word DOC read unavailable: {detail.strip()}")
        result = json.loads(completed.stdout.decode("utf-8-sig", errors="strict").splitlines()[-1])
        if not isinstance(result, dict) or not isinstance(result.get("sections"), list):
            raise ValueError("Microsoft Word returned invalid document evidence")
        return result
