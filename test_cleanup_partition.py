import json
import tempfile
import unittest
from pathlib import Path

from cleanup_partition import derive_manifest
from directory_audit import sha256_file


class CleanupPartitionTests(unittest.TestCase):
    def _parent(self, root: Path) -> Path:
        parent = root / "parent.json"
        parent.write_text(json.dumps({
            "schema_version": 1, "root": str(root), "total_bytes": 7,
            "groups": [
                {"name": "first", "bytes": 3,
                 "items": [{"type": "file", "path": str(root / "a"),
                            "bytes": 3, "sha256": "a" * 64,
                            "extension": {"kept": True}}]},
                {"name": "second", "bytes": 4,
                 "items": [{"type": "file", "path": str(root / "b"),
                            "bytes": 4, "sha256": "b" * 64}]},
            ],
        }), encoding="utf-8")
        return parent

    def test_exact_group_is_preserved_with_parent_identity(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            parent = self._parent(Path(tmp))
            child = derive_manifest(parent, ["first"], "permanent",
                                    "Nimeesh selected the first group.")
            self.assertEqual(child["total_bytes"], 3)
            self.assertEqual(child["disposition"], "permanent")
            self.assertEqual(child["derived_from_manifest_sha256"],
                             sha256_file(parent))
            self.assertTrue(child["groups"][0]["items"][0]
                            ["extension"]["kept"])

    def test_unknown_or_duplicate_group_is_refused(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            parent = self._parent(Path(tmp))
            with self.assertRaisesRegex(ValueError, "unknown parent groups"):
                derive_manifest(parent, ["missing"], "recycle", "decision")
            with self.assertRaisesRegex(ValueError, "unique"):
                derive_manifest(parent, ["first", "first"],
                                "recycle", "decision")

    def test_duplicate_parent_group_identity_is_refused(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            parent = self._parent(Path(tmp))
            payload = json.loads(parent.read_text(encoding="utf-8"))
            payload["groups"][1]["name"] = "first"
            parent.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate group name"):
                derive_manifest(parent, ["first"], "recycle", "decision")


if __name__ == "__main__":
    unittest.main()
