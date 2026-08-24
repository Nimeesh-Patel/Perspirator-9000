"""Regenerate and verify deterministic PDF-annotation contract fixtures.

Maintainer-only dependencies are pinned in ``manifest.json``.  They are not
runtime dependencies of Perspirator's parser.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

import pypdf
import reportlab
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Highlight, Text
from pypdf.constants import PageLabelStyle
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    TextStringObject,
)
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


REPORTLAB_VERSION = "4.4.9"
PYPDF_VERSION = "6.10.0"
PAGE_WIDTH = 612
PAGE_HEIGHT = 792
FONT = "Helvetica"
FONT_SIZE = 12
BASELINE = 720

FIXED_METADATA = {
    "/Title": "Perspirator PDF annotation fixture",
    "/Author": "Perspirator maintainers",
    "/Subject": "Deterministic annotation-parser contract evidence",
    "/Creator": "fixtures/pdf_annotations/generate_fixtures.py",
    "/Producer": "Perspirator deterministic fixture generator",
    "/CreationDate": "D:20260824120000+05'30'",
    "/ModDate": "D:20260824120000+05'30'",
}


def _check_versions() -> None:
    if reportlab.Version != REPORTLAB_VERSION:
        raise RuntimeError(
            f"expected reportlab {REPORTLAB_VERSION}, found {reportlab.Version}")
    if pypdf.__version__ != PYPDF_VERSION:
        raise RuntimeError(
            f"expected pypdf {PYPDF_VERSION}, found {pypdf.__version__}")


def _base_pdf(lines: list[str]) -> bytes:
    stream = io.BytesIO()
    drawing = canvas.Canvas(
        stream,
        pagesize=(PAGE_WIDTH, PAGE_HEIGHT),
        invariant=1,
        pageCompression=0,
    )
    drawing.setAuthor(FIXED_METADATA["/Author"])
    drawing.setCreator(FIXED_METADATA["/Creator"])
    drawing.setProducer(FIXED_METADATA["/Producer"])
    drawing.setSubject(FIXED_METADATA["/Subject"])
    drawing.setTitle(FIXED_METADATA["/Title"])
    for line in lines:
        drawing.setFont(FONT, FONT_SIZE)
        drawing.drawString(72, BASELINE, line)
        drawing.showPage()
    drawing.save()
    return stream.getvalue()


def _quad_for(line: str, selected: str) -> tuple[tuple[float, ...], tuple[float, ...]]:
    start = line.index(selected)
    x0 = 72 + stringWidth(line[:start], FONT, FONT_SIZE)
    x1 = x0 + stringWidth(selected, FONT, FONT_SIZE)
    y0 = BASELINE - 3
    y1 = BASELINE + 10
    rect = (x0 - 1, y0 - 1, x1 + 1, y1 + 1)
    quad = (x0, y1, x1, y1, x0, y0, x1, y0)
    return rect, quad


def _array(values: tuple[float, ...]) -> ArrayObject:
    return ArrayObject([FloatObject(value) for value in values])


def _stamp_highlight(
        annotation: Highlight,
        *,
        native_id: str | None,
        contents: str | None,
        author: str,
        creation: str,
        modified: str,
        opacity: float,
) -> Highlight:
    if native_id is not None:
        annotation[NameObject("/NM")] = TextStringObject(native_id)
    if contents is not None:
        annotation[NameObject("/Contents")] = TextStringObject(contents)
    annotation[NameObject("/T")] = TextStringObject(author)
    annotation[NameObject("/CreationDate")] = TextStringObject(creation)
    annotation[NameObject("/M")] = TextStringObject(modified)
    annotation[NameObject("/CA")] = FloatObject(opacity)
    annotation[NameObject("/Subj")] = TextStringObject("Research highlight")
    annotation[NameObject("/F")] = NumberObject(4)
    return annotation


def _writer_from(payload: bytes) -> PdfWriter:
    reader = PdfReader(io.BytesIO(payload))
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    writer.add_metadata(FIXED_METADATA)
    return writer


def _standard_pdf() -> bytes:
    first = "Alpha beta gamma delta."
    second = "Second page carries a fallback identity."
    writer = _writer_from(_base_pdf([first, second]))
    writer.set_page_label(
        0, 1, style=PageLabelStyle.DECIMAL, prefix="p-", start=1)

    rect, quad = _quad_for(first, "beta gamma")
    first_highlight = Highlight(
        rect=rect, quad_points=_array(quad), highlight_color="ffcc00")
    _stamp_highlight(
        first_highlight,
        native_id="highlight-001",
        contents="A separate reader comment.",
        author="Nimeesh",
        creation="D:20260824120500+05'30'",
        modified="D:20260824121000+05'30'",
        opacity=0.625,
    )
    writer.add_annotation(0, first_highlight)

    rect, quad = _quad_for(second, "fallback identity")
    second_highlight = Highlight(
        rect=rect, quad_points=_array(quad), highlight_color="33cc66")
    _stamp_highlight(
        second_highlight,
        native_id=None,
        contents=None,
        author="Nimeesh",
        creation="D:20260824121500+05'30'",
        modified="D:20260824121500+05'30'",
        opacity=1,
    )
    writer.add_annotation(1, second_highlight)

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _mixed_pdf() -> bytes:
    line = "Malformed fallback quote and unsupported note."
    writer = _writer_from(_base_pdf([line]))

    rect, _quad = _quad_for(line, "fallback quote")
    missing_quad = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Highlight"),
        NameObject("/Rect"): _array(rect),
        NameObject("/NM"): TextStringObject("missing-quad"),
        NameObject("/Contents"): TextStringObject("Geometry is deliberately partial."),
    })
    writer.add_annotation(0, missing_quad)

    invalid_rect = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Highlight"),
        NameObject("/Rect"): ArrayObject([
            TextStringObject("not-a-number"),
            NumberObject(700), NumberObject(120), NumberObject(730),
        ]),
        NameObject("/QuadPoints"): _array((
            72, 730, 120, 730, 72, 717, 120, 717)),
        NameObject("/NM"): TextStringObject("invalid-rect"),
    })
    writer.add_annotation(0, invalid_rect)

    unsupported = Text(
        rect=(40, 640, 60, 660),
        text="A standard text note, intentionally outside parser scope.",
        title_bar="Nimeesh",
    )
    unsupported[NameObject("/NM")] = TextStringObject("text-note-001")
    writer.add_annotation(0, unsupported)

    missing_subtype = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Rect"): ArrayObject([
            NumberObject(40), NumberObject(600),
            NumberObject(60), NumberObject(620),
        ]),
    })
    writer.add_annotation(0, missing_subtype)

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _encrypted_pdf(standard: bytes) -> bytes:
    reader = PdfReader(io.BytesIO(standard))
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    writer.add_metadata(FIXED_METADATA)
    writer.encrypt("fixture-password", algorithm="RC4-40")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def build_fixtures() -> dict[str, bytes]:
    _check_versions()
    standard = _standard_pdf()
    return {
        "standard.pdf": standard,
        "mixed.pdf": _mixed_pdf(),
        "encrypted.pdf": _encrypted_pdf(standard),
        "corrupt.pdf": b"%PDF-1.7\nThis fixture is intentionally truncated.\n",
    }


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _verify(output_dir: Path, fixtures: dict[str, bytes]) -> None:
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = manifest["fixtures"]
    failures = []
    for name, generated in fixtures.items():
        checked_in = (output_dir / name).read_bytes()
        entry = expected.get(name, {})
        if checked_in != generated:
            failures.append(f"{name}: checked-in bytes differ from regeneration")
        if entry.get("bytes") != len(checked_in):
            failures.append(f"{name}: byte count differs from manifest")
        if entry.get("sha256") != _digest(checked_in):
            failures.append(f"{name}: sha256 differs from manifest")
    if set(expected) != set(fixtures):
        failures.append("manifest fixture names differ from generator output")
    if failures:
        raise SystemExit("\n".join(failures))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    fixtures = build_fixtures()
    if args.verify:
        _verify(args.output_dir, fixtures)
        print("PDF annotation fixtures are deterministic and match manifest.json")
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in fixtures.items():
        (args.output_dir / name).write_bytes(payload)
        print(f"{name} {len(payload)} {_digest(payload)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
