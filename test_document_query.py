"""Extraction invariants shared by served Calibre books and local files."""

import builtins
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import calibre_query as cq
import document_query as dq
import document_text as dt
import legacy_doc
from contracts import validate_source_record


def package(members):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in members:
            archive.writestr(name, data)
    return out.getvalue()


def epub(href="second.xhtml", broken=False):
    return package([
        ("OPS/first.xhtml", "<html><head><title>Book</title></head><body><h1>First chapter</h1><p>Alpha <em>error</em> correction. Criticism.</p></body></html>"),
        ("OPS/second.xhtml", "<html><head><title>Book</title><style>secret</style></head><body><h1>Second chapter</h1><p>Beta ERROR\nCORRECTION. Another error correction.</p><script>secret</script></body></html>"),
        ("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>'),
        ("OPS/book.opf", '<package><manifest><item id="one" href="first.xhtml" media-type="application/xhtml+xml"/><item id="two" href="' + href + '" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="two"/><itemref idref="' + ('missing' if broken else 'one') + '"/></spine></package>'),
        ("OPS/not-in-spine.xhtml", "<p>Unreferenced text is not reading-spine evidence.</p>"),
    ])


def served(data):
    return lambda *args: {"ok": True, "status": "complete", "bytes": data}


def blank_pdf():
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> >>"]
    data, offsets = b"%PDF-1.4\n", [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 4\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        data += f"{offset:010d} 00000 n \n".encode()
    return data + f"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


class DocumentExtractionTests(unittest.TestCase):
    def test_local_cli_preserves_unicode_on_binary_and_embedded_text_streams(self):
        class BinaryOutput:
            encoding = "ascii"

            def __init__(self):
                self.buffer = io.BytesIO()

            def write(self, text):
                raise AssertionError("native JSON must bypass the host text codec")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unicode.txt"
            original = "Ānanda – criticism and error correction.".encode("utf-8")
            path.write_bytes(original)
            for output in (BinaryOutput(), io.StringIO()):
                with self.subTest(stream=type(output).__name__), \
                        patch.object(dq.sys, "stdout", output):
                    result = dq.main(["--path", str(path), "read", "--section", "1"])
                emitted = (output.buffer.getvalue().decode("utf-8")
                           if hasattr(output, "buffer") else output.getvalue())
                self.assertEqual(result, 0)
                self.assertEqual(json.loads(emitted)["records"][0]["text"],
                                 original.decode("utf-8"))
                self.assertEqual(path.read_bytes(), original)

    def test_spine_order_inline_prose_and_multiple_occurrences(self):
        parsed = dt.parse_document(epub(), "EPUB")
        self.assertTrue(parsed["extraction_complete"])
        self.assertEqual([item["title"] for item in parsed["sections"]], ["Second chapter", "First chapter"])
        self.assertNotIn("secret", parsed["sections"][0]["text"])
        self.assertNotIn("Unreferenced", str(parsed))
        first = dt.select_text(parsed, "passages", query_text="error correction", limit=2, context=8)
        self.assertEqual(first["total"], 3)
        self.assertEqual(first["next_offset"], 2)
        last = dt.select_text(parsed, "passages", query_text="error correction", limit=2, offset=2)
        self.assertIsNone(last["next_offset"])
        self.assertEqual(last["records"][0]["section"], 2)
        match = last["records"][0]
        self.assertEqual(parsed["sections"][1]["text"][match["match_start"]:match["match_end"]], "error correction")

    def test_read_continuation_reassembles_exact_normalized_section(self):
        parsed = dt.parse_document(epub(), "EPUB")
        offset, parts = 0, []
        while offset is not None:
            result = dt.select_text(parsed, "read", section=1, chars=9, offset=offset)
            parts.extend(row["text"] for row in result["records"])
            offset = result["next_offset"]
        self.assertEqual("".join(parts), parsed["sections"][0]["text"])

    def test_unobserved_spine_and_external_resources_are_explicit(self):
        for data in (epub(broken=True), epub(href="https://example.com/chapter"), epub(href="../../outside.xhtml")):
            parsed = dt.parse_document(data, "EPUB")
            self.assertFalse(parsed["extraction_complete"])
            self.assertTrue(parsed["errors"])
        with self.assertRaises(dt.DocumentError):
            dt.parse_document(b"not an epub", "EPUB")
        with patch.object(dt, "MAX_EXPANDED_BYTES", 10):
            with self.assertRaisesRegex(dt.DocumentError, "expanded-byte"):
                dt.parse_document(epub(), "EPUB")
        with patch.object(dt, "MAX_TEXT_CHARS", 20):
            bounded = dt.parse_document(epub(), "EPUB")
            self.assertFalse(bounded["extraction_complete"])
            self.assertLessEqual(sum(len(row["text"]) for row in bounded["sections"]), 20)

    def test_literal_query_is_not_fts_or_regex_and_utf16_is_not_guessed(self):
        parsed = dt.parse_document("C++ café and (a|b).".encode("utf-16"), "TXT")
        result = dt.select_text(parsed, "passages", query_text="(a|b)")
        self.assertEqual(result["total"], 1)
        self.assertIn("café", parsed["sections"][0]["text"])
        with self.assertRaises(dt.DocumentError):
            dt.parse_document(b"\xff no encoding", "TXT")
        with self.assertRaisesRegex(dt.DocumentError, "unsupported"):
            dt.parse_document(b"unsupported ebook", "MOBI")

    def test_legacy_doc_uses_declared_native_reader_and_reports_unavailable(self):
        with patch("legacy_doc.read_doc", return_value={"engine": "Microsoft Word COM", "sections": [{"title": "Document body", "text": "Paragraph one\rParagraph two\x07Table cell"}]}):
            parsed = dt.parse_document(b"native doc", "DOC")
        self.assertEqual(parsed["sections"][0]["text"], "Paragraph one\nParagraph two Table cell")
        self.assertIn("temporary read-only", parsed["coverage"])
        with patch("legacy_doc.read_doc", side_effect=ValueError("Word unavailable")):
            with self.assertRaises(dt.DocumentError) as caught:
                dt.parse_document(b"native doc", "DOC")
        self.assertEqual(caught.exception.status, "unavailable")

    def test_word_worker_cleanup_is_bound_to_checkpoint_identity(self):
        header = json.dumps({"owned_process_pid": 12345, "owned_process_start_ticks": 638000000000000000}).encode()
        with patch("legacy_doc.subprocess.run") as runner:
            legacy_doc.cleanup_owned_worker(header)
        command = runner.call_args.args[0][-1]
        self.assertIn("12345", command)
        self.assertIn("638000000000000000", command)
        self.assertIn("WINWORD", command)
        self.assertIn("StartTime", command)
        with patch("legacy_doc.subprocess.run") as runner:
            legacy_doc.cleanup_owned_worker(b'{"owned_process_pid":"123; unsafe", "owned_process_start_ticks":1}')
            legacy_doc.cleanup_owned_worker(b"no checkpoint")
        runner.assert_not_called()

    def test_docx_body_tables_and_notes_keep_text_order(self):
        data = package([
            ("word/document.xml", '<w:document xmlns:w="urn:word"><w:body><w:p><w:r><w:t>First </w:t><w:t>paragraph</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'),
            ("word/footnotes.xml", '<w:footnotes xmlns:w="urn:word"><w:footnote><w:p><w:r><w:t>Footnote text</w:t></w:r></w:p></w:footnote></w:footnotes>'),
        ])
        parsed = dt.parse_document(data, "DOCX")
        self.assertEqual(parsed["sections"][0]["text"], "First paragraph\n\nTable cell")
        self.assertEqual(parsed["sections"][1]["title"], "Footnotes")

    def test_pdf_pages_missing_text_dependency_and_encryption(self):
        try:
            import pdfplumber
        except ImportError:
            self.skipTest("optional pdfplumber unavailable")
        fixture = Path(__file__).parent / "fixtures" / "pdf_annotations"
        parsed = dt.parse_document((fixture / "standard.pdf").read_bytes(), "PDF", max_pdf_pages=1)
        self.assertEqual(parsed["sections"][0]["page_number"], 1)
        self.assertIn("beta gamma", parsed["sections"][0]["text"])
        self.assertFalse(parsed["extraction_complete"])
        # Listing pages must not extract all their text. A selected-page read
        # must not be blocked by unrelated scanned pages or parse them first.
        with patch("pdfplumber.page.Page.extract_text", side_effect=AssertionError("contents extracted text")):
            listing = dt.parse_document((fixture / "standard.pdf").read_bytes(), "PDF", operation="contents")
        self.assertEqual(len(listing["sections"]), 2)
        self.assertIsNone(listing["sections"][0]["characters"])
        one = dt.parse_document((fixture / "standard.pdf").read_bytes(), "PDF", operation="read", section=2, max_pdf_pages=1)
        self.assertEqual([row["section"] for row in one["sections"]], [2])
        self.assertTrue(one["extraction_complete"])
        blank = dt.parse_document(blank_pdf(), "PDF")
        self.assertTrue(any("no extractable text" in row["error"] for row in blank["errors"]))
        with self.assertRaises(dt.DocumentError):
            dt.parse_document((fixture / "encrypted.pdf").read_bytes(), "PDF")
        original = builtins.__import__
        def missing(name, *args, **kwargs):
            if name == "pdfplumber":
                raise ImportError("missing")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=missing):
            with self.assertRaises(dt.DocumentError) as caught:
                dt.parse_document(b"PDF", "PDF")
        self.assertEqual(caught.exception.status, "unavailable")

    def test_served_records_have_hash_anchor_and_no_fake_reader_position(self):
        data = epub()
        result = cq.query("passages", library_id="Calibre_Library", book_id=72,
                          query_text="error correction", http_get=served(data), limit=2)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["scope"]["total"], 3)
        self.assertEqual(result["scope"]["representation_sha256"], hashlib.sha256(data).hexdigest())
        for row in result["records"]:
            validate_source_record(row)
            self.assertTrue(row["locator"].startswith("calibre-text://"))
            self.assertEqual(row["reader_locator"], "calibre://show-book/Calibre_Library/72")
        other = cq.query("passages", library_id="Calibre_Library", book_id=73,
                         query_text="error correction", http_get=served(data), limit=2)
        self.assertNotEqual(other["records"][0]["id"], result["records"][0]["id"])

    def test_scanned_page_ocr_is_bounded_and_remains_derived_partial_evidence(self):
        try:
            import pdfplumber
        except ImportError:
            self.skipTest("optional pdfplumber unavailable")
        with patch("pdf_ocr.recognize_pdf_page", return_value={"text": "Recognized critical tradition", "engine": "Windows.Media.Ocr", "language": "en-US"}) as ocr:
            result = cq.query("passages", library_id="Calibre_Library", book_id=81, book_format="PDF",
                              query_text="critical tradition", http_get=served(blank_pdf()), ocr=True)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["scope"]["ocr"])
        self.assertEqual(result["records"][0]["text_origin"], "ocr")
        self.assertEqual(result["records"][0]["completeness"], "partial")
        self.assertIn("verification", result["records"][0]["completeness_explanation"])
        self.assertEqual(ocr.call_count, 1)
        rejected = cq.query("read", library_id="Calibre_Library", book_id=81, book_format="PDF",
                            section=1, ocr=True, max_ocr_pages=0,
                            http_get=lambda *a: self.fail("invalid OCR bound fetched source"))
        self.assertEqual(rejected["status"], "unavailable")

    def test_invalid_selection_is_refused_before_network_and_stale_scope_is_exact(self):
        def forbidden(*args):
            self.fail("invalid selection attempted network access")
        for override in ({"section": 0}, {"section": 1, "chars": 0}, {"section": 1, "offset": -1}):
            result = cq.query("read", library_id="Calibre_Library", book_id=72, http_get=forbidden, **override)
            self.assertEqual(result["status"], "unavailable")
        prior = cq.query("read", library_id="Calibre_Library", book_id=72, section=1, http_get=served(epub()))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prior.json"
            path.write_text(json.dumps(prior), encoding="utf-8")
            failed = cq.query("read", library_id="Calibre_Library", book_id=72, section=2,
                              http_get=lambda *a: {"ok": False, "status": "unavailable", "error": "offline"}, fallback=path)
            self.assertEqual(failed["status"], "unavailable")
            self.assertIn("section", failed["errors"][-1]["fallback_error"])

    def test_local_file_preserves_bytes_and_limits_and_query_file_preserves_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "café.txt"
            data = "A café problem. Another CAFÉ problem.".encode("utf-8")
            path.write_bytes(data)
            result = dq.query(path, "passages", query_text="café", limit=1)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["scope"]["total"], 2)
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(dq.query(path, "read", section=1, max_bytes=2)["status"], "unavailable")
            query_path = Path(tmp) / "query.txt"
            query_path.write_text('authors:"Karl Popper"', encoding="utf-8-sig")
            args = cq.arguments(["--library-id", "Calibre_Library", "full-text", "--restrict-search", 'title:"Open Society"', "--query-file", str(query_path)])
            self.assertEqual(args.query, 'authors:"Karl Popper"')
            self.assertEqual(args.restrict_search, 'title:"Open Society"')


if __name__ == "__main__":
    unittest.main()
