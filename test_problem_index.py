import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from problem_index import index_note, root_problem_records


class RootProblemIndexTests(unittest.TestCase):
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
