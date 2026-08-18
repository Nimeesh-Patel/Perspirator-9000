"""Offline contract tests for the read-only Calibre provider."""

import json
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

import calibre_query as cq


NOW = "2026-08-18T12:00:00Z"


class Completed:
    def __init__(self, payload=None, *, returncode=0, stderr=""):
        self.returncode = returncode
        self.stdout = (
            json.dumps(payload).encode("utf-8") if payload is not None else b"")
        self.stderr = stderr.encode("utf-8") if isinstance(stderr, str) else stderr


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
