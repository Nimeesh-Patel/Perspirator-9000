import json
import tempfile
import unittest
from pathlib import Path

from cleanup_manifest import tree_state, validate_manifest
from directory_audit import sha256_file


class CleanupManifestTests(unittest.TestCase):
    def _fixture(self, root: Path):
        downloads = root / "Downloads"
        downloads.mkdir()
        target = downloads / "old.zip"
        target.write_bytes(b"archive")
        folder = downloads / "old-folder"
        folder.mkdir()
        (folder / "one.txt").write_text("one", encoding="utf-8")
        state = tree_state(folder)
        manifest = root / "manifest.json"
        payload = {
            "schema_version": 1,
            "root": str(downloads),
            "total_bytes": target.stat().st_size + state["bytes"],
            "groups": [{
                "name": "test",
                "bytes": target.stat().st_size + state["bytes"],
                "items": [
                    {"type": "file", "path": str(target),
                     "bytes": target.stat().st_size,
                     "sha256": sha256_file(target)},
                    {"type": "directory_tree", "path": str(folder),
                     "bytes": state["bytes"], "file_count": state["file_count"],
                     "directory_count": state["directory_count"],
                     "tree_sha256": state["tree_sha256"]},
                ],
            }],
        }
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        return downloads, target, folder, manifest

    def test_exact_manifest_is_ready(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            _, _, _, manifest = self._fixture(Path(tmp))
            result = validate_manifest(manifest)
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["targets_observed"], 2)

    def test_changed_file_refuses_transaction(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            _, target, _, manifest = self._fixture(Path(tmp))
            target.write_bytes(b"changed")
            result = validate_manifest(manifest)
            self.assertEqual(result["status"], "stale-or-invalid")
            self.assertTrue(any("SHA-256 changed" in item for item in result["problems"]))

    def test_extra_tree_file_refuses_transaction(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            _, _, folder, manifest = self._fixture(Path(tmp))
            (folder / "two.txt").write_text("two", encoding="utf-8")
            result = validate_manifest(manifest)
            self.assertEqual(result["status"], "stale-or-invalid")
            self.assertTrue(any("file_count changed" in item
                                for item in result["problems"]))

    def test_target_outside_root_is_refused(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            _, _, _, manifest = self._fixture(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            outside = root / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            item = payload["groups"][0]["items"][0]
            item.update({"path": str(outside), "bytes": outside.stat().st_size,
                         "sha256": sha256_file(outside)})
            payload["groups"][0]["bytes"] = sum(
                entry["bytes"] for entry in payload["groups"][0]["items"])
            payload["total_bytes"] = payload["groups"][0]["bytes"]
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            result = validate_manifest(manifest)
            self.assertEqual(result["status"], "stale-or-invalid")
            self.assertTrue(any("escapes" in item for item in result["problems"]))

    def test_tree_state_reports_scan_and_hash_progress(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            (root / "one.txt").write_text("one", encoding="utf-8")
            (root / "two.txt").write_text("two", encoding="utf-8")
            events = []
            state = tree_state(root, progress=events.append, progress_every=1)
            self.assertEqual(state["file_count"], 2)
            names = {event["event"] for event in events}
            self.assertIn("tree-scan-progress", names)
            self.assertIn("tree-hash-progress", names)
            self.assertIn("tree-hash-complete", names)

    def test_parallel_tree_hash_preserves_deterministic_identity(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            for number in range(20):
                (root / f"{number:02}.txt").write_bytes(
                    (f"payload-{number}" * 100).encode("utf-8"))
            sequential = tree_state(root, hash_workers=1)
            parallel = tree_state(root, hash_workers=4)
            self.assertEqual(sequential, parallel)

    def test_overlapping_targets_are_refused(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            _, _, folder, manifest = self._fixture(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            nested = folder / "one.txt"
            nested_item = {
                "type": "file", "path": str(nested),
                "bytes": nested.stat().st_size, "sha256": sha256_file(nested),
            }
            payload["groups"][0]["items"].append(nested_item)
            payload["groups"][0]["bytes"] += nested_item["bytes"]
            payload["total_bytes"] += nested_item["bytes"]
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            result = validate_manifest(manifest)
            self.assertEqual(result["status"], "stale-or-invalid")
            self.assertTrue(any("overlapping target" in item
                                for item in result["problems"]))


if __name__ == "__main__":
    unittest.main()
