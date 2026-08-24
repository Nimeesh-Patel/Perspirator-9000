"""Bounded, read-only extraction of standard PDF text-markup annotations.

The PDF remains the annotation owner.  This module accepts an in-memory copy,
never rewrites it, and exposes the native annotation geometry separately from
text reconstructed from that geometry.

Production dependency: ``pdfplumber==0.11.9`` (MIT license).  The import is
deliberately lazy so the rest of Perspirator remains usable when this optional
provider dependency is absent.
"""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable


PDFPLUMBER_DEPENDENCY = "pdfplumber==0.11.9"
PDFPLUMBER_VERSION = "0.11.9"
PDFPLUMBER_LICENSE = "MIT"

DEFAULT_MAX_PAGES = 2_000
DEFAULT_MAX_ANNOTATIONS = 20_000
MAX_PAGE_BOUND = 10_000
MAX_ANNOTATION_BOUND = 100_000
MAX_MAX_PAGES = MAX_PAGE_BOUND
MAX_MAX_ANNOTATIONS = MAX_ANNOTATION_BOUND
MAX_PDF_BYTES = 256 * 1024 * 1024

SUPPORTED_SUBTYPES = frozenset({
    "Highlight",
    "Squiggly",
    "StrikeOut",
    "Underline",
})


class PDFDependencyUnavailable(RuntimeError):
    """The explicitly optional PDF parser is absent or has an untested version."""


class PDFExtractionError(RuntimeError):
    """The supplied bytes cannot be safely observed as a supported PDF."""

    def __init__(self, message: str, *, code: str = "extraction_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _Dependencies:
    pdfplumber: Any
    resolve1: Callable[[Any], Any]
    decode_text: Callable[[bytes], str]
    extract_text: Callable[..., str]


def _load_dependencies() -> _Dependencies:
    try:
        pdfplumber = importlib.import_module("pdfplumber")
    except (ImportError, ModuleNotFoundError) as exc:
        raise PDFDependencyUnavailable(
            f"PDF annotation extraction requires optional dependency "
            f"{PDFPLUMBER_DEPENDENCY} ({PDFPLUMBER_LICENSE} license)."
        ) from exc

    version = getattr(pdfplumber, "__version__", None)
    if version != PDFPLUMBER_VERSION:
        raise PDFDependencyUnavailable(
            f"PDF annotation extraction is tested with {PDFPLUMBER_DEPENDENCY}; "
            f"found pdfplumber {version or 'with no version metadata'}."
        )

    try:
        pdftypes = importlib.import_module("pdfminer.pdftypes")
        pdfminer_utils = importlib.import_module("pdfminer.utils")
        plumber_utils = importlib.import_module("pdfplumber.utils")
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover - broken install
        raise PDFDependencyUnavailable(
            f"{PDFPLUMBER_DEPENDENCY} is incomplete: {exc}."
        ) from exc

    return _Dependencies(
        pdfplumber=pdfplumber,
        resolve1=pdftypes.resolve1,
        decode_text=pdfminer_utils.decode_text,
        extract_text=plumber_utils.extract_text,
    )


def _bounded_integer(name: str, value: int, hard_maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < 1 or value > hard_maximum:
        raise ValueError(f"{name} must be between 1 and {hard_maximum}")
    return value


def _pdf_error(exc: BaseException, phase: str) -> PDFExtractionError:
    kind = type(exc).__name__
    detail = str(exc).strip()
    pending: list[Any] = [exc]
    seen: set[int] = set()
    error_tokens: list[str] = []
    while pending and len(seen) < 12:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        error_tokens.extend((type(item).__name__, str(item)))
        if isinstance(item, BaseException):
            pending.extend(arg for arg in item.args if isinstance(arg, BaseException))
            if item.__cause__ is not None:
                pending.append(item.__cause__)
            if item.__context__ is not None:
                pending.append(item.__context__)
    combined = " ".join(error_tokens).lower()
    if "password" in combined or "encrypt" in combined:
        code = "encrypted"
        message = "Encrypted PDFs are not accepted by the read-only annotation parser."
    else:
        code = "corrupt"
        suffix = f": {detail}" if detail else ""
        message = f"PDF {phase} failed ({kind}){suffix}"
    return PDFExtractionError(message, code=code)


def _resolve(value: Any, resolve1: Callable[[Any], Any]) -> Any:
    return resolve1(value)


def _pdf_text(value: Any, deps: _Dependencies) -> str | None:
    if value is None:
        return None
    value = _resolve(value, deps.resolve1)
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return deps.decode_text(value)
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    return None


def _subtype(value: Any, deps: _Dependencies) -> str | None:
    text = _pdf_text(value, deps)
    if text is None:
        return None
    return text[1:] if text.startswith("/") else text


def _number(value: Any, deps: _Dependencies) -> int | float | None:
    value = _resolve(value, deps.resolve1)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (float, Decimal)):
        result = float(value)
        return result if math.isfinite(result) else None
    return None


def _json_safe(value: Any, deps: _Dependencies, *, depth: int = 0) -> Any:
    """Make a small raw field inspectable without leaking object repr addresses."""
    if depth > 8:
        return "<maximum-depth>"
    try:
        value = _resolve(value, deps.resolve1)
    except Exception as exc:  # pragma: no cover - hostile indirect object
        return {"unresolved": type(exc).__name__}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        converted = float(value)
        return converted if math.isfinite(converted) else str(value)
    if isinstance(value, bytes):
        try:
            return deps.decode_text(value)
        except Exception:
            return {"bytes_hex": value.hex()}
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    if isinstance(value, Mapping):
        return {
            str(_json_safe(key, deps, depth=depth + 1)):
            _json_safe(item, deps, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, Sequence):
        return [_json_safe(item, deps, depth=depth + 1) for item in value]
    return {"type": type(value).__name__}


def _numeric_array(
        value: Any,
        deps: _Dependencies,
        *,
        exact: int | None = None,
        multiple: int | None = None,
) -> tuple[list[int | float] | None, Any, str | None]:
    try:
        resolved = _resolve(value, deps.resolve1)
    except Exception as exc:
        return None, {"unresolved": type(exc).__name__}, "cannot be resolved"
    raw = _json_safe(resolved, deps)
    if not isinstance(resolved, (list, tuple)):
        return None, raw, "is not an array"
    if exact is not None and len(resolved) != exact:
        return None, raw, f"must contain exactly {exact} numbers"
    if multiple is not None and (not resolved or len(resolved) % multiple):
        return None, raw, f"must contain a non-empty multiple of {multiple} numbers"
    numbers: list[int | float] = []
    for item in resolved:
        number = _number(item, deps)
        if number is None:
            return None, raw, "contains a non-finite or non-numeric value"
        numbers.append(number)
    return numbers, numbers, None


def _metadata_number(
        data: Mapping[str, Any], key: str, deps: _Dependencies,
) -> tuple[int | float | None, str | None]:
    if key not in data:
        return None, None
    value = _number(data[key], deps)
    if value is None:
        return None, f"/{key} is not a finite number"
    return value, None


def _metadata_color(
        data: Mapping[str, Any], deps: _Dependencies,
) -> tuple[list[int | float] | None, str | None]:
    if "C" not in data:
        return None, None
    numbers, _raw, error = _numeric_array(data["C"], deps)
    if error is not None:
        return None, f"/C {error}"
    if len(numbers or []) not in {1, 3, 4}:
        return numbers, "/C must have 1, 3, or 4 components"
    return numbers, None


def _media_box(page: Any, deps: _Dependencies) -> list[int | float] | None:
    value = getattr(page.page_obj, "mediabox", None)
    numbers, _raw, error = _numeric_array(value, deps, exact=4)
    return numbers if error is None else None


def _page_box(
        page: Any, attribute: str, deps: _Dependencies,
) -> list[int | float] | None:
    value = getattr(page.page_obj, attribute, None)
    numbers, _raw, error = _numeric_array(value, deps, exact=4)
    return numbers if error is None else None


def _char_box(char: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    try:
        values = tuple(float(char[key]) for key in ("x0", "y0", "x1", "y1"))
    except (KeyError, TypeError, ValueError):
        return None
    return values if all(math.isfinite(value) for value in values) else None


def _selected_chars(
        chars: Sequence[Mapping[str, Any]], box: tuple[float, float, float, float],
) -> list[Mapping[str, Any]]:
    x0, y0, x1, y1 = box
    tolerance = 0.25
    selected = []
    for char in chars:
        char_box = _char_box(char)
        if char_box is None:
            continue
        cx = (char_box[0] + char_box[2]) / 2
        cy = (char_box[1] + char_box[3]) / 2
        if (x0 - tolerance <= cx <= x1 + tolerance
                and y0 - tolerance <= cy <= y1 + tolerance):
            selected.append(char)
    return selected


def _quote_from_geometry(
        page: Any,
        rect: list[int | float] | None,
        quad_points: list[int | float] | None,
        deps: _Dependencies,
) -> tuple[str | None, str, str | None, list[str]]:
    """Return quote, completeness, method, and bounded diagnostic reasons."""
    reasons: list[str] = []
    try:
        chars = page.chars
    except Exception as exc:
        return (
            None,
            "unavailable",
            None,
            [f"page text extraction failed ({type(exc).__name__})"],
        )
    if not chars:
        return None, "unavailable", None, ["page has no extractable characters"]

    sound = True
    rotation = int(getattr(page, "rotation", 0) or 0)
    if rotation:
        sound = False
        reasons.append(f"page rotation {rotation} makes raw geometry alignment uncertain")
    media_box = _media_box(page, deps)
    if media_box is None:
        sound = False
        reasons.append("page MediaBox is unavailable")
    elif abs(float(media_box[0])) > 1e-6 or abs(float(media_box[1])) > 1e-6:
        sound = False
        reasons.append("non-zero MediaBox origin makes raw geometry alignment uncertain")

    boxes: list[tuple[float, float, float, float]] = []
    method: str | None = None
    if quad_points:
        method = "quad_points"
        for offset in range(0, len(quad_points), 8):
            quad = [float(value) for value in quad_points[offset:offset + 8]]
            xs = quad[0::2]
            ys = quad[1::2]
            boxes.append((min(xs), min(ys), max(xs), max(ys)))
            # Standard text markup has parallel top and bottom edges.  We can
            # still recover a candidate from a skewed quad, but cannot call it
            # complete using character bounding boxes alone.
            if not (
                    math.isclose(quad[1], quad[3], abs_tol=0.5)
                    and math.isclose(quad[5], quad[7], abs_tol=0.5)
                    and math.isclose(quad[0], quad[4], abs_tol=0.5)
                    and math.isclose(quad[2], quad[6], abs_tol=0.5)):
                sound = False
                reasons.append("a QuadPoints segment is not axis-aligned")
    elif rect:
        method = "rect_fallback"
        xs = [float(rect[0]), float(rect[2])]
        ys = [float(rect[1]), float(rect[3])]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
        sound = False
        reasons.append("quote uses /Rect because /QuadPoints is unavailable")
    else:
        return None, "unavailable", None, ["no usable annotation geometry"]

    pieces: list[str] = []
    missing_segments = 0
    non_upright = False
    for box in boxes:
        selected = _selected_chars(chars, box)
        if not selected:
            missing_segments += 1
            continue
        if any(char.get("upright") is False for char in selected):
            non_upright = True
        try:
            piece = deps.extract_text(
                selected, layout=False, x_tolerance=1, y_tolerance=2).strip()
        except Exception as exc:  # pragma: no cover - defensive around dependency
            reasons.append(f"selected text assembly failed ({type(exc).__name__})")
            missing_segments += 1
            continue
        if piece:
            pieces.append(piece)
        else:
            missing_segments += 1

    if non_upright:
        sound = False
        reasons.append("selected characters include transformed text")
    if missing_segments:
        sound = False
        reasons.append(
            f"{missing_segments} of {len(boxes)} geometry segments had no text")

    quote = "\n".join(pieces) or None
    if quote is None:
        completeness = "unavailable"
    elif sound:
        completeness = "complete"
    else:
        completeness = "partial"
    return quote, completeness, method, list(dict.fromkeys(reasons))


def _identity(
        *,
        page_index: int,
        subtype: str,
        rect: Any,
        quad_points: Any,
        creation_date: str | None,
        contents: str | None,
) -> tuple[dict[str, Any], str]:
    inputs = {
        "page_index": page_index,
        "subtype": subtype,
        "rect": rect,
        "quad_points": quad_points,
        "creation_date": creation_date,
        # /NM is the only native stable annotation name.  When it is absent,
        # /Contents disambiguates collocated annotations that carry different
        # reader notes.  Editing that note can therefore change this explicitly
        # derived identity; the native geometry remains separately preserved.
        "contents": contents,
    }
    encoded = json.dumps(
        inputs, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")
    return inputs, f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _malformed_row(
        page_index: int,
        annotation_index: int,
        subtype: str | None,
        reason: str,
        native_id: str | None = None,
) -> dict[str, Any]:
    return {
        "page_index": page_index,
        "page_number": page_index + 1,
        "annotation_index": annotation_index,
        "subtype": subtype,
        "native_id": native_id,
        "reason": reason,
    }


def parse_pdf_annotations(
        payload: bytes,
        *,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_annotations: int = DEFAULT_MAX_ANNOTATIONS,
) -> dict[str, Any]:
    """Observe embedded PDF text-markup annotations without changing ``payload``.

    ``records`` contains supported text-markup annotations.  Unsupported and
    malformed input remains explicit in counters, subtype counts, diagnostics,
    and the overall ``status`` rather than being confused with absence.
    """
    max_pages = _bounded_integer("max_pages", max_pages, MAX_PAGE_BOUND)
    max_annotations = _bounded_integer(
        "max_annotations", max_annotations, MAX_ANNOTATION_BOUND)
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if not payload:
        raise PDFExtractionError("PDF payload is empty", code="corrupt")
    if len(payload) > MAX_PDF_BYTES:
        raise PDFExtractionError(
            f"PDF payload exceeds the {MAX_PDF_BYTES}-byte bound", code="bounds")

    deps = _load_dependencies()
    try:
        pdf = deps.pdfplumber.open(io.BytesIO(payload))
    except Exception as exc:
        raise _pdf_error(exc, "open") from exc

    records: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []
    partial_rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    unsupported = Counter()
    pages_with_annotations: set[int] = set()
    observed_rows = 0
    pages_observed = 0
    annotations_truncated = False

    try:
        if getattr(pdf.doc, "encryption", None):
            raise PDFExtractionError(
                "Encrypted PDFs are not accepted by the read-only annotation parser.",
                code="encrypted",
            )
        try:
            pages = pdf.pages
        except Exception as exc:
            raise _pdf_error(exc, "page enumeration") from exc
        page_count = len(pages)
        if page_count < 1:
            raise PDFExtractionError("PDF has no pages", code="corrupt")

        pages_to_read = pages[:max_pages]
        if page_count > max_pages:
            warnings.append(
                f"page bound reached: observed first {max_pages} of {page_count} pages")

        for page_index, page in enumerate(pages_to_read):
            pages_observed += 1
            try:
                raw_annots = _resolve(page.page_obj.annots, deps.resolve1)
            except Exception as exc:
                malformed.append(_malformed_row(
                    page_index, -1, None,
                    f"page annotation array cannot be resolved ({type(exc).__name__})"))
                continue
            if raw_annots is None:
                continue
            if not isinstance(raw_annots, (list, tuple)):
                malformed.append(_malformed_row(
                    page_index, -1, None, "page /Annots is not an array"))
                continue
            if raw_annots:
                pages_with_annotations.add(page_index)

            for annotation_index, raw_annot in enumerate(raw_annots):
                if observed_rows >= max_annotations:
                    annotations_truncated = True
                    break
                observed_rows += 1
                try:
                    data = _resolve(raw_annot, deps.resolve1)
                except Exception as exc:
                    malformed.append(_malformed_row(
                        page_index, annotation_index, None,
                        f"annotation cannot be resolved ({type(exc).__name__})"))
                    continue
                if not isinstance(data, Mapping):
                    malformed.append(_malformed_row(
                        page_index, annotation_index, None,
                        "annotation is not a dictionary"))
                    continue

                try:
                    subtype = _subtype(data.get("Subtype"), deps)
                    native_id = _pdf_text(data.get("NM"), deps)
                except Exception as exc:
                    malformed.append(_malformed_row(
                        page_index, annotation_index, None,
                        f"annotation identity cannot be decoded ({type(exc).__name__})"))
                    continue
                if not subtype:
                    malformed.append(_malformed_row(
                        page_index, annotation_index, None,
                        "annotation has no usable /Subtype", native_id))
                    continue
                if subtype not in SUPPORTED_SUBTYPES:
                    unsupported[subtype] += 1
                    continue

                issues: list[str] = []
                rect_numbers: list[int | float] | None = None
                rect_raw: Any = None
                if "Rect" in data:
                    rect_numbers, rect_raw, error = _numeric_array(
                        data["Rect"], deps, exact=4)
                    if error:
                        issues.append(f"/Rect {error}")
                else:
                    error = "is missing"
                    issues.append("/Rect is missing")

                quad_numbers: list[int | float] | None = None
                quad_raw: Any = None
                if "QuadPoints" in data:
                    quad_numbers, quad_raw, quad_error = _numeric_array(
                        data["QuadPoints"], deps, multiple=8)
                    if quad_error:
                        issues.append(f"/QuadPoints {quad_error}")
                else:
                    issues.append("/QuadPoints is missing")

                if rect_numbers is None:
                    malformed.append(_malformed_row(
                        page_index, annotation_index, subtype,
                        "; ".join(issues), native_id))
                    continue

                try:
                    contents = _pdf_text(data.get("Contents"), deps)
                    author = _pdf_text(data.get("T"), deps)
                    creation_date = _pdf_text(data.get("CreationDate"), deps)
                    modification_date = _pdf_text(data.get("M"), deps)
                    subject = _pdf_text(data.get("Subj"), deps)
                except Exception as exc:
                    malformed.append(_malformed_row(
                        page_index, annotation_index, subtype,
                        f"annotation metadata cannot be decoded ({type(exc).__name__})",
                        native_id))
                    continue

                color, color_issue = _metadata_color(data, deps)
                opacity, opacity_issue = _metadata_number(data, "CA", deps)
                flags, flags_issue = _metadata_number(data, "F", deps)
                for issue in (color_issue, opacity_issue, flags_issue):
                    if issue:
                        issues.append(issue)

                quote, quote_completeness, quote_method, quote_reasons = (
                    _quote_from_geometry(
                        page, rect_numbers, quad_numbers, deps))
                issues.extend(quote_reasons)
                issues = list(dict.fromkeys(issues))

                identity_inputs, derived_identity = _identity(
                    page_index=page_index,
                    subtype=subtype,
                    rect=rect_raw,
                    quad_points=quad_raw,
                    creation_date=creation_date,
                    contents=contents,
                )
                page_label = getattr(page.page_obj, "label", None)
                if page_label is not None:
                    page_label = str(page_label)
                record = {
                    "identity_source": "pdf:/NM" if native_id else "derived:sha256",
                    "native_id": native_id,
                    "derived_identity": derived_identity,
                    "identity_inputs": identity_inputs,
                    "page_index": page_index,
                    "page_number": page_index + 1,
                    "page_label": page_label,
                    "page_rotation": int(getattr(page, "rotation", 0) or 0),
                    "media_box": _page_box(page, "mediabox", deps),
                    "crop_box": _page_box(page, "cropbox", deps),
                    "coordinate_space": "PDF default user space",
                    "annotation_index": annotation_index,
                    "subtype": subtype,
                    # These are the values as stored in PDF user space.  They
                    # are intentionally not converted to top-origin viewer
                    # coordinates.
                    "rect": rect_raw,
                    "quad_points": quad_raw,
                    "quote": quote,
                    "contents": contents,
                    "quote_completeness": quote_completeness,
                    "quote_method": quote_method,
                    "completeness": quote_completeness,
                    "completeness_explanation": (
                        "; ".join(issues) if issues else None),
                    "author": author,
                    "color": color,
                    "opacity": opacity,
                    "creation_date": creation_date,
                    "modification_date": modification_date,
                    "created": creation_date,
                    "modified": modification_date,
                    "subject": subject,
                    "flags": flags,
                    "issues": issues,
                }
                records.append(record)
                if issues:
                    partial_rows.append(_malformed_row(
                        page_index, annotation_index, subtype,
                        "; ".join(issues), native_id))

            if annotations_truncated:
                break
    finally:
        pdf.close()

    if annotations_truncated:
        warnings.append(
            f"annotation bound reached after {max_annotations} observed rows")
    if unsupported:
        warnings.append(
            f"{sum(unsupported.values())} annotations use unsupported subtypes")
    if malformed:
        warnings.append(
            f"{len(malformed)} annotation or page records were malformed")
    if partial_rows:
        warnings.append(
            f"{len(partial_rows)} recovered annotations were partial")
    if any(record["quote_completeness"] != "complete" for record in records):
        warnings.append("one or more annotation quotes are not complete")

    truncated = page_count > max_pages or annotations_truncated
    partial = bool(truncated or unsupported or malformed or partial_rows or any(
        record["quote_completeness"] != "complete" for record in records))
    malformed_reasons = dict(sorted(Counter(
        row["reason"] for row in malformed).items()))
    result = {
        "status": "partial" if partial else "complete",
        "records": records,
        "page_count": page_count,
        "pages_observed": pages_observed,
        "observed_rows": observed_rows,
        "unsupported_rows": sum(unsupported.values()),
        "unsupported_subtypes": dict(sorted(unsupported.items())),
        "malformed_rows": len(malformed),
        "malformed_reasons": malformed_reasons,
        "malformed": malformed,
        "partial_rows": len(partial_rows),
        "partial": partial_rows,
        "annotation_surface_present": observed_rows > 0,
        "pages_with_annotations": sorted(pages_with_annotations),
        "warnings": warnings,
        "truncated": {
            "pages": page_count > max_pages,
            "annotations": annotations_truncated,
        },
        "bounds": {
            "max_pages": max_pages,
            "max_annotations": max_annotations,
            "max_pdf_bytes": MAX_PDF_BYTES,
        },
        "parser": {
            "name": "pdfplumber",
            "version": PDFPLUMBER_VERSION,
            "dependency": PDFPLUMBER_DEPENDENCY,
            "license": PDFPLUMBER_LICENSE,
        },
    }
    # Enforce the provider-facing promise that no dependency object or byte
    # string escaped into the contract.
    json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return result


__all__ = [
    "DEFAULT_MAX_ANNOTATIONS",
    "DEFAULT_MAX_PAGES",
    "MAX_ANNOTATION_BOUND",
    "MAX_MAX_ANNOTATIONS",
    "MAX_MAX_PAGES",
    "MAX_PAGE_BOUND",
    "MAX_PDF_BYTES",
    "PDFDependencyUnavailable",
    "PDFExtractionError",
    "PDFPLUMBER_DEPENDENCY",
    "SUPPORTED_SUBTYPES",
    "parse_pdf_annotations",
]
