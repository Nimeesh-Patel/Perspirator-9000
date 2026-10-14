import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_structural_clis_expose_help(self):
        root = Path(__file__).resolve().parent
        for script in ("problem_index.py", "problem_half.py"):
            completed = subprocess.run(
                [sys.executable, str(root / script), "--help"],
                capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("usage:", completed.stdout.casefold())


if __name__ == "__main__":
    unittest.main()
