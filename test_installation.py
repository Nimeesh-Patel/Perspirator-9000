import tempfile
import unittest
from pathlib import Path

from installation import (manifest_problems, retire_stale_owned_files,
                          write_manifest)


class InstallationOwnershipTests(unittest.TestCase):
    def test_only_unchanged_retired_output_is_deleted(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            (root / "old.py").write_text("old", encoding="utf-8")
            (root / "unrelated.py").write_text("mine", encoding="utf-8")
            write_manifest(root, ["old.py"])
            retired = retire_stale_owned_files(root, ["new.py"])
            self.assertEqual(retired, ["old.py"])
            self.assertFalse((root / "old.py").exists())
            self.assertTrue((root / "unrelated.py").exists())

    def test_modified_retired_output_is_refused(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            target = root / "old.py"
            target.write_text("old", encoding="utf-8")
            write_manifest(root, ["old.py"])
            target.write_text("locally changed", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "modified"):
                retire_stale_owned_files(root, [])
            self.assertTrue(target.exists())

    def test_manifest_requires_every_current_generated_file(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            (root / "one.py").write_text("one", encoding="utf-8")
            write_manifest(root, ["one.py"])
            problems = manifest_problems(root, ["one.py", "two.py"])
            self.assertIn("two.py", problems[0])

    def test_manifest_owns_nested_generated_files(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            nested = root / "fixtures" / "contract.json"
            nested.parent.mkdir()
            nested.write_text("{}", encoding="utf-8")
            write_manifest(root, ["fixtures/contract.json"])

            self.assertEqual(
                manifest_problems(root, ["fixtures/contract.json"]), [])


if __name__ == "__main__":
    unittest.main()
