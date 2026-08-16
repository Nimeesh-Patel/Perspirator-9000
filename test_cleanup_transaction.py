import json
import tempfile
import unittest
from pathlib import Path

from cleanup_manifest import tree_state, validate_manifest
from cleanup_transaction import (apply_cleanup, windows_permanent_delete,
                                 windows_recycle)
from directory_audit import sha256_file


class CleanupTransactionTests(unittest.TestCase):
    @staticmethod
    def _capacity(_targets):
        return {"status": "available", "volumes": []}

    def _fixture(self, root: Path, names=("one.bin", "two.bin")):
        target_root = root / "target"
        target_root.mkdir()
        items = []
        for index, name in enumerate(names, start=1):
            path = target_root / name
            path.write_bytes(bytes([index]) * index)
            items.append({
                "type": "file", "path": str(path),
                "bytes": path.stat().st_size, "sha256": sha256_file(path),
            })
        manifest = root / "manifest.json"
        payload = {
            "schema_version": 1, "root": str(target_root),
            "disposition": "recycle",
            "operation": "move exact nominated paths to the Windows Recycle Bin",
            "total_bytes": sum(item["bytes"] for item in items),
            "groups": [{"name": "fixture",
                        "bytes": sum(item["bytes"] for item in items),
                        "items": items}],
        }
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        return manifest, [Path(item["path"]) for item in items]

    @staticmethod
    def _delete(path):
        path.unlink()

    def test_exact_approval_applies_and_checkpoints(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            manifest, targets = self._fixture(root)
            record_path = root / "record.json"
            result = apply_cleanup(
                manifest, sha256_file(manifest), record_path,
                mutator=self._delete, capacity_checker=self._capacity)
            self.assertEqual(result["status"], "applied")
            self.assertEqual(result["applied_targets"], 2)
            self.assertTrue(all(not path.exists() for path in targets))
            recorded = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(recorded["status"], "applied")

    def test_wrong_approval_refuses_before_mutation(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            manifest, targets = self._fixture(root)
            with self.assertRaisesRegex(ValueError, "explicit approval"):
                apply_cleanup(manifest, "0" * 64, root / "record.json",
                              mutator=self._delete)
            self.assertTrue(all(path.exists() for path in targets))

    def test_stale_validation_refuses_before_mutation(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            manifest, targets = self._fixture(root)
            approved = sha256_file(manifest)
            targets[0].write_bytes(b"changed")
            result = apply_cleanup(manifest, approved, root / "record.json",
                                   mutator=self._delete,
                                   capacity_checker=self._capacity)
            self.assertEqual(result["status"], "refused")
            self.assertTrue(all(path.exists() for path in targets))

    def test_adapter_failure_is_indeterminate_and_stops(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            manifest, targets = self._fixture(root)

            def fail_second(path):
                if path.name == "one.bin":
                    raise OSError("adapter ambiguity")
                path.unlink()

            result = apply_cleanup(
                manifest, sha256_file(manifest), root / "record.json",
                mutator=fail_second, capacity_checker=self._capacity)
            # Largest-first makes two.bin succeed, then one.bin fail.
            self.assertEqual(result["status"], "indeterminate")
            self.assertTrue(result["do_not_retry"])
            self.assertFalse(targets[1].exists())
            self.assertTrue(targets[0].exists())

    def test_manifest_change_during_validation_refuses(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            manifest, targets = self._fixture(root)
            approved = sha256_file(manifest)

            def changing_validator(path, **kwargs):
                result = validate_manifest(path, **kwargs)
                path.write_text(path.read_text(encoding="utf-8") + "\n",
                                encoding="utf-8")
                return result

            with self.assertRaisesRegex(RuntimeError, "changed during validation"):
                apply_cleanup(manifest, approved, root / "record.json",
                              mutator=self._delete,
                              validator=changing_validator,
                              capacity_checker=self._capacity)
            self.assertTrue(all(path.exists() for path in targets))

    def test_windows_adapter_sends_path_over_stdin(self):
        calls = []

        class Completed:
            returncode = 0
            stderr = ""
            stdout = ""

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            return Completed()

        path = Path(r"C:\strange path\name ' $().bin")
        windows_recycle(path, run=fake_run)
        request = json.loads(calls[0][1]["input"])
        self.assertEqual(request["path"], str(path))
        self.assertEqual(request["disposition"], "recycle")
        self.assertIsInstance(calls[0][0], list)

        windows_permanent_delete(path, run=fake_run)
        permanent = json.loads(calls[1][1]["input"])
        self.assertEqual(permanent["disposition"], "permanent")

    def test_record_inside_nominated_directory_is_refused(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            target_root = root / "target"
            target_root.mkdir()
            folder = target_root / "old-cache"
            folder.mkdir()
            (folder / "data.bin").write_bytes(b"cache")
            state = tree_state(folder)
            manifest = root / "manifest.json"
            payload = {
                "schema_version": 1, "root": str(target_root),
                "disposition": "recycle",
                "operation": "move exact nominated paths to the Windows Recycle Bin",
                "total_bytes": state["bytes"],
                "groups": [{
                    "name": "fixture", "bytes": state["bytes"],
                    "items": [{
                        "type": "directory_tree", "path": str(folder),
                        "bytes": state["bytes"],
                        "file_count": state["file_count"],
                        "directory_count": state["directory_count"],
                        "tree_sha256": state["tree_sha256"],
                    }],
                }],
            }
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "record cannot be inside"):
                apply_cleanup(
                    manifest, sha256_file(manifest), folder / "record.json",
                    mutator=self._delete, capacity_checker=self._capacity)
            self.assertTrue(folder.exists())

    def test_insufficient_recycle_capacity_refuses_every_mutation(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            manifest, targets = self._fixture(root)
            record_path = root / "record.json"
            result = apply_cleanup(
                manifest, sha256_file(manifest), record_path,
                mutator=self._delete,
                capacity_checker=lambda _targets: {
                    "status": "insufficient", "required_bytes": 3,
                    "volumes": [{"quota_bytes": 2}],
                })
            self.assertEqual(result["status"], "refused")
            self.assertTrue(all(path.exists() for path in targets))
            self.assertEqual(
                json.loads(record_path.read_text(encoding="utf-8"))["status"],
                "refused")

    def test_permanent_disposition_skips_recycle_capacity(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            manifest, targets = self._fixture(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload.update({
                "disposition": "permanent",
                "operation": "permanently delete exact nominated paths",
            })
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            result = apply_cleanup(
                manifest, sha256_file(manifest), root / "record.json",
                disposition="permanent", mutator=self._delete,
                capacity_checker=lambda _targets: self.fail(
                    "permanent deletion must not query Recycle Bin capacity"))
            self.assertEqual(result["status"], "applied")
            self.assertFalse(result["recoverable"])
            self.assertTrue(all(not target.exists() for target in targets))

    def test_manifest_disposition_must_match_request(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            manifest, targets = self._fixture(root)
            with self.assertRaisesRegex(ValueError, "does not match"):
                apply_cleanup(
                    manifest, sha256_file(manifest), root / "record.json",
                    disposition="permanent", mutator=self._delete)
            self.assertTrue(all(target.exists() for target in targets))


if __name__ == "__main__":
    unittest.main()
