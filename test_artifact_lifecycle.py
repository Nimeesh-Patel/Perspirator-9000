import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from artifact_lifecycle import inventory, lifecycle_problems, load_lifecycle


def entry(**fields):
    value = {
        "name": "archive",
        "role": "snapshot",
        "path": ".perspirator/transactions",
        "validation": "rollback-value",
        "retire_when": "rollback value no longer exists",
    }
    value.update(fields)
    return value


class ArtifactLifecycleTests(unittest.TestCase):
    def test_every_validation_class_checks_required_path_presence(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            problems = lifecycle_problems(Path(tmp), [entry()])
        self.assertEqual(
            problems,
            ["archive: artifact missing: .perspirator/transactions"])

    def test_declared_path_type_is_checked(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            vault = Path(tmp)
            target = vault / ".perspirator" / "transactions"
            target.parent.mkdir(parents=True)
            target.write_text("not a directory", encoding="utf-8")
            problems = lifecycle_problems(
                vault, [entry(path_type="directory")])
        self.assertEqual(
            problems,
            ["archive: expected directory: .perspirator/transactions"])

    def test_semantic_archive_class_can_forbid_active_markdown_surface(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            vault = Path(tmp)
            archive = vault / ".perspirator" / "transactions" / "run" / "originals"
            archive.mkdir(parents=True)
            (archive / "active.md").write_text("Problem.\n***\nAnswer.\n", encoding="utf-8")
            (archive / "inactive.snapshot").write_text("historical", encoding="utf-8")
            declaration = entry(
                path_type="directory", forbidden_patterns=["**/*.md"])

            problems = lifecycle_problems(vault, [declaration])

        self.assertEqual(problems, [
            "archive: forbidden artifact matches **/*.md: run/originals/active.md"
        ])

    def test_loader_rejects_unknown_classes_and_escaping_patterns(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            vault = Path(tmp)
            declaration = vault / "memory" / "perspirator" / "artifact-lifecycle.json"
            declaration.parent.mkdir(parents=True)
            for invalid in (
                    entry(validation="unimplemented"),
                    entry(path_type="directory", forbidden_patterns=["../*.md"])):
                declaration.write_text(
                    json.dumps({"artifacts": [invalid]}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_lifecycle(vault)

    def test_inventory_selects_declared_trees_and_preserves_observed_metadata(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            vault = Path(tmp)
            runs = vault / "runs"
            runs.mkdir()
            payload = b"---\nstatus: open\nretained_for: original criticism\n---\nA finding.\n"
            (runs / "one.md").write_bytes(payload)
            (runs / "README.md").write_bytes(b"\xff")
            # The other tree must not be scanned or interpreted.
            (vault / "other").write_bytes(b"\xff")
            entries = [entry(name="runs", path="runs", exempt=["README.md"]),
                       entry(name="other", path="other")]
            result = inventory(vault, entries, ["runs"])
            self.assertEqual(result["status"], "complete")
            self.assertEqual(len(result["artifacts"]), 1)
            files = result["artifacts"][0]["files"]
            self.assertEqual([f["path"] for f in files], ["runs/one.md"])
            self.assertEqual(files[0]["size"], len(payload))
            self.assertEqual(files[0]["frontmatter"]["retained_for"], "original criticism")
            self.assertEqual((runs / "one.md").read_bytes(), payload)
            with self.assertRaises(ValueError):
                inventory(vault, entries, ["undeclared"])

    def test_invalid_utf8_is_partial_observation_not_empty_metadata(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            vault = Path(tmp)
            (vault / "broken.md").write_bytes(b"\xff")
            result = inventory(vault, [entry(path="broken.md")], ["archive"])
            self.assertEqual(result["status"], "partial")
            observed = result["artifacts"][0]["files"][0]
            self.assertEqual(observed["status"], "unavailable")
            self.assertIn("sha256", observed)
            self.assertNotIn("frontmatter", observed)

    def test_status_validation_cannot_ignore_unreadable_proposal(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            vault = Path(tmp)
            (vault / "drafts").mkdir()
            (vault / "drafts/broken.md").write_bytes(b"---\nstatus: \xff\n---\n")
            problems = lifecycle_problems(vault, [entry(
                path="drafts", path_type="directory", validation="frontmatter-state",
                forbidden_states=["completed"])])
            self.assertEqual(len(problems), 1)
            self.assertIn("broken.md is unreadable", problems[0])

    def test_reparse_root_is_not_followed_or_treated_as_empty(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            vault = Path(tmp)
            (vault / "boundary").mkdir()
            with patch("artifact_lifecycle._is_reparse", return_value=True), \
                    patch("artifact_lifecycle.scan_tree") as scanner:
                result = inventory(vault, [entry(path="boundary")], ["archive"])
            scanner.assert_not_called()
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["artifacts"][0]["status"], "unavailable")
            self.assertIn("reparse boundary", result["artifacts"][0]["error"])

    def test_loader_rejects_empty_rooted_and_cross_platform_escaping_paths(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            vault = Path(tmp)
            declaration = vault / "memory/perspirator/artifact-lifecycle.json"
            declaration.parent.mkdir(parents=True)
            for unsafe in ("", ".", "../other", "C:other", "\\rooted", "..\\other"):
                declaration.write_text(json.dumps({"artifacts": [entry(path=unsafe)]}),
                                       encoding="utf-8")
                with self.subTest(path=unsafe), self.assertRaises(ValueError):
                    load_lifecycle(vault)


if __name__ == "__main__":
    unittest.main()
