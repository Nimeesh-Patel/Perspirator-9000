#!/usr/bin/env python3
"""Optional bounded PDF-page rendering plus Windows local OCR.

The source PDF is unchanged; transient page PNGs are removed after recognition.
Recognition is labelled derived evidence, never an exact native text layer.
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


class OCRError(ValueError):
    pass


def recognize_pdf_page(data, page_index, *, language="en-US", timeout=45):
    if os.name != "nt":
        raise OCRError("local Windows OCR unavailable on this platform")
    if not isinstance(language, str) or not re.fullmatch(r"[A-Za-z0-9-]{2,35}", language):
        raise OCRError("OCR language must be a Windows language tag")
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise OCRError("OCR rendering requires pypdfium2 from requirements-pdf.txt") from exc
    script = Path(__file__).with_name("windows_ocr.ps1")
    if not script.is_file():
        raise OCRError("installed Windows OCR helper is missing; reinstall Perspirator")
    try:
        with tempfile.TemporaryDirectory(prefix="perspirator-ocr-") as directory:
            path = Path(directory) / "page.png"
            with pdfium.PdfDocument(data) as pdf:
                page = pdf[page_index]
                try:
                    width, height = page.get_size()
                    # Keep each image within 2400px and 5.76 million pixels.
                    scale = min(3.0, 2400 / max(width, height))
                    bitmap = page.render(scale=scale)
                    try:
                        image = bitmap.to_pil()
                        try:
                            image.save(path)
                            size = list(image.size)
                        finally:
                            image.close()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script),
                 "-ImagePath", str(path), "-Language", language],
                capture_output=True, timeout=timeout, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if completed.returncode:
                detail = completed.stderr.decode("utf-8", errors="backslashreplace")[:1500]
                raise OCRError(f"Windows OCR failed: {detail.strip()}")
            result = json.loads(completed.stdout.decode("utf-8-sig", errors="strict"))
            if not isinstance(result, dict) or not isinstance(result.get("text"), str):
                raise OCRError("Windows OCR returned invalid text evidence")
            result.update({"render_scale": scale, "image_size": size,
                           "evidence_kind": "OCR recognition hypothesis; verify against original page"})
            return result
    except OCRError:
        raise
    except Exception as exc:
        raise OCRError(f"OCR unavailable or failed: {type(exc).__name__}: {exc}") from exc
