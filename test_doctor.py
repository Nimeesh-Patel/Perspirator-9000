import json
import tempfile
import unittest
from pathlib import Path

from doctor import orphan_census_partition


class DoctorOrphanCensusTests(unittest.TestCase):
    def test_lifecycle_run_role_is_directed_evidence_not_ambient_memory(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            vault = Path(tmp)
            lifecycle_path = (
                vault / "memory" / "perspirator" / "artifact-lifecycle.json"
            )
            lifecycle_path.parent.mkdir(parents=True)
            lifecycle_path.write_text(
                json.dumps(
                    {
                        "artifacts": [
                            {
                                "name": "run reports",
                                "role": "run",
                                "path": "memory/perspirator/runs",
                                "validation": "unique-evidence",
                                "retire_when": "no unique criticism remains",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            current = vault / "memory" / "projects" / "Current.md"
            current.parent.mkdir(parents=True)
            current.write_text("current", encoding="utf-8")
            run = vault / "memory" / "perspirator" / "runs" / "Past.md"
            run.parent.mkdir(parents=True)
            run.write_text("past", encoding="utf-8")

            ambient, directed = orphan_census_partition(vault, [current, run])

            self.assertEqual(ambient, [current])
            self.assertEqual(directed, [run])

    def test_malformed_lifecycle_excludes_nothing(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            vault = Path(tmp)
            lifecycle_path = (
                vault / "memory" / "perspirator" / "artifact-lifecycle.json"
            )
            lifecycle_path.parent.mkdir(parents=True)
            lifecycle_path.write_text("not json", encoding="utf-8")
            note = vault / "memory" / "One.md"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text("one", encoding="utf-8")

            ambient, directed = orphan_census_partition(vault, [note])

            self.assertEqual(ambient, [note])
            self.assertEqual(directed, [])


if __name__ == "__main__":
    unittest.main()
