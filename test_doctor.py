import argparse
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import adapters
import doctor
from installation import write_manifest


class DoctorActiveNoteTests(unittest.TestCase):
    def test_invalid_or_unavailable_active_text_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "Active.md"
            note.write_bytes(b"---\nstatus: active\n---\nBroken \xff text.\n")
            with patch.object(doctor, "RESULTS", []), contextlib.redirect_stdout(io.StringIO()):
                doctor.check_active_note("active note", note)
                self.assertEqual(doctor.RESULTS, [True, False])
            with patch.object(doctor, "RESULTS", []), \
                    patch.object(doctor, "read_note", return_value=None), \
                    contextlib.redirect_stdout(io.StringIO()):
                doctor.check_active_note("active note", note)
                self.assertEqual(doctor.RESULTS, [True, False])


class DoctorSourceComparisonTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.source = root / "source"
        self.installed = root / "installed"
        self.vault = root / "vault"
        self.source.mkdir()
        self.installed.mkdir()
        self.vault.mkdir()
        self.shared = ("probe.py", "doctor.py", "contract_copies.json")
        self.template = (
            "Read {{VAULT_PATH}}/memory/perspirator/Bootstrap.md\n"
            "Structural tools: {{TOOLS_DIR}}\n"
        )
        (self.source / "SKILL.md").write_text(self.template, encoding="utf-8")
        for relative in self.shared:
            text = '{"contracts": []}' if relative.endswith(".json") else "# original\n"
            (self.source / relative).write_text(text, encoding="utf-8")
            (self.installed / relative).write_text(text, encoding="utf-8")
        (self.installed / "SKILL.md").write_text(
            adapters.render(self.template, self.vault, self.installed),
            encoding="utf-8",
        )
        write_manifest(self.installed, [*self.shared, "SKILL.md"])

    def run_installed_doctor(self, source=None):
        args = argparse.Namespace(
            vault=str(self.vault), target="Codex", source=source,
            claude_dir=None, codex_dir=str(self.installed), custom_dir=None,
        )
        output = io.StringIO()
        with (
            patch.object(doctor, "__file__", str(self.installed / "doctor.py")),
            patch.object(doctor, "SHARED_FILES", self.shared),
            patch.object(adapters, "SHARED_FILES", self.shared),
            patch.object(doctor, "RESULTS", []),
            patch.object(doctor, "arguments", return_value=args),
            patch.object(doctor, "validate_vault"),
            contextlib.redirect_stdout(output),
        ):
            result = doctor.main()
        return result, output.getvalue()

    def test_installed_copy_without_source_cannot_claim_source_equality(self):
        (self.installed / "probe.py").write_text("# locally modified\n", encoding="utf-8")

        result, output = self.run_installed_doctor()

        self.assertEqual(result, 0, output)
        self.assertIn("repository source comparison unavailable", output)
        self.assertNotIn("installed shared file matches source", output)
        self.assertNotIn("render matches canonical bootstrap source exactly", output)
        self.assertIn("[ok  ] installed shared file exists: probe.py", output)
        self.assertIn("[ok  ] generated-file ownership is current", output)
        self.assertTrue(output.rstrip().endswith("repository source comparison unavailable"))

    def test_explicit_source_detects_modified_installed_copy(self):
        (self.installed / "probe.py").write_text("# locally modified\n", encoding="utf-8")

        result, output = self.run_installed_doctor(str(self.source / "SKILL.md"))

        self.assertEqual(result, 1, output)
        self.assertIn("[FAIL] installed shared file matches source: probe.py", output)
        self.assertIn("[ok  ] render matches canonical bootstrap source exactly", output)
        self.assertNotIn("repository source comparison unavailable", output)

    def test_missing_installed_file_still_fails_without_source(self):
        (self.installed / "probe.py").unlink()

        result, output = self.run_installed_doctor()

        self.assertEqual(result, 1, output)
        self.assertIn("[FAIL] installed shared file exists: probe.py", output)

    def test_explicit_rendered_adapter_is_not_accepted_as_canonical_source(self):
        result, output = self.run_installed_doctor(str(self.installed / "SKILL.md"))

        self.assertEqual(result, 1, output)
        self.assertIn("[FAIL] canonical source template is available", output)
        self.assertNotIn("installed shared file matches source", output)


if __name__ == "__main__":
    unittest.main()
