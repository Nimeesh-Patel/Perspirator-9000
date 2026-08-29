"""Contract tests for bounded, read-only standard PDF annotation recovery."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import unittest
from pathlib import Path
from unittest import mock

import calibre_query as cq
import pdf_annotations as pa
import source_to_notes as stn


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pdf_annotations"

try:
    HAS_PINNED_PDFPLUMBER = (
        importlib.metadata.version("pdfplumber") == pa.PDFPLUMBER_VERSION)
except importlib.metadata.PackageNotFoundError:
    HAS_PINNED_PDFPLUMBER = False


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@unittest.skipUnless(
    HAS_PINNED_PDFPLUMBER,
    f"optional test dependency {pa.PDFPLUMBER_DEPENDENCY} is unavailable",
)
class PDFAnnotationExtractionTests(unittest.TestCase):
    def test_standard_annotations_preserve_native_fields_and_separate_quote(self):
        payload = fixture("standard.pdf")
        result = pa.parse_pdf_annotations(payload)

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["page_count"], 2)
        self.assertEqual(result["pages_with_annotations"], [0, 1])
        self.assertTrue(result["annotation_surface_present"])
        self.assertEqual(result["observed_rows"], 2)
        self.assertEqual(result["unsupported_rows"], 0)
        self.assertEqual(result["malformed_rows"], 0)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["parser"], {
            "name": "pdfplumber",
            "version": "0.11.9",
            "dependency": "pdfplumber==0.11.9",
            "license": "MIT",
        })

        first, second = result["records"]
        self.assertEqual(first["page_index"], 0)
        self.assertEqual(first["page_number"], 1)
        self.assertEqual(first["page_label"], "p-1")
        self.assertEqual(first["page_rotation"], 0)
        self.assertEqual(first["media_box"], [0, 0, 612, 792])
        self.assertEqual(first["crop_box"], [0, 0, 612, 792])
        self.assertEqual(first["coordinate_space"], "PDF default user space")
        self.assertEqual(first["subtype"], "Highlight")
        self.assertEqual(first["native_id"], "highlight-001")
        self.assertEqual(first["identity_source"], "pdf:/NM")
        self.assertEqual(first["rect"], [105.02, 716, 173.716, 731])
        self.assertEqual(first["quad_points"], [
            106.02, 730, 172.716, 730,
            106.02, 717, 172.716, 717,
        ])
        self.assertEqual(first["quote"], "beta gamma")
        self.assertEqual(first["contents"], "A separate reader comment.")
        self.assertEqual(first["quote_completeness"], "complete")
        self.assertEqual(first["quote_method"], "quad_points")
        self.assertEqual(first["author"], "Fixture Author")
        self.assertEqual(first["color"], [1, 0.8, 0.0])
        self.assertEqual(first["opacity"], 0.625)
        self.assertEqual(first["creation_date"], "D:20260824120500+05'30'")
        self.assertEqual(first["modification_date"], "D:20260824121000+05'30'")
        self.assertEqual(first["created"], first["creation_date"])
        self.assertEqual(first["modified"], first["modification_date"])
        self.assertEqual(first["subject"], "Research highlight")
        self.assertEqual(first["flags"], 4)
        self.assertEqual(first["issues"], [])
        self.assertIsNone(first["completeness_explanation"])

        self.assertEqual(second["page_index"], 1)
        self.assertEqual(second["page_number"], 2)
        self.assertEqual(second["page_label"], "p-2")
        self.assertIsNone(second["native_id"])
        self.assertEqual(second["identity_source"], "derived:sha256")
        self.assertTrue(second["derived_identity"].startswith("sha256:"))
        self.assertEqual(len(second["derived_identity"]), 71)
        self.assertEqual(second["quote"], "fallback identity")
        self.assertIsNone(second["contents"])

        # The provider contract itself is JSON, not dependency-owned objects.
        json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)

    def test_repeated_reads_are_byte_independent_and_deterministic(self):
        payload = fixture("standard.pdf")
        before = hashlib.sha256(payload).hexdigest()
        first = pa.parse_pdf_annotations(payload)
        second = pa.parse_pdf_annotations(bytes(payload))
        self.assertEqual(first, second)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), before)
        self.assertEqual(
            first["records"][1]["derived_identity"],
            second["records"][1]["derived_identity"],
        )

    def test_derived_identity_distinguishes_collocated_reader_notes(self):
        common = {
            "page_index": 0,
            "subtype": "Highlight",
            "rect": [10, 10, 20, 20],
            "quad_points": [10, 20, 20, 20, 10, 10, 20, 10],
            "creation_date": None,
        }
        first_inputs, first = pa._identity(
            **common, contents="first reader note")
        second_inputs, second = pa._identity(
            **common, contents="second reader note")
        self.assertNotEqual(first, second)
        self.assertEqual(first_inputs["contents"], "first reader note")
        self.assertEqual(second_inputs["contents"], "second reader note")

    def test_standard_fixture_enters_source_pipeline_with_unique_locators(self):
        payload = fixture("standard.pdf")

        def fetched(*_args):
            return {"ok": True, "bytes": payload}

        result = cq.query(
            "annotations", server="http://127.0.0.1:8081",
            library_id="Calibre_Library", book_id=81, book_format="PDF",
            http_get=fetched, now=lambda: "2026-08-24T12:00:00Z")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(
            len({record["locator"] for record in result["records"]}), 2)
        self.assertEqual(
            {record["reader_locator"] for record in result["records"]},
            {"calibre://show-book/Calibre_Library/81"})
        self.assertEqual(len(stn.source_records(result)), 2)

    def test_partial_quote_uses_rect_without_conflating_reader_contents(self):
        result = pa.parse_pdf_annotations(fixture("mixed.pdf"))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["observed_rows"], 4)
        self.assertEqual(len(result["records"]), 1)
        record = result["records"][0]
        self.assertEqual(record["native_id"], "missing-quad")
        self.assertEqual(record["quote"], "fallback quote")
        self.assertEqual(
            record["contents"], "Geometry is deliberately partial.")
        self.assertEqual(record["quote_method"], "rect_fallback")
        self.assertEqual(record["quote_completeness"], "partial")
        self.assertIn("/QuadPoints is missing", record["issues"])
        self.assertIn("/QuadPoints", record["completeness_explanation"])

    def test_unsupported_and_malformed_rows_are_explicit_not_false_absence(self):
        result = pa.parse_pdf_annotations(fixture("mixed.pdf"))
        self.assertTrue(result["annotation_surface_present"])
        self.assertEqual(result["pages_with_annotations"], [0])
        self.assertEqual(result["unsupported_rows"], 1)
        self.assertEqual(result["unsupported_subtypes"], {"Text": 1})
        self.assertEqual(result["malformed_rows"], 2)
        self.assertEqual(result["partial_rows"], 1)
        reasons = [row["reason"] for row in result["malformed"]]
        self.assertTrue(any("/Rect" in reason for reason in reasons))
        self.assertTrue(any("/Subtype" in reason for reason in reasons))
        self.assertEqual(sum(result["malformed_reasons"].values()), 2)
        self.assertEqual(
            len(result["records"])
            + result["unsupported_rows"]
            + result["malformed_rows"],
            result["observed_rows"],
        )

    def test_page_and_annotation_bounds_report_partial_observation(self):
        payload = fixture("standard.pdf")
        page_bounded = pa.parse_pdf_annotations(payload, max_pages=1)
        self.assertEqual(page_bounded["status"], "partial")
        self.assertEqual(page_bounded["page_count"], 2)
        self.assertEqual(page_bounded["pages_observed"], 1)
        self.assertEqual(len(page_bounded["records"]), 1)
        self.assertTrue(page_bounded["truncated"]["pages"])
        self.assertIn("page bound reached", page_bounded["warnings"][0])

        annotation_bounded = pa.parse_pdf_annotations(
            payload, max_annotations=1)
        self.assertEqual(annotation_bounded["status"], "partial")
        self.assertEqual(annotation_bounded["observed_rows"], 1)
        self.assertEqual(len(annotation_bounded["records"]), 1)
        self.assertTrue(annotation_bounded["truncated"]["annotations"])
        self.assertTrue(any(
            "annotation bound reached" in warning
            for warning in annotation_bounded["warnings"]))

    def test_encrypted_and_corrupt_inputs_fail_with_distinct_codes(self):
        with self.assertRaises(pa.PDFExtractionError) as encrypted:
            pa.parse_pdf_annotations(fixture("encrypted.pdf"))
        self.assertEqual(encrypted.exception.code, "encrypted")

        with self.assertRaises(pa.PDFExtractionError) as corrupt:
            pa.parse_pdf_annotations(fixture("corrupt.pdf"))
        self.assertEqual(corrupt.exception.code, "corrupt")


class PDFAnnotationBoundaryTests(unittest.TestCase):
    def test_missing_optional_dependency_is_explicit(self):
        real_import = pa.importlib.import_module

        def unavailable(name: str):
            if name == "pdfplumber":
                raise ModuleNotFoundError("no module named pdfplumber")
            return real_import(name)

        with mock.patch.object(pa.importlib, "import_module", unavailable):
            with self.assertRaises(pa.PDFDependencyUnavailable) as caught:
                pa.parse_pdf_annotations(b"%PDF-1.7\n")
        self.assertIn("pdfplumber==0.11.9", str(caught.exception))
        self.assertIn("MIT", str(caught.exception))

    def test_bounds_and_input_type_are_rejected_before_dependency_loading(self):
        with self.assertRaises(TypeError):
            pa.parse_pdf_annotations(bytearray(b"%PDF"))  # type: ignore[arg-type]
        with self.assertRaises(pa.PDFExtractionError) as empty:
            pa.parse_pdf_annotations(b"")
        self.assertEqual(empty.exception.code, "corrupt")
        with self.assertRaises(ValueError):
            pa.parse_pdf_annotations(b"%PDF", max_pages=0)
        with self.assertRaises(ValueError):
            pa.parse_pdf_annotations(
                b"%PDF", max_pages=pa.MAX_MAX_PAGES + 1)
        with self.assertRaises(ValueError):
            pa.parse_pdf_annotations(b"%PDF", max_annotations=True)
        with self.assertRaises(ValueError):
            pa.parse_pdf_annotations(
                b"%PDF", max_annotations=pa.MAX_MAX_ANNOTATIONS + 1)
        with mock.patch.object(pa, "MAX_PDF_BYTES", 4):
            with self.assertRaises(pa.PDFExtractionError) as oversized:
                pa.parse_pdf_annotations(b"12345")
        self.assertEqual(oversized.exception.code, "bounds")

    def test_checked_in_fixture_manifest_matches_bytes(self):
        manifest = json.loads(
            (FIXTURES / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["generator_dependencies"], {
            "pypdf": "6.10.0", "reportlab": "4.4.9"})
        for name, expected in manifest["fixtures"].items():
            payload = fixture(name)
            self.assertEqual(len(payload), expected["bytes"])
            self.assertEqual(
                hashlib.sha256(payload).hexdigest(), expected["sha256"])


if __name__ == "__main__":
    unittest.main()
