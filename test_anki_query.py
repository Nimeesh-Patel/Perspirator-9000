import io
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

import anki_query as aq


class AnkiQueryTests(unittest.TestCase):
    def invoke_response(self, body, action="notesInfo"):
        response = io.BytesIO(json.dumps(body).encode())
        with patch.object(aq.urllib.request, "urlopen", return_value=response):
            return aq.invoke(action)

    def test_malformed_envelopes_and_null_reads_are_errors(self):
        for body in ([], {}, {"result": []}, {"result": None, "error": None},
                     {"result": {}, "error": None}):
            with self.subTest(body=body):
                self.assertFalse(self.invoke_response(body)["ok"])
        self.assertFalse(self.invoke_response({"result": True, "error": None}, "version")["ok"])
        self.assertTrue(self.invoke_response({"result": 6, "error": None}, "version")["ok"])

    def test_exact_note_id_and_cardinality_are_required(self):
        for data in (None, [], [{}, {}], [None], [{"noteId": 99}],
                     [{"noteId": 1, "fields": {"Front": None}}]):
            with self.subTest(data=data), patch.object(aq, "invoke", return_value={
                "ok": True, "status": "ok", "action": "notesInfo", "data": data, "error": None,
            }):
                result = aq.notes([1])
                self.assertFalse(result["ok"])
                self.assertIsNone(result["data"])

    def test_empty_native_record_confirms_absence(self):
        with patch.object(aq, "invoke", return_value={"ok": True, "data": [{}]}):
            self.assertEqual(aq.notes([1])["data"], [{"note_id": 1, "present": False}])

    def test_exact_native_fields_are_preserved(self):
        raw = {"noteId": 1, "modelName": "Basic", "fields": {"Front": {"value": "<b>Exact</b>"}},
               "cards": [2], "tags": ["source"]}
        with patch.object(aq, "invoke", return_value={"ok": True, "data": [raw]}):
            record = aq.notes([1])["data"][0]
            self.assertTrue(record["present"])
            self.assertEqual(record["fields"], {"Front": "<b>Exact</b>"})

    def test_writes_remain_unavailable(self):
        with self.assertRaises(ValueError):
            aq.invoke("deleteNotes", notes=[1])


class AnkiSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vault = self.root / "Vault & ideas"
        self.vault.mkdir()
        self.note = self.vault / "Clippings" / "A problem.v2.md"
        self.note.parent.mkdir()
        self.note.write_text("---\nanki_note_id: '11'\n---\nA problem\n***\nAn answer", encoding="utf-8")
        self.uri = ("obsidian://open?vault=" + quote(self.vault.name, safe="-_.!~*'()")
                    + "&file=" + quote("Clippings/A problem.v2", safe="-_.!~*'()"))
        self.front = ('<div style="text-align:right;font-size:0.75em;margin-bottom:6px;opacity:0.6;">'
                      f'<a href="{self.uri}">A problem.v2 ↗</a></div><p>Question</p>')
        self.card = {"card_id": 21, "note_id": 11, "ordinal": 0,
                     "state": {"deck_id": 1, "original_deck_id": 0, "type": 2,
                               "queue": 2, "due": 1876, "original_due": 0,
                               "interval": 7, "reps": 3, "lapses": 1},
                     "source_front_html": self.front, "future_card_field": {"keep": True}}
        self.capture = {
            "provider": "ankidroid", "capability": "card-state", "status": "complete",
            "scope": {"queries": ["is:due"], "limit_per_query": 200, "anki_version": "2.25.1",
                      "collection_identity": None},
            "freshness": {"started_at": "2026-11-05T01:00:00Z", "finished_at": "2026-11-05T01:00:01Z",
                          "timezone_offset_minutes": 330},
            "records": [{"query": "is:due", "status": "complete", "observed_at_ms": 1793840400000,
                         "total": 1, "cards": [self.card], "errors": []}],
            "errors": [], "future_provider_field": "preserved",
        }
        self.file = self.root / "capture.json"
        self.now = datetime(2026, 11, 5, 2, 0, tzinfo=timezone.utc)

    def read(self, data=None, **kwargs):
        self.file.write_text(json.dumps(self.capture if data is None else data, ensure_ascii=False), encoding="utf-8")
        return aq.snapshot_context(self.file, self.vault, now=self.now, **kwargs)

    def test_exact_subfolder_source_and_extensions_survive_with_current_hash(self):
        result = self.read()
        card = result["records"][0]["cards"][0]
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["future_provider_field"], "preserved")
        self.assertEqual(card["future_card_field"], {"keep": True})
        self.assertEqual(card["state"], self.card["state"])
        self.assertNotIn("source_front_html", card)
        self.assertEqual(card["source_front_html_reference"]["artifact"], "capture_file")
        self.assertEqual(self.read(include_fronts=True)["records"][0]["cards"][0]["source_front_html"], self.front)
        source = card["vault_source"]
        self.assertEqual(source["note"], "Clippings/A problem.v2.md")
        self.assertEqual(source["status"], "matched")
        self.assertEqual(source["locator"], self.uri)
        before = source["sha256"]
        self.note.write_text(self.note.read_text(encoding="utf-8") + "\nA later refinement", encoding="utf-8")
        later = self.read()["records"][0]["cards"][0]["vault_source"]
        self.assertNotEqual(later["sha256"], before)
        self.assertEqual(later["content_sync"], "not-established-by-identity-and-current-hash")
        self.assertFalse(result["freshness"]["live"])
        self.assertEqual(result["freshness"]["capture_age_seconds"], 3599)
        self.assertNotIn("text", source)

    def test_current_interest_header_binds_like_the_legacy_one(self):
        # The header Interest renders since 2026-11-08: a classed div holding
        # one link with an inline SVG icon and the title in a span.
        front = ('<div class="interest-source" style="display:flex;justify-content:flex-end;">'
                 f'<a href="{self.uri}" style="display:inline-flex;">'
                 '<svg width="15" height="15" viewBox="0 0 24 24"><path d="M12 20h9"/></svg>'
                 '<span>A problem.v2</span></a></div><p>Question</p>')
        data = json.loads(json.dumps(self.capture))
        data["records"][0]["cards"][0]["source_front_html"] = front
        source = self.read(data)["records"][0]["cards"][0]["vault_source"]
        self.assertEqual(source["status"], "matched")
        self.assertEqual(source["locator"], self.uri)

    def test_multiple_cards_and_nonatomic_overlapping_queries_are_retained(self):
        second = {**self.card, "card_id": 22, "ordinal": 1}
        self.capture["records"][0]["cards"].append(second)
        self.capture["records"][0]["total"] = 2
        later = copy.deepcopy(self.card)
        later["state"]["reps"] = 1
        self.capture["scope"]["queries"].append("rated:1")
        self.capture["records"].append({"query": "rated:1", "status": "complete", "total": 1,
                                        "observed_at_ms": 1793840400500, "cards": [later], "errors": []})
        result = self.read()
        self.assertEqual([c["card_id"] for c in result["records"][0]["cards"]], [21, 22])
        self.assertEqual(result["records"][1]["cards"][0]["note_id"], 11)
        self.assertEqual(result["observation_conflicts"][0]["differing_fields"], ["state"])
        self.assertEqual(result["status"], "partial")

    def test_malformed_scope_identity_and_false_complete_are_refused(self):
        changes = [
            lambda d: d.update(provider="desktop-mirror"),
            lambda d: d["scope"].update(queries=["is:due", "is:due"]),
            lambda d: d["freshness"].update(timezone_offset_minutes=None),
            lambda d: d["freshness"].update(finished_at="2026-11-05T00:00:00Z"),
            lambda d: d["records"][0].update(total=2),
            lambda d: d["records"][0]["cards"].append(copy.deepcopy(self.card)),
            lambda d: d["records"][0]["cards"][0].update(card_id=True),
            lambda d: d["records"][0]["cards"][0]["state"].update(queue="review"),
            lambda d: d["records"][0]["cards"][0]["state"].pop("due"),
        ]
        for change in changes:
            with self.subTest(change=change):
                data = copy.deepcopy(self.capture)
                change(data)
                with self.assertRaises(ValueError):
                    self.read(data)

    def test_partial_source_failure_and_native_unavailability_never_mean_empty_success(self):
        data = copy.deepcopy(self.capture)
        data["status"] = data["records"][0]["status"] = "partial"
        data["records"][0]["cards"][0].update(source_front_html=None, source_error="note unavailable")
        data["records"][0]["errors"] = [{"stage": "source", "message": "note unavailable"}]
        self.assertEqual(self.read(data)["records"][0]["cards"][0]["vault_source"]["status"], "unresolved")
        data["status"] = data["records"][0]["status"] = "unavailable"
        data["records"][0].update(total=None, cards=[])
        self.assertEqual(self.read(data)["status"], "unavailable")

    def test_identity_mismatch_body_link_ambiguity_and_escape_are_explicit(self):
        mismatch = {**self.card, "note_id": 12}
        self.assertEqual(aq.bind_source(mismatch, self.vault, 10000)["status"], "identity-mismatch")
        body_only = {**self.card, "source_front_html": f'<p><a href="{self.uri}">Related</a></p>'}
        self.assertEqual(aq.bind_source(body_only, self.vault, 10000)["status"], "unresolved")
        ambiguous = {**self.card, "source_front_html": self.front.replace('</a>', f'</a><a href="{self.uri}">another</a>', 1)}
        self.assertEqual(aq.bind_source(ambiguous, self.vault, 10000)["status"], "ambiguous")
        escaped = {**self.card, "source_front_html": self.front.replace(quote("Clippings/A problem.v2", safe="-_.!~*'()"), quote("../outside", safe=""))}
        self.assertEqual(aq.bind_source(escaped, self.vault, 10000)["status"], "unavailable")

    def test_card_query_and_byte_bounds_preserve_declared_limits(self):
        self.capture["records"][0]["cards"].append({**self.card, "card_id": 22, "ordinal": 1})
        self.capture["records"][0]["total"] = 2
        result = self.read(limit=1, query="is:due")
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["records"][0]["consumer_truncated"])
        self.assertEqual(result["records"][0]["captured_cards"], 2)
        self.assertEqual(result["records"][0]["returned_cards"], 1)
        for args in ({"max_input_bytes": 1}, {"max_bytes": 1}, {"query": "not-captured"}, {"limit": 0}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.read(**args)

    def test_snapshot_cli_never_contacts_desktop_and_uses_utf8_envelope(self):
        self.read()
        output = io.StringIO()
        with patch.object(aq, "invoke", side_effect=AssertionError("must not contact AnkiConnect")), \
                patch.object(aq.sys, "stdout", output):
            code = aq.main(["snapshot", "--file", str(self.file), "--vault", str(self.vault), "--query", "is:due"])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["provider"], "ankidroid")
        self.assertNotIn("source_front_html", result["records"][0]["cards"][0])
        self.assertIn("source_front_html_reference", result["records"][0]["cards"][0])

    def test_duplicate_json_keys_and_nonfinite_extensions_are_refused(self):
        self.file.write_text('{"provider":"ankidroid","provider":"other"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            aq.snapshot_context(self.file, self.vault)
        valid = json.dumps(self.capture, ensure_ascii=False)
        for token in ("NaN", "1e400", "-1e400"):
            with self.subTest(token=token):
                self.file.write_text(valid.replace('"preserved"', token), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "non-finite JSON number"):
                    aq.snapshot_context(self.file, self.vault)


if __name__ == "__main__":
    unittest.main()
