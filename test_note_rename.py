import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import note_rename


class FakeObsidian:
    def __init__(self, vault, propagate=True, timeout=False):
        self.vault = Path(vault)
        self.propagate = propagate
        self.timeout = timeout
        self.renames = 0

    def __call__(self, argv, **kwargs):
        if "backlinks" in argv:
            data = [{"file": "Source.md", "count": "1"}]
            return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")
        if "rename" in argv:
            self.renames += 1
            if self.timeout:
                raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
            old = next(item[5:] for item in argv if item.startswith("path="))
            name = next(item[5:] for item in argv if item.startswith("name="))
            old_file = self.vault / old
            new_file = old_file.with_name(name + ".md")
            if self.propagate:
                source = self.vault / "Source.md"
                source.write_text(source.read_text(encoding="utf-8").replace(
                    f"[[{Path(old).stem}]]", f"[[{name}]]"), encoding="utf-8")
            old_file.rename(new_file)
            return SimpleNamespace(returncode=0, stdout="Renamed", stderr="")
        return SimpleNamespace(returncode=0, stdout=self.vault.name, stderr="")


class NoteRenameTests(unittest.TestCase):
    def fixture(self, root):
        vault = Path(root)
        authored = b"authored" + bytes((13, 10)) + b"content" + bytes((13, 10))
        (vault / "Old.md").write_bytes(authored)
        (vault / "Source.md").write_text("See [[Old]].\n", encoding="utf-8")
        return vault

    def test_apply_preserves_content_and_propagates_identity(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            vault = self.fixture(tmp)
            fake = FakeObsidian(vault)
            plan = note_rename.build_plan(vault, "Old.md", "New", runner=fake)
            result = note_rename.apply_plan(plan)
            self.assertEqual(result["status"], "applied")
            self.assertEqual(fake.renames, 1)
            self.assertFalse((vault / "Old.md").exists())
            self.assertEqual((vault / "New.md").read_bytes(),
                             b"authored" + bytes((13, 10))
                             + b"content" + bytes((13, 10)))
            self.assertEqual((vault / "Source.md").read_text(encoding="utf-8"),
                             "See [[New]].\n")
            self.assertEqual(result["stale_old_links"], [])
            self.assertEqual(result["anki_sync_candidates"],
                             ["New.md", "Source.md"])

    def test_destination_collision_and_path_are_refused(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            vault = self.fixture(tmp)
            fake = FakeObsidian(vault)
            (vault / "Taken.md").write_text("taken", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "destination absent"):
                note_rename.build_plan(vault, "Old", "Taken", runner=fake)
            with self.assertRaisesRegex(ValueError, "filename"):
                note_rename.build_plan(vault, "Old", "folder/New", runner=fake)

    def test_missing_link_propagation_is_reported_partial(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            vault = self.fixture(tmp)
            fake = FakeObsidian(vault, propagate=False)
            plan = note_rename.build_plan(vault, "Old", "New", runner=fake)
            result = note_rename.apply_plan(plan)
            self.assertEqual(result["status"], "partial")
            self.assertIn("old wikilink targets remain", result["failures"])

    def test_timeout_is_indeterminate_and_forbids_retry(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            vault = self.fixture(tmp)
            fake = FakeObsidian(vault, timeout=True)
            plan = note_rename.build_plan(vault, "Old", "New", runner=fake)
            result = note_rename.apply_plan(plan)
            self.assertEqual(result["status"], "indeterminate")
            self.assertTrue(result["do_not_retry"])
            self.assertEqual(fake.renames, 1)


if __name__ == "__main__":
    unittest.main()
