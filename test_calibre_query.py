"""Offline contract tests for the read-only Calibre provider."""

import base64
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
import zipfile
from http.client import IncompleteRead
from pathlib import Path
from urllib.parse import unquote, urlsplit

import calibre_query as cq
import source_to_notes as stn
from contracts import validate_source_record


NOW = "2026-08-18T12:00:00Z"


class Completed:
    def __init__(self, payload=None, *, returncode=0, stderr=""):
        self.returncode = returncode
        self.stdout = (
            json.dumps(payload).encode("utf-8") if payload is not None else b"")
        self.stderr = stderr.encode("utf-8") if isinstance(stderr, str) else stderr


def make_epub(annotations=None, *, raw_member=None, include_member=True):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        if include_member:
            if raw_member is None:
                encoded = base64.b64encode(
                    json.dumps(annotations or []).encode("utf-8"))
                raw_member = cq.ANNOTATION_MAGIC + encoded
            archive.writestr(cq.ANNOTATION_MEMBER, raw_member)
    return output.getvalue()


def fetched(payload):
    def get(_url, _server, _username, _password_file, _timeout, _max_bytes):
        return {"ok": True, "status": "complete", "bytes": payload}
    return get


class CalibreQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.password_file = Path(self.tmp.name) / "password.txt"
        self.password_file.write_text("not-a-real-password", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, operation="list", runner=None, **overrides):
        values = {
            "server": "http://127.0.0.1:8081",
            "library_id": "Calibre_Library",
            "username": "reader",
            "password_file": self.password_file,
            "runner": runner or (lambda *args, **kwargs: Completed([])),
            "now": lambda: NOW,
        }
        values.update(overrides)
        return cq.query(operation, **values)

    def test_help_owns_exact_library_id_discovery(self):
        self.assertIn(
            "calibredb list --with-library http://127.0.0.1:8081/#-",
            cq.__doc__,
        )
        full_text = cq.arguments([
            "--library-id", "Calibre_Library", "full-text",
            "--book-id", "82", "Wheeler"])
        self.assertEqual(full_text.operation, "full-text")
        self.assertEqual(full_text.book_id, 82)
        annotations = cq.arguments([
            "--library-id", "Calibre_Library", "annotations",
            "--book-id", "82", "--format", "EPUB"])
        self.assertEqual(annotations.operation, "annotations")
        self.assertEqual(annotations.limit, cq.MAX_LIMIT)
        pdf_annotations = cq.arguments([
            "--library-id", "Calibre_Library", "annotations",
            "--book-id", "81", "--format", "pdf"])
        self.assertEqual(pdf_annotations.book_format, "PDF")
        self.assertEqual(pdf_annotations.max_pdf_pages,
                         cq.DEFAULT_MAX_PDF_PAGES)

    def test_non_loopback_targets_are_refused_before_running_calibredb(self):
        calls = []
        runner = lambda *args, **kwargs: calls.append(args)  # pragma: no cover
        for server in (
                "http://localhost:8081", "http://192.168.1.7:8081",
                "https://books.example:8081"):
            result = self.call(server=server, runner=runner)
            self.assertEqual(result["status"], "unavailable")
            self.assertIn("loopback", result["errors"][0]["error"])
        result = self.call(server="file:///library", runner=runner)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("http or https", result["errors"][0]["error"])
        self.assertEqual(calls, [])

    def test_command_is_read_only_and_password_never_appears_in_it(self):
        commands = []

        def runner(command, **kwargs):
            commands.append(command)
            return Completed([])

        result = self.call(runner=runner)
        self.assertEqual(result["status"], "complete")
        command = commands[0]
        self.assertEqual(command[1], "list")
        self.assertIn("--for-machine", command)
        self.assertNotIn("not-a-real-password", command)
        self.assertIn(f"<f:{self.password_file.as_posix()}>", command)
        forbidden = {"add", "remove", "set_metadata", "export", "local-write"}
        self.assertTrue(forbidden.isdisjoint(command))
        self.assertFalse(result["scope"]["local_write_used"])
        self.assertEqual(result["scope"]["authentication"], "username/password")

    def test_no_auth_is_a_valid_loopback_read_configuration(self):
        commands = []

        def runner(command, **kwargs):
            commands.append(command)
            return Completed([])

        result = self.call(
            runner=runner, username=None, password_file=None)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["scope"]["authentication"], "none")
        self.assertNotIn("--username", commands[0])
        self.assertNotIn("--password", commands[0])
        self.assertFalse(result["scope"]["local_write_used"])

    def test_live_records_preserve_identity_provenance_and_exact_metadata(self):
        row = {
            "id": 17,
            "uuid": "book-uuid",
            "title": "Geons, Black Holes and Quantum Foam",
            "authors": ["John Archibald Wheeler"],
            "comments": "<p>A life in physics.</p>",
            "formats": ["EPUB"],
            "tags": ["physics"],
            "last_modified": "2026-08-18T10:00:00+00:00",
        }
        result = self.call(runner=lambda *a, **k: Completed([row]))
        self.assertEqual(result["status"], "complete")
        record = result["records"][0]
        self.assertEqual(record["id"], "calibre:Calibre_Library:17")
        self.assertEqual(
            record["locator"],
            "calibre://show-book/Calibre_Library/17")
        self.assertEqual(record["provider_identity"], {
            "library_id": "Calibre_Library", "book_id": 17,
            "uuid": "book-uuid"})
        self.assertEqual(record["metadata"], row)
        self.assertIn("A life in physics.", record["text"])
        self.assertEqual(record["provenance"]["observed_at"], NOW)
        self.assertEqual(result["freshness"]["observed_at"], NOW)

    def test_calibre_machine_json_is_decoded_as_strict_utf8_without_replacement(self):
        row = {
            "id": 18,
            "uuid": "punctuation",
            "title": "Evolution – and explanation",
            "authors": ["Ānanda"],
        }
        machine = Completed()
        # Calibre 7.22 json.dumps() escapes these characters, then writes UTF-8.
        machine.stdout = json.dumps([row]).encode("utf-8")
        result = self.call(runner=lambda *a, **k: machine)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["records"][0]["title"], row["title"])
        self.assertEqual(result["records"][0]["authors"], row["authors"])
        self.assertNotIn("�", result["records"][0]["text"])

        invalid = Completed()
        invalid.stdout = b'[{"id": 19, "title": "Evolution \x96 and"}]'
        result = self.call(runner=lambda *a, **k: invalid)
        self.assertEqual(result["status"], "indeterminate")
        self.assertEqual(result["records"], [])
        self.assertIn("invalid UTF-8 JSON", result["errors"][0]["error"])

    def test_cli_json_output_is_utf8_independent_of_text_stream_codepage(self):
        class BinaryOutput:
            def __init__(self):
                self.buffer = io.BytesIO()

        output = BinaryOutput()
        cq.emit_json_utf8({"title": "Evolution – Ānanda"}, stream=output)
        raw = output.buffer.getvalue()
        self.assertEqual(
            raw, b'{\n  "title": "Evolution \xe2\x80\x93 \xc4\x80nanda"\n}\n')
        self.assertEqual(json.loads(raw.decode("utf-8"))["title"],
                         "Evolution – Ānanda")

    def test_search_is_bounded_and_reports_truncation_as_partial(self):
        commands = []
        rows = [
            {"id": number, "title": f"Book {number}", "uuid": f"u{number}"}
            for number in range(1, 4)
        ]

        def runner(command, **kwargs):
            commands.append(command)
            return Completed(rows)

        result = self.call(
            "search", runner=runner, query_text="tags:physics", limit=2)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["scope"]["truncated"])
        self.assertEqual(len(result["records"]), 2)
        command = commands[0]
        self.assertEqual(command[command.index("--limit") + 1], "3")
        self.assertEqual(command[command.index("--search") + 1], "tags:physics")

    def test_public_maximum_limit_allows_one_internal_sentinel(self):
        commands = []

        def runner(command, **kwargs):
            commands.append(command)
            return Completed([])

        result = self.call(limit=500, runner=runner)
        self.assertEqual(result["status"], "complete")
        command = commands[0]
        self.assertEqual(command[command.index("--limit") + 1], "501")

        refused = self.call(limit=501, runner=runner)
        self.assertEqual(refused["status"], "unavailable")
        self.assertIn("between 1 and 500", refused["errors"][0]["error"])
        self.assertEqual(len(commands), 1)

    def test_full_text_is_book_bounded_read_only_and_preserves_raw_snippet(self):
        commands = []
        row = {
            "book_id": 82,
            "format": "EPUB",
            "title": "Geons, Black Holes, and Quantum Foam",
            "authors": ["John Archibald Wheeler"],
            "text": ("... John Archibald " + cq.FTS_MATCH_START + "Wheeler"
                     + cq.FTS_MATCH_END + " described it ..."),
        }

        def runner(command, **kwargs):
            commands.append(command)
            return Completed([row])

        result = self.call(
            "full-text", runner=runner, book_id=82,
            query_text="Wheeler", exact=True)
        self.assertEqual(result["status"], "complete")
        command = commands[0]
        self.assertEqual(command[1], "fts_search")
        self.assertEqual(command[command.index("--restrict-to") + 1], "ids:82")
        self.assertEqual(
            command[command.index("--indexing-threshold") + 1], "100")
        self.assertIn("--do-not-match-on-related-words", command)
        self.assertNotIn("not-a-real-password", command)
        self.assertIn(f"<f:{self.password_file.as_posix()}>", command)
        self.assertFalse(result["scope"]["local_write_used"])
        self.assertIn("not every occurrence",
                      result["scope"]["occurrence_coverage"])
        record = result["records"][0]
        self.assertEqual(record["metadata"], row)
        self.assertEqual(record["text"], row["text"])
        self.assertEqual(record["format"], "EPUB")
        open_at = unquote(urlsplit(record["locator"]).query.split("=", 1)[1])
        self.assertTrue(open_at.startswith("search:"))
        self.assertNotIn(cq.FTS_MATCH_START, open_at)

    def test_full_text_locator_preserves_multi_term_match_context(self):
        snippet = (
            "… " + cq.FTS_MATCH_START + "John" + cq.FTS_MATCH_END
            + " Archibald " + cq.FTS_MATCH_START + "Wheeler"
            + cq.FTS_MATCH_END + " described geons …")
        result = self.call(
            "full-text", book_id=82, query_text="John Wheeler",
            runner=lambda *a, **k: Completed([{
                "book_id": 82, "format": "EPUB", "title": "Geons",
                "text": snippet,
            }]))
        record = result["records"][0]
        open_at = unquote(urlsplit(record["locator"]).query.split("=", 1)[1])
        self.assertEqual(
            open_at, "search:John Archibald Wheeler described geons")

    def test_full_text_refuses_incomplete_index_and_escaped_book_rows(self):
        unavailable = self.call(
            "full-text", book_id=82, query_text="Wheeler",
            runner=lambda *a, **k: Completed(
                returncode=1,
                stderr="12 files out of 20 are not yet indexed"))
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertIn("not yet indexed", unavailable["errors"][0]["error"])

        escaped = self.call(
            "full-text", book_id=82, query_text="Wheeler",
            runner=lambda *a, **k: Completed([{
                "book_id": 83, "format": "EPUB", "title": "Other",
                "text": "Wheeler",
            }]))
        self.assertEqual(escaped["status"], "indeterminate")
        self.assertIn("escaped book restriction", escaped["errors"][0]["error"])

        non_integer = self.call(
            "full-text", book_id=82.5, query_text="Wheeler")
        self.assertEqual(non_integer["status"], "unavailable")
        self.assertIn("positive integer", non_integer["errors"][0]["error"])

    def test_full_text_bounds_format_rows_and_exact_fallback_scope(self):
        rows = [
            {"book_id": 82, "format": fmt, "title": "Geons", "text": "hit"}
            for fmt in ("EPUB", "PDF")
        ]
        prior = self.call(
            "full-text", book_id=82, query_text="Wheeler", limit=1,
            runner=lambda *a, **k: Completed(rows))
        self.assertEqual(prior["status"], "partial")
        self.assertTrue(prior["scope"]["truncated"])
        self.assertEqual(prior["scope"]["raw_rows"], 2)
        self.assertEqual(len(prior["records"]), 1)
        snapshot = Path(self.tmp.name) / "fts.json"
        snapshot.write_text(json.dumps(prior), encoding="utf-8")
        failed = self.call(
            "full-text", book_id=83, query_text="Wheeler", limit=1,
            runner=lambda *a, **k: Completed(
                returncode=1, stderr="connection refused"),
            fallback=snapshot)
        self.assertEqual(failed["status"], "unavailable")
        self.assertIn("book_id", failed["errors"][-1]["fallback_error"])

    def test_annotations_recover_current_last_read_with_exact_raw_evidence(self):
        annotation = {
            "pos": "epubcfi(/16/2/4[text]/2[ch01]/4/2/2:163)",
            "pos_type": "epubcfi",
            "timestamp": "2026-08-19T08:04:35.453648+00:00",
            "type": "last-read",
            "future_field": {"preserved": True},
        }
        result = self.call(
            "annotations", book_id=82,
            http_get=fetched(make_epub([annotation])))
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["scope"]["annotation_member_present"])
        self.assertEqual(result["scope"]["raw_rows"], 1)
        self.assertIn("web-user", " ".join(
            result["scope"]["unobserved_namespaces"]))
        record = result["records"][0]
        self.assertEqual(record["annotation_type"], "last-read")
        self.assertEqual(record["metadata"], annotation)
        self.assertEqual(record["position"], annotation["pos"])
        self.assertEqual(
            unquote(urlsplit(record["locator"]).query.split("=", 1)[1]),
            annotation["pos"])

    def test_annotations_preserve_highlight_notes_unknown_fields_and_types(self):
        highlight = {
            "type": "highlight", "uuid": "highlight-1",
            "highlighted_text": "space tells matter how to move",
            "notes": "Wheeler formulation", "spine_index": 2,
            "start_cfi": "/4/2:7", "style": {"kind": "color", "which": "yellow"},
        }
        future = {"type": "future-annotation", "opaque": [1, 2, 3]}
        result = self.call(
            "annotations", book_id=82,
            http_get=fetched(make_epub([highlight, future])))
        self.assertEqual(result["status"], "complete")
        first, second = result["records"]
        self.assertEqual(first["provider_identity"]["annotation_id"], "highlight-1")
        self.assertEqual(first["position"], "epubcfi(/6/4/2:7)")
        self.assertIn("Wheeler formulation", first["text"])
        self.assertEqual(first["metadata"]["style"], highlight["style"])
        self.assertEqual(second["annotation_type"], "future-annotation")
        self.assertEqual(second["metadata"], future)

    def test_annotations_expose_withdrawal_and_do_not_link_arbitrary_positions(self):
        annotations = [
            {
                "type": "bookmark", "title": "Deleted bookmark",
                "pos": "epubcfi(/6)", "pos_type": "epubcfi",
                "removed": True,
            },
            {
                "type": "bookmark", "title": "Opaque position",
                "pos": "chapter-one:37",
            },
        ]
        result = self.call(
            "annotations", book_id=82,
            http_get=fetched(make_epub(annotations)))
        withdrawn, opaque = result["records"]
        self.assertEqual(withdrawn["withdrawal_state"], "withdrawn")
        self.assertTrue(withdrawn["metadata"]["removed"])
        self.assertEqual(withdrawn["position"], "epubcfi(/6)")
        self.assertEqual(urlsplit(withdrawn["locator"]).query, "")
        self.assertEqual(opaque["withdrawal_state"], "active")
        self.assertIsNone(opaque["position"])
        self.assertEqual(urlsplit(opaque["locator"]).query, "")
        self.assertEqual(opaque["metadata"]["pos"], "chapter-one:37")

        malformed_cfis = self.call(
            "annotations", book_id=82,
            http_get=fetched(make_epub([
                {
                    "type": "bookmark", "title": "No slash",
                    "pos": "epubcfi(foo)", "pos_type": "epubcfi",
                },
                {
                    "type": "bookmark", "title": "Trailing garbage",
                    "pos": "epubcfi(/16garbage)", "pos_type": "epubcfi",
                },
                {
                    "type": "bookmark", "title": "Zero selector",
                    "pos": "epubcfi(/0)", "pos_type": "epubcfi",
                },
                {
                    "type": "bookmark", "title": "Odd selector",
                    "pos": "epubcfi(/3)", "pos_type": "epubcfi",
                },
                {
                    "type": "bookmark", "title": "Escaped parenthesis",
                    "pos": "epubcfi(/6/4:2[pre^),post])",
                    "pos_type": "epubcfi",
                },
            ])))
        for record in malformed_cfis["records"][:4]:
            self.assertIsNone(record["position"])
            self.assertEqual(urlsplit(record["locator"]).query, "")
        escaped = malformed_cfis["records"][4]
        self.assertEqual(
            escaped["position"], "epubcfi(/6/4:2[pre^),post])")
        self.assertNotEqual(urlsplit(escaped["locator"]).query, "")

    def test_annotations_preserve_exact_native_bookmark_title_identity(self):
        result = self.call(
            "annotations", book_id=82,
            http_get=fetched(make_epub([
                {"type": "bookmark", "title": "Chapter"},
                {"type": "bookmark", "title": " Chapter "},
            ])))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["records"]), 2)
        self.assertNotEqual(result["records"][0]["id"],
                            result["records"][1]["id"])

    def test_annotations_distinguish_empty_surface_from_all_calibre_absence(self):
        result = self.call(
            "annotations", book_id=82,
            http_get=fetched(make_epub(include_member=False)))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["records"], [])
        self.assertFalse(result["scope"]["annotation_member_present"])
        self.assertIn("not proof", result["scope"]["absence_meaning"])

    def test_annotations_recover_valid_legacy_bookmark_and_last_read(self):
        legacy = (
            "Chapter One*|!|?|*0*|!|?|*/4/2:7\n"
            "calibre_current_page_bookmark*|!|?|*7*|!|?|*/2/4:163\n"
        ).encode("utf-8")
        result = self.call(
            "annotations", book_id=82,
            http_get=fetched(make_epub(raw_member=legacy)))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["scope"]["annotation_storage_format"], "legacy")
        bookmark, last_read = result["records"]
        self.assertEqual(bookmark["annotation_type"], "bookmark")
        self.assertEqual(bookmark["metadata"]["title"], "Chapter One")
        self.assertEqual(bookmark["position"], "epubcfi(/2/4/2:7)")
        self.assertEqual(last_read["annotation_type"], "last-read")
        self.assertEqual(last_read["position"], "epubcfi(/16/2/4:163)")
        self.assertEqual(last_read["timestamp"], cq.LEGACY_TIMESTAMP)

    def test_annotations_refuse_malformed_or_unbounded_representations(self):
        malformed = self.call(
            "annotations", book_id=82, http_get=fetched(b"not an epub"))
        self.assertEqual(malformed["status"], "indeterminate")
        self.assertIn("EPUB ZIP", malformed["errors"][0]["error"])

        legacy_noise = self.call(
            "annotations", book_id=82,
            http_get=fetched(make_epub(raw_member=b"old-format")))
        self.assertEqual(legacy_noise["status"], "indeterminate")
        self.assertEqual(legacy_noise["records"], [])
        self.assertEqual(
            legacy_noise["scope"]["annotation_storage_format"], "legacy")
        self.assertEqual(legacy_noise["scope"]["raw_rows"], 1)
        self.assertEqual(legacy_noise["scope"]["migration_skipped_rows"], 1)
        self.assertIn("unrecognized-line",
                      legacy_noise["scope"]["migration_skip_reasons"])

        invalid_utf8 = self.call(
            "annotations", book_id=82,
            http_get=fetched(make_epub(raw_member=b"\xff")))
        self.assertEqual(invalid_utf8["status"], "indeterminate")
        self.assertIn("not UTF-8", invalid_utf8["errors"][0]["error"])

        nonfinite = cq.ANNOTATION_MAGIC + base64.b64encode(
            b'[{"type":"future","value":NaN}]')
        nonfinite_result = self.call(
            "annotations", book_id=82,
            http_get=fetched(make_epub(raw_member=nonfinite)))
        self.assertEqual(nonfinite_result["status"], "indeterminate")
        self.assertIn("non-finite", nonfinite_result["errors"][0]["error"])

        mixed_legacy = self.call(
            "annotations", book_id=82,
            http_get=fetched(make_epub(raw_member=(
                b"Chapter One*|!|?|*0*|!|?|*/4/2:7\n"
                b"Numeric*|!|?|*0*|!|?|*0.5\n"))))
        self.assertEqual(mixed_legacy["status"], "partial")
        self.assertEqual(mixed_legacy["scope"]["raw_rows"], 2)
        self.assertEqual(mixed_legacy["scope"]["recoverable_rows"], 1)
        self.assertEqual(mixed_legacy["scope"]["migration_skipped_rows"], 1)

        calls = []
        refused = self.call(
            "annotations", book_id=82,
            max_book_bytes=cq.MAX_MAX_BOOK_BYTES + 1,
            http_get=lambda *args: calls.append(args))
        self.assertEqual(refused["status"], "unavailable")
        self.assertEqual(calls, [])

    def test_annotations_preserve_partial_status_for_malformed_rows(self):
        annotations = [
            {"type": "bookmark", "title": "Chapter one", "pos": "epubcfi(/6)"},
            "not an object",
        ]
        result = self.call(
            "annotations", book_id=82,
            http_get=fetched(make_epub(annotations)))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["records"]), 1)
        self.assertIn("not an object", result["errors"][0]["error"])

        bounded = self.call(
            "annotations", book_id=82, limit=1,
            http_get=fetched(make_epub([
                {"type": "bookmark", "title": "One", "pos": "epubcfi(/2)"},
                {"type": "bookmark", "title": "Two", "pos": "epubcfi(/4)"},
            ])))
        self.assertEqual(bounded["status"], "partial")
        self.assertTrue(bounded["scope"]["truncated"])
        self.assertEqual(bounded["scope"]["raw_rows"], 2)
        self.assertEqual(len(bounded["records"]), 1)

    def test_annotations_propagate_bounded_get_failure_without_fabricating_rows(self):
        def failed(*args):
            return {"ok": False, "status": "unavailable",
                    "error": "Content Server GET failed with HTTP 404"}

        result = self.call("annotations", book_id=82, http_get=failed)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["records"], [])
        self.assertIn("HTTP 404", result["errors"][0]["error"])

    def test_pdf_annotations_preserve_native_page_geometry_quote_and_note(self):
        native = {
            "subtype": "Highlight",
            "page_index": 4,
            "page_number": 5,
            "page_label": "xiii",
            "page_rotation": 0,
            "media_box": [0, 0, 612, 792],
            "crop_box": [0, 0, 612, 792],
            "coordinate_space": "PDF default user space",
            "rect": [72, 700, 240, 714],
            "quad_points": [[72, 714, 240, 714, 72, 700, 240, 700]],
            "native_id": "sumatra-mark-1",
            "derived_identity": "unused-derived-id",
            "identity_source": "NM",
            "quote": "space tells matter how to move",
            "contents": "Wheeler formulation",
            "created": "2026-08-24T10:00:00+05:30",
            "modified": "2026-08-24T10:01:00+05:30",
            "color": [1, 1, 0],
            "opacity": 0.5,
            "completeness": "complete",
        }

        def parser(payload, **bounds):
            self.assertEqual(payload, b"%PDF-fixture")
            self.assertEqual(bounds["max_pages"], cq.DEFAULT_MAX_PDF_PAGES)
            self.assertEqual(
                bounds["max_annotations"], cq.DEFAULT_MAX_PDF_ANNOTATIONS)
            return {
                "records": [native], "page_count": 7,
                "observed_rows": 1, "unsupported_rows": 0,
                "unsupported_subtypes": {}, "malformed_rows": 0,
                "malformed_reasons": {}, "warnings": [],
                "pages_with_annotations": [4],
                "annotation_surface_present": True,
                "parser": "fixture-parser",
            }

        result = self.call(
            "annotations", book_id=81, book_format="PDF",
            http_get=fetched(b"%PDF-fixture"), pdf_parser=parser)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["scope"]["annotation_storage_format"],
                         "PDF page /Annots")
        self.assertEqual(result["scope"]["raw_rows"], 1)
        record = result["records"][0]
        self.assertEqual(record["quote"], native["quote"])
        self.assertEqual(record["note"], native["contents"])
        self.assertEqual(record["position"], "pdf-page:5")
        self.assertEqual(record["position_data"]["quad_points"],
                         native["quad_points"])
        self.assertEqual(record["provider_identity"]["native_annotation_id"],
                         "sumatra-mark-1")
        self.assertEqual(
            record["reader_locator"],
            "calibre://show-book/Calibre_Library/81")
        self.assertTrue(record["locator"].startswith(
            "calibre-pdf://annotation/Calibre_Library/81/"))
        self.assertEqual(urlsplit(record["locator"]).query, "")
        self.assertEqual(
            record["provenance"]["representation_sha256"],
            hashlib.sha256(b"%PDF-fixture").hexdigest())
        self.assertIs(validate_source_record(record), record)

    def test_pdf_annotations_report_unsupported_and_incomplete_without_false_empty(self):
        note_only = {
            "subtype": "Highlight", "page_index": 0, "page_number": 1,
            "page_label": None, "rect": [10, 10, 20, 20],
            "quad_points": [[10, 20, 20, 20, 10, 10, 20, 10]],
            "native_id": None, "derived_identity": "geometry-1",
            "identity_source": "geometry-fingerprint", "quote": "",
            "contents": "Geometry-only reader note", "completeness": "partial",
            "completeness_explanation": (
                "highlight geometry intersects no recoverable text layer"),
        }
        geometry_only = dict(
            note_only, contents="", derived_identity="geometry-2")

        def parser(_payload, **_bounds):
            return {
                "records": [note_only, geometry_only], "page_count": 1,
                "observed_rows": 3, "unsupported_rows": 1,
                "unsupported_subtypes": {"Link": 1},
                "malformed_rows": 0, "malformed_reasons": {},
                "warnings": [], "pages_with_annotations": [0],
                "annotation_surface_present": True,
                "parser": "fixture-parser",
            }

        result = self.call(
            "annotations", book_id=81, book_format="PDF",
            http_get=fetched(b"%PDF-fixture"), pdf_parser=parser)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["records"]), 1)
        self.assertFalse(result["records"][0]["source_text_available"])
        self.assertTrue(result["records"][0]["reader_note_available"])
        self.assertEqual(
            result["records"][0]["note"], "Geometry-only reader note")
        geometry_errors = [
            error for error in result["errors"]
            if error.get("annotation_evidence", {}).get(
                "derived_identity") == "geometry-2"]
        self.assertEqual(len(geometry_errors), 1)
        self.assertIn("no recoverable quote", geometry_errors[0]["error"])
        self.assertEqual(
            geometry_errors[0]["annotation_evidence"]["page_number"], 1)
        self.assertEqual(
            geometry_errors[0]["annotation_evidence"]["rect"],
            geometry_only["rect"])
        self.assertEqual(
            geometry_errors[0]["annotation_evidence"]["quad_points"],
            geometry_only["quad_points"])
        self.assertEqual(result["scope"]["unsupported_subtypes"], {"Link": 1})
        self.assertIn("external-reader", result["scope"]["absence_meaning"])
        self.assertGreaterEqual(len(result["errors"]), 2)

    def test_pdf_annotation_locators_are_unique_but_reader_link_stays_book_level(self):
        first = {
            "subtype": "Highlight", "page_index": 0, "page_number": 1,
            "rect": [10, 10, 20, 20],
            "quad_points": [10, 20, 20, 20, 10, 10, 20, 10],
            "native_id": "first", "derived_identity": "unused-first",
            "identity_source": "pdf:/NM", "quote": "First exact quote.",
            "contents": "", "completeness": "complete",
        }
        second = dict(
            first, native_id="second", derived_identity="unused-second",
            quote="Second exact quote.")

        def parser(_payload, **_bounds):
            return {
                "records": [first, second], "page_count": 1,
                "observed_rows": 2, "unsupported_rows": 0,
                "unsupported_subtypes": {}, "malformed_rows": 0,
                "malformed_reasons": {}, "warnings": [],
                "pages_with_annotations": [0],
                "annotation_surface_present": True,
                "parser": "fixture-parser",
            }

        result = self.call(
            "annotations", book_id=81, book_format="PDF",
            http_get=fetched(b"%PDF-fixture"), pdf_parser=parser)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len({record["locator"] for record in result["records"]}), 2)
        self.assertEqual({record["reader_locator"] for record in result["records"]}, {
            "calibre://show-book/Calibre_Library/81"})
        self.assertEqual(len(stn.source_records(result)), 2)

    def test_pdf_annotations_distinguish_valid_empty_missing_dependency_and_corruption(self):
        def empty(_payload, **_bounds):
            return {
                "records": [], "page_count": 2, "observed_rows": 0,
                "unsupported_rows": 0, "unsupported_subtypes": {},
                "malformed_rows": 0, "malformed_reasons": {},
                "warnings": [], "pages_with_annotations": [],
                "annotation_surface_present": False,
                "parser": "fixture-parser",
            }

        valid = self.call(
            "annotations", book_id=81, book_format="PDF",
            http_get=fetched(b"%PDF-empty"), pdf_parser=empty)
        self.assertEqual(valid["status"], "complete")
        self.assertEqual(valid["records"], [])
        self.assertIn("served PDF", valid["scope"]["absence_meaning"])

        def missing(_payload, **_bounds):
            raise cq.PDFDependencyUnavailable("pdfplumber is not installed")

        unavailable = self.call(
            "annotations", book_id=81, book_format="PDF",
            http_get=fetched(b"%PDF-empty"), pdf_parser=missing)
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertIn("pdfplumber", unavailable["errors"][0]["error"])

        def corrupt(_payload, **_bounds):
            raise cq.PDFExtractionError("PDF cross-reference is unreadable")

        indeterminate = self.call(
            "annotations", book_id=81, book_format="PDF",
            http_get=fetched(b"%PDF-broken"), pdf_parser=corrupt)
        self.assertEqual(indeterminate["status"], "indeterminate")
        self.assertEqual(indeterminate["records"], [])

        def encrypted(_payload, **_bounds):
            raise cq.PDFExtractionError(
                "PDF requires a password", code="encrypted")

        unavailable_encrypted = self.call(
            "annotations", book_id=81, book_format="PDF",
            http_get=fetched(b"%PDF-encrypted"), pdf_parser=encrypted)
        self.assertEqual(unavailable_encrypted["status"], "unavailable")
        self.assertEqual(
            unavailable_encrypted["errors"][0]["code"], "pdf_encrypted")

        def page_bounded(_payload, **_bounds):
            return {
                "records": [], "page_count": 3000, "observed_rows": 0,
                "unsupported_rows": 0, "unsupported_subtypes": {},
                "malformed_rows": 0, "malformed_reasons": {},
                "warnings": ["page bound reached"],
                "pages_with_annotations": [],
                "annotation_surface_present": False,
                "truncated": {"pages": True, "annotations": False},
                "parser": "fixture-parser",
            }

        bounded = self.call(
            "annotations", book_id=81, book_format="PDF",
            http_get=fetched(b"%PDF-large"), pdf_parser=page_bounded)
        self.assertEqual(bounded["status"], "partial")
        self.assertTrue(bounded["scope"]["truncated"])
        self.assertEqual(bounded["scope"]["pdf_truncated"], {
            "pages": True, "annotations": False})

    def test_pdf_annotation_bounds_are_validated_before_fetch(self):
        calls = []
        result = self.call(
            "annotations", book_id=81, book_format="PDF",
            max_pdf_pages=cq.MAX_MAX_PDF_PAGES + 1,
            http_get=lambda *args: calls.append(args))
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("max PDF pages", result["errors"][0]["error"])
        self.assertEqual(calls, [])

    def test_book_get_rejects_redirected_origin_and_declared_oversize(self):
        class Response:
            def __init__(self, final_url, payload=b"book", declared=None):
                self.final_url = final_url
                self.payload = payload
                self.headers = {}
                if declared is not None:
                    self.headers["Content-Length"] = str(declared)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def geturl(self):
                return self.final_url

            def read(self, size):
                return self.payload[:size]

        captured = {}

        class Opener:
            def __init__(self, response):
                self.response = response

            def open(self, request, timeout):
                captured["request"] = request
                captured["timeout"] = timeout
                return self.response

        def factory_for(response):
            def factory(*handlers):
                captured["handlers"] = handlers
                return Opener(response)
            return factory

        url = "http://127.0.0.1:8081/get/EPUB/82/Calibre_Library"
        escaped = cq.fetch_bytes(
            url, "http://127.0.0.1:8081", "reader", self.password_file,
            20, 100, opener_factory=factory_for(
                Response("https://books.example/get/EPUB/82/library")))
        self.assertEqual(escaped["status"], "unavailable")
        self.assertIn("escaped", escaped["error"])
        self.assertNotIn("not-a-real-password", captured["request"].full_url)
        self.assertTrue(any(isinstance(handler, cq.ProxyHandler)
                            and not handler.proxies
                            for handler in captured["handlers"]))
        self.assertTrue(any(isinstance(handler, cq._NoRedirect)
                            for handler in captured["handlers"]))

        oversized = cq.fetch_bytes(
            url, "http://127.0.0.1:8081", None, None, 20, 100,
            opener_factory=factory_for(Response(url, declared=101)))
        self.assertEqual(oversized["status"], "unavailable")
        self.assertIn("exceeds 100 bytes", oversized["error"])

        class InterruptedResponse(Response):
            def read(self, size):
                raise IncompleteRead(b"partial", 100)

        interrupted = cq.fetch_bytes(
            url, "http://127.0.0.1:8081", None, None, 20, 100,
            opener_factory=factory_for(InterruptedResponse(url)))
        self.assertEqual(interrupted["status"], "indeterminate")
        self.assertIn("IncompleteRead", interrupted["error"])

    def test_known_unavailability_and_uncertain_output_are_distinct(self):
        def missing(*args, **kwargs):
            raise FileNotFoundError("calibredb")

        result = self.call(runner=missing)
        self.assertEqual(result["status"], "unavailable")

        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], 20)

        result = self.call(runner=timeout)
        self.assertEqual(result["status"], "indeterminate")

        malformed = Completed()
        malformed.stdout = b"not json"
        result = self.call(runner=lambda *a, **k: malformed)
        self.assertEqual(result["status"], "indeterminate")

    def test_an_exact_prior_result_can_be_exposed_only_as_stale(self):
        row = {"id": 2, "title": "The Selfish Gene", "uuid": "gene"}
        prior = self.call(runner=lambda *a, **k: Completed([row]))
        snapshot = Path(self.tmp.name) / "prior.json"
        snapshot.write_text(json.dumps(prior), encoding="utf-8")

        failed = self.call(
            runner=lambda *a, **k: Completed(
                returncode=1, stderr="connection refused"),
            fallback=snapshot)
        self.assertEqual(failed["status"], "stale")
        self.assertEqual(failed["records"], prior["records"])
        self.assertEqual(failed["freshness"]["stale_as_of"], NOW)
        self.assertIn("connection refused", failed["errors"][-1]["live_error"])

    def test_fallback_with_different_query_is_refused(self):
        row = {"id": 2, "title": "The Selfish Gene", "uuid": "gene"}
        prior = self.call(
            "search", runner=lambda *a, **k: Completed([row]),
            query_text="title:gene")
        snapshot = Path(self.tmp.name) / "prior.json"
        snapshot.write_text(json.dumps(prior), encoding="utf-8")

        failed = self.call(
            "search", runner=lambda *a, **k: Completed(
                returncode=1, stderr="connection refused"),
            query_text="title:wheeler", fallback=snapshot)
        self.assertEqual(failed["status"], "unavailable")
        self.assertIn("fallback scope differs", failed["errors"][-1]["fallback_error"])

    def test_failed_prior_result_cannot_be_relabelled_as_stale_evidence(self):
        prior = self.call(
            runner=lambda *a, **k: Completed(
                returncode=1, stderr="connection refused"))
        self.assertEqual(prior["status"], "unavailable")
        snapshot = Path(self.tmp.name) / "failed-prior.json"
        snapshot.write_text(json.dumps(prior), encoding="utf-8")

        failed = self.call(
            runner=lambda *a, **k: Completed(
                returncode=1, stderr="still unavailable"),
            fallback=snapshot)
        self.assertEqual(failed["status"], "unavailable")
        self.assertEqual(failed["records"], [])
        self.assertIn(
            "not a prior complete or partial observation",
            failed["errors"][-1]["fallback_error"])

    def test_partial_authentication_configuration_is_refused(self):
        result = self.call(username="reader", password_file=None)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("must be supplied together", result["errors"][0]["error"])
        result = self.call(username=None, password_file=self.password_file)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("must be supplied together", result["errors"][0]["error"])


if __name__ == "__main__":
    unittest.main()
