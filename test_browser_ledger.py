import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import browser_ledger as b


def sample():
    return {"schema":"browser-attention/v1","captured_at":"2026-09-15T12:00:00Z",
            "scope":{"id":"profile-a","profile":"test","windows":"all-regular","incognito":False},
            "status":"complete","groups":[{"id":3,"title":"AGI"}],
            "tabs":[{"id":1,"url":"https://www.youtube.com/watch?v=abcdefghijk&t=42&list=PLx","title":"Talk","groupId":3}]}


class BrowserLedgerTests(unittest.TestCase):
    def test_video_identity_exact_host(self):
        self.assertEqual(b.video_id("https://youtu.be/abcdefghijk?t=42"), "abcdefghijk")
        self.assertEqual(b.video_id("https://youtube.com/shorts/abcdefghijk"), "abcdefghijk")
        self.assertIsNone(b.video_id("https://youtube.com.evil.test/watch?v=abcdefghijk"))
        self.assertIsNone(b.video_id("https://example.org/?v=abcdefghijk"))

    def test_restart_ids_do_not_create_false_new_content(self):
        a=sample(); c=copy.deepcopy(a); c["captured_at"]="2026-09-16T12:00:00Z"
        c["tabs"][0]["id"]=80; c["tabs"][0]["groupId"]=50; c["groups"][0]["id"]=50
        d=b.diff(a,c)
        self.assertEqual(d["changed"],[]); self.assertEqual(d["newly_observed"],{})

    def test_context_preserved_and_duplicate_count_change(self):
        a=sample(); c=copy.deepcopy(a); c["captured_at"]="2026-09-16T12:00:00Z"
        c["tabs"].append({"id":2,"url":"https://youtu.be/abcdefghijk?t=9","title":"Talk","groupId":-1})
        d=b.diff(a,c)
        self.assertEqual(d["newly_observed"],{})
        self.assertEqual(len(d["changed"][0]["after"]),2)
        self.assertIn("list=PLx",d["changed"][0]["after"][0]["url"])

    def test_partial_suppresses_absences(self):
        a=sample(); c=copy.deepcopy(a); c.update(captured_at="2026-09-16T12:00:00Z",tabs=[],status="partial")
        self.assertEqual(b.diff(a,c)["no_longer_observed"],{})
        c["status"]="complete"
        self.assertEqual(len(b.diff(a,c)["no_longer_observed"]),1)

    def test_scope_and_time_guards(self):
        a=sample(); c=copy.deepcopy(a)
        with self.assertRaises(ValueError): b.diff(a,c)
        c["captured_at"]="2026-09-16T12:00:00Z"; c["scope"]["id"]="other"
        with self.assertRaises(ValueError): b.diff(a,c)

    def test_complete_requires_group_metadata(self):
        a=sample(); a["groups"]=[]
        with self.assertRaises(ValueError): b.validate(a)

    def test_immutable_import_previous_and_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/"input.json"; store=root/"store"
            a=sample(); source.write_text(json.dumps(a),encoding="utf-8")
            first=b.ingest(source,store); b.ingest(source,store)
            self.assertEqual(len(list((store/"snapshots").glob("*"))),1)
            c=copy.deepcopy(a); c["captured_at"]="2026-09-17T12:00:00Z"; c["tabs"]=[]
            source.write_text(json.dumps(c),encoding="utf-8")
            result=b.ingest(source,store)
            diff=b.read(Path(result["report"]).with_name("comparison.json"))
            self.assertEqual(len(diff["no_longer_observed"]),1)
            self.assertEqual(b.read(first["snapshot"]),a)

    def test_caption_block_stops_batch_and_cache_avoids_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/"in.json"; a=sample()
            a["tabs"].append({"id":2,"url":"https://youtu.be/zzzzzzzzzzz","title":"Other","groupId":-1})
            b.write(source,a)
            response=type("Result",(),{"returncode":0,"stdout":json.dumps({"status":"unavailable","reason":"RequestBlocked","blocked":True})})()
            with patch.object(b.subprocess,"run",return_value=response) as run:
                result=b.captions(source,root/"cache",["en"],5,10)
                self.assertEqual(run.call_count,1); self.assertEqual(result["not_processed"],1)

    def test_leading_hyphen_video_id_and_success_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/"in.json"; a=sample()
            a["tabs"][0]["url"]="https://youtu.be/-80PEYVeI4E"; b.write(source,a)
            response=type("Result",(),{"returncode":0,"stdout":json.dumps({"status":"complete","segments":[]})})()
            with patch.object(b.subprocess,"run",return_value=response) as run:
                b.captions(source,root/"cache",["en"],1,10)
                argv=run.call_args.args[0]
                self.assertEqual(argv[-2:],["--","-80PEYVeI4E"])
                b.captions(source,root/"cache",["en"],1,10)
                self.assertEqual(run.call_count,1)


if __name__=="__main__": unittest.main()
