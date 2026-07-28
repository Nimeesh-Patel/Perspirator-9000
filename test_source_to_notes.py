import tempfile
import unittest
from pathlib import Path

import source_to_notes as stn


SOURCES = [
    {"id": "a", "text": "First exact idea.", "url": "https://example.com/a"},
    {"id": "b", "text": "A conflicting idea.", "url": "https://example.com/b"},
]


class SourceToNotesTests(unittest.TestCase):
    def test_accepts_list_and_adapter_object(self):
        self.assertEqual(set(stn.source_records(SOURCES)), {"a", "b"})
        self.assertEqual(set(stn.source_records({"records": SOURCES})), {"a", "b"})
        self.assertEqual(set(stn.source_records({"sources": SOURCES})), {"a", "b"})

    def test_validates_exact_coverage_and_duplicate_assignment(self):
        sources = stn.source_records(SOURCES)
        plan = {"notes": [{"title": "One", "problem": "What is the problem?", "source_ids": ["a"]}]}
        with self.assertRaisesRegex(ValueError, "unassigned source ids: b"):
            stn.validate_plan(plan, sources)
        plan["notes"].append({"title": "Two", "problem": "What conflicts?", "source_ids": ["a", "b"]})
        with self.assertRaisesRegex(ValueError, "assigned to both"):
            stn.validate_plan(plan, sources)

    def test_renders_normal_problem_note_with_sources_on_idea_side(self):
        sources = stn.source_records(SOURCES)
        plan = {"notes": [{"title": "A live conflict", "problem": "How can both claims stand?", "up": ["parent"], "source_ids": ["a", "b"]}]}
        notes, unassigned = stn.validate_plan(plan, sources)
        self.assertEqual(unassigned, [])
        text = stn.render_note(notes[0], sources)
        self.assertIn("up:\n- '[[parent]]'\ncategory: Default", text)
        self.assertIn("How can both claims stand?\n\n***\n\n", text)
        self.assertIn("First exact idea.\n\nhttps://example.com/a", text)
        self.assertIn("A conflicting idea.\n\nhttps://example.com/b", text)
        self.assertNotIn("date:", text)
        self.assertNotIn("drafted from", text)

    def test_validates_parent_links_when_vault_is_available(self):
        sources = stn.source_records([SOURCES[0]])
        plan = {"notes": [{"title": "Child", "problem": "What follows?", "up": ["Known parent"], "source_ids": ["a"]}]}
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            (vault / "Known parent.md").write_text("parent", encoding="utf-8")
            notes, _ = stn.validate_plan(plan, sources, vault=vault)
            self.assertEqual(notes[0]["up"], ["Known parent"])
            plan["notes"][0]["up"] = ["Missing"]
            with self.assertRaisesRegex(ValueError, "unresolved up link"):
                stn.validate_plan(plan, sources, vault=vault)

    def test_refuses_overwrite(self):
        sources = stn.source_records([SOURCES[0]])
        note = {"filename": "Existing.md", "problem": "What exists?", "category": "Default", "up": [], "source_ids": ["a"]}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Existing.md"
            target.write_text("user content", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                stn.write_notes([note], sources, Path(directory))
            self.assertEqual(target.read_text(encoding="utf-8"), "user content")


if __name__ == "__main__":
    unittest.main()
