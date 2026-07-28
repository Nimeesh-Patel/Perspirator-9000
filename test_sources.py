"""Offline contract tests for source adapters and source-to-note staging."""

import tempfile
import unittest
from pathlib import Path

import source_to_notes as stn
import x_posts


SOURCES = [
    {"id": "a", "text": "First exact idea.", "url": "https://example.com/a"},
    {"id": "b", "text": "A conflicting idea.", "url": "https://example.com/b"},
]


def x_fixture(status_id, text, screen_name="Example"):
    return {
        "id_str": status_id,
        "text": text,
        "created_at": "2026-01-01T00:00:00.000Z",
        "entities": {"urls": [{
            "url": "https://t.co/a",
            "expanded_url": "https://example.com/a",
        }]},
        "user": {"name": "Example Person", "screen_name": screen_name},
    }


class SourceAdapterTests(unittest.TestCase):
    def test_x_identity_and_duplicate_handling(self):
        text = """
        https://x.com/alice/status/123?s=20
        https://twitter.com/i/status/456
        https://mobile.x.com/alice/status/123
        https://example.com/alice/status/999
        """
        ids = x_posts.status_ids(text)
        self.assertEqual(ids, ["123", "456", "123"])
        self.assertEqual(
            x_posts.unique_with_duplicates(ids),
            (["123", "456"], ["123"]),
        )

    def test_x_preserves_context_and_only_reports_text_repetition(self):
        post = x_fixture("123", "A &amp; B https://t.co/a", "alice")
        post["parent"] = x_fixture("122", "Parent", "bob")
        post["quoted_tweet"] = x_fixture("121", "Quote", "carol")
        post["mediaDetails"] = [{
            "type": "photo",
            "media_url_https": "https://pbs.example/image.png",
            "ext_alt_text": "diagram",
        }]
        record = x_posts.compact_record("123", post)
        self.assertEqual(record["text"], "A & B https://example.com/a")
        self.assertEqual(record["parent"]["id"], "122")
        self.assertEqual(record["quoted"]["id"], "121")
        self.assertEqual(record["media"][0]["alt_text"], "diagram")

        posts = {
            "1": x_fixture("1", "@a Same claim https://t.co/a", "one"),
            "2": x_fixture("2", "Same claim", "two"),
        }
        result = x_posts.build_result(
            ["1", "2"], fetcher=lambda status_id: posts[status_id])
        self.assertEqual([record["id"] for record in result["records"]], ["1", "2"])
        self.assertEqual(result["possible_duplicate_text_groups"], [["1", "2"]])


class SourceToNotesTests(unittest.TestCase):
    def test_plan_requires_complete_unique_assignment(self):
        sources = stn.source_records({"records": SOURCES})
        plan = {"notes": [{
            "title": "One",
            "problem": "What is the problem?",
            "source_ids": ["a"],
        }]}
        with self.assertRaisesRegex(ValueError, "unassigned source ids: b"):
            stn.validate_plan(plan, sources)
        plan["notes"].append({
            "title": "Two",
            "problem": "What conflicts?",
            "source_ids": ["a", "b"],
        })
        with self.assertRaisesRegex(ValueError, "assigned to both"):
            stn.validate_plan(plan, sources)

    def test_rendering_preserves_problem_note_and_source_contracts(self):
        sources = stn.source_records(SOURCES)
        plan = {"notes": [{
            "title": "A live conflict",
            "problem": "How can both claims stand?",
            "up": ["Known parent"],
            "source_ids": ["a", "b"],
        }]}
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            (vault / "Known parent.md").write_text("parent", encoding="utf-8")
            notes, unassigned = stn.validate_plan(plan, sources, vault=vault)
            text = stn.render_note(notes[0], sources)
        self.assertEqual(unassigned, [])
        self.assertIn("up:\n- '[[Known parent]]'\ncategory: Default", text)
        self.assertIn("How can both claims stand?\n\n***\n\n", text)
        self.assertIn("First exact idea.\n\nhttps://example.com/a", text)
        self.assertIn("A conflicting idea.\n\nhttps://example.com/b", text)
        self.assertNotIn("date:", text)

    def test_writer_refuses_to_replace_an_existing_note(self):
        sources = stn.source_records([SOURCES[0]])
        note = {
            "filename": "Existing.md",
            "problem": "What exists?",
            "category": "Default",
            "up": [],
            "source_ids": ["a"],
        }
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Existing.md"
            target.write_text("user content", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                stn.write_notes([note], sources, Path(directory))
            self.assertEqual(target.read_text(encoding="utf-8"), "user content")


if __name__ == "__main__":
    unittest.main()
