import json
import tempfile
import unittest
from pathlib import Path

from artifact_lifecycle import lifecycle_problems, load_lifecycle


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


if __name__ == "__main__":
    unittest.main()
