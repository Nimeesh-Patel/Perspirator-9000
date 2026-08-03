import tempfile
import unittest
from pathlib import Path

import anki_sync


class FakeAnki:
    def __init__(self, notes=None, new_id=91):
        self.notes = set(notes or [])
        self.new_id = new_id
        self.calls = []

    def __call__(self, action, params=None):
        self.calls.append((action, params))
        if action == "deckNames":
            return ["Default", "Optimism"]
        if action == "notesInfo":
            return [{"noteId": i} for i in params["notes"] if i in self.notes]
        if action == "updateNoteFields":
            return None
        if action == "addNote":
            return self.new_id
        raise AssertionError(action)


class AnkiSyncTests(unittest.TestCase):
    def payload(self, note_id):
        return {"path": "A.md", "anki_note_id": note_id,
                "deck_candidate": "Optimism", "model": "Basic",
                "fields": {"Front": "F", "Back": "B"}}

    def test_payload_uses_problem_boundary_and_renders_wikilinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "My Vault"; vault.mkdir()
            path = vault / "A note.md"
            path.write_text(
                "---\ncategory: Optimism\nanki_note_id: 17\n---\n"
                "How can [[knowledge|knowledge creation]] help?\n***\n"
                "By solving [[problems]].\n", encoding="utf-8")
            payload = anki_sync.note_payload(path, vault)
            self.assertEqual(payload["anki_note_id"], 17)
            self.assertEqual(payload["deck_candidate"], "Optimism")
            self.assertIn("file=A+note", payload["fields"]["Front"])
            self.assertIn("knowledge creation", payload["fields"]["Front"])
            self.assertIn("file=problems", payload["fields"]["Back"])

    def test_existing_identity_is_updated_in_place(self):
        fake = FakeAnki(notes=[17])
        actions = anki_sync.synchronize([self.payload(17)], fake, apply=True)
        self.assertEqual(actions[0]["action"], "update")
        self.assertEqual(actions[0]["anki_note_id"], 17)
        self.assertTrue(any(call[0] == "updateNoteFields" for call in fake.calls))

    def test_missing_existing_identity_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "does not resolve"):
            anki_sync.synchronize([self.payload(17)], FakeAnki(), apply=True)

    def test_new_identity_is_created_and_reported(self):
        actions = anki_sync.synchronize(
            [self.payload(None)], FakeAnki(new_id=123), apply=True)
        self.assertEqual(actions[0]["anki_note_id"], 123)
        self.assertEqual(actions[0]["action"], "create")

    def test_dry_run_reports_the_candidate_deck(self):
        actions = anki_sync.synchronize([self.payload(None)], FakeAnki())
        self.assertEqual(actions[0]["deck"], "Optimism")

    def test_non_problem_note_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp); path = vault / "A.md"
            path.write_text("Only prose.\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Problem Note"):
                anki_sync.note_payload(path, vault)


if __name__ == "__main__":
    unittest.main()
