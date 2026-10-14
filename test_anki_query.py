import io
import json
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
