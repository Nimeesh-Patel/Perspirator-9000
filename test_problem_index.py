import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from problem_index import index_note, root_problem_records


class RootProblemIndexTests(unittest.TestCase):
    def test_invalid_note_encoding_does_not_replace_a_previous_index(self):
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "broken.md").write_bytes(b"Problem \xff\n***\nConjecture")
            output = vault / "index.json"
            output.write_text("previous index", encoding="utf-8")
            completed = subprocess.run([sys.executable, str(root / "problem_index.py"),
                str(vault), "--out", str(output)], capture_output=True, text=True)
            self.assertEqual(completed.returncode, 2)
            self.assertIn("discovery did not complete", completed.stderr)
            self.assertEqual(output.read_text(), "previous index")
            half = subprocess.run([sys.executable, str(root / "problem_half.py"),
                str(vault / "broken.md"), "--json"], capture_output=True, text=True)
            self.assertEqual(half.returncode, 2)
            self.assertIn('"status": "unreadable"', half.stdout)

    def test_only_direct_root_problem_notes_are_indexed(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            vault = Path(tmp)
            (vault / "root.md").write_text(
                "A root problem.\n***\nA conjecture.\n", encoding="utf-8")
            template = vault / "Interesting" / "Templates" / "default.md"
            template.parent.mkdir(parents=True)
            template.write_text("---\ntags: []\n---\n\n***\n", encoding="utf-8")
            nested = vault / "folder" / "ordinary.md"
            nested.parent.mkdir()
            nested.write_text("Nested problem.\n***\nAnswer.\n", encoding="utf-8")

            records = root_problem_records(vault)

            self.assertEqual([record["path"] for record in records], ["root.md"])

    def test_stub_uses_the_canonical_separator_parser(self):
        cases = {
            "separator-at-start.md": ("***\nAn answer.\n", False),
            "indented-separator.md": ("Question.\n   ***   \n", True),
            "fenced-marker.md": (
                "Question.\n```text\n***\n```\n***\nAnswer.\n", False),
        }
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            vault = Path(tmp)
            for name, (text, expected_stub) in cases.items():
                path = vault / name
                path.write_text(text, encoding="utf-8")
                self.assertEqual(index_note(path, vault)["stub"], expected_stub, name)

    def test_deck_follows_interests_projection_rule(self):
        # Interest's scanner owns the rule; the index must report the same deck.
        cases = {
            "plain.md": ("---\ncategory: Morality\n---\nQ\n***\nA\n", "Morality", None, "Morality"),
            "quoted.md": ('---\ncategory: "Default"\n---\nQ\n***\nA\n', "Default", None, "Default"),
            "list.md": ("---\nup:\ncategory:\n  - Institutions\n---\nQ\n***\nA\n", "Institutions", None, "Institutions"),
            "flow.md": ("---\ncategory: [English]\n---\nQ\n***\nA\n", "English", None, "English"),
            "absent.md": ("---\nup: null\n---\nQ\n***\nA\n", None, None, "Default"),
            "collection.md": ("---\ncollection: People\n---\n***\n", None, "People", None),
            "empty-collection.md": ("---\ncollection:\ncategory: Morality\n---\nQ\n***\n", "Morality", "", None),
            "two-decks.md": ("---\ncategory:\n- A\n- B\n---\nQ\n***\n", None, None, None),
        }
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            vault = Path(tmp)
            for name, (text, category, collection, deck) in cases.items():
                path = vault / name
                path.write_text(text, encoding="utf-8")
                record = index_note(path, vault)
                self.assertEqual((record["category"], record["collection"], record["deck"]),
                                 (category, collection, deck), name)
            self.assertTrue(index_note(vault / "two-decks.md", vault)["category_error"])

    def test_structural_clis_expose_help(self):
        root = Path(__file__).resolve().parent
        for script in ("problem_index.py", "problem_half.py"):
            completed = subprocess.run(
                [sys.executable, str(root / script), "--help"],
                capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("usage:", completed.stdout.casefold())


class AuthoredLinkRetrievalTests(unittest.TestCase):
    def test_shared_rankers_keep_evaluation_scope_and_one_embedding_session(self):
        import numpy as np
        import evaluate_retrieval as evaluation

        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "Alpha.md").write_text(
                "A linked topic.\n***\n[[Alpha]] [[Beta]]\n", encoding="utf-8")
            (vault / "Beta.md").write_text(
                "Another problem.\n***\nAn answer.\n", encoding="utf-8")
            meta = [
                {"note": "Alpha.md", "corpus": "vault", "text": "linked topic"},
                {"note": "folder/Alpha.md", "corpus": "vault", "text": "linked topic"},
                {"note": "Beta.md", "corpus": "vault", "text": "linked topic"},
                {"note": "Gamma.md", "corpus": "vault", "text": "other words"},
                {"note": "memory/Memo.md", "corpus": "memory", "text": "linked topic"},
            ]
            loaded = {"config": {"exempt": [], "model": "stub",
                                  "formation": {"max-tokens": 256}},
                      "meta": meta,
                      "vectors": np.array([[1., 0.], [1., 0.], [.8, .2],
                                           [.9, .1], [1., 0.]])}
            with patch.object(evaluation, "load_index", return_value=loaded) as load, \
                    patch.object(evaluation, "Embedder") as embedder, \
                    patch.object(evaluation, "lexical_scores",
                                 wraps=evaluation.lexical_scores) as lexical, \
                    patch.object(evaluation, "merge_rankers",
                                 wraps=evaluation.merge_rankers) as rankers:
                embedder.return_value.return_value = np.array([[1., 0.]])
                report = evaluation.evaluate(vault, k=1, sample=0, seed=7)

            self.assertEqual(report, {
                "notes": 1, "links": 1, "k": 1,
                "found": {"embedding": 0, "lexical": 1, "both": 0},
                "unlinked": {"embedding": 1, "lexical": 0, "both": 1},
            })
            load.assert_called_once_with(vault, refresh_index=False)
            embedder.assert_called_once_with("stub", 256)
            embedder.return_value.assert_called_once_with(["Alpha A linked topic."])
            lexical.assert_called_once()
            self.assertEqual(lexical.call_args.args[2], [2, 3])
            self.assertEqual([call.args[-1] for call in rankers.call_args_list],
                             ["embedding", "lexical", "both"])


if __name__ == "__main__":
    unittest.main()
