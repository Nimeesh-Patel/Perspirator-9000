import argparse
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adapters import (ADAPTERS, SHARED_FILES, VAULT_ENV,
                      adapter_directories, add_adapter_directory_arguments,
                      add_vault_argument, generated_files, parse_target)
from contract_copy import validate_contract_copies


class AdapterDeclarationTests(unittest.TestCase):
    def test_targets_and_directory_options_come_from_one_declaration(self):
        parser = argparse.ArgumentParser()
        add_adapter_directory_arguments(parser)
        args = parser.parse_args([
            ADAPTERS["ClaudeCode"]["directory_option"], "claude-target",
            ADAPTERS["Codex"]["directory_option"], "codex-target",
        ])
        directories = adapter_directories(args)

        self.assertEqual(parse_target("claude"), "ClaudeCode")
        self.assertEqual(parse_target("codex"), "Codex")
        self.assertEqual(directories["ClaudeCode"].name, "claude-target")
        self.assertEqual(directories["Codex"].name, "codex-target")

    def test_vault_is_explicit_or_environment_derived(self):
        with patch.dict(os.environ, {VAULT_ENV: "configured-vault"}):
            parser = argparse.ArgumentParser()
            add_vault_argument(parser)
            self.assertEqual(parser.parse_args([]).vault, "configured-vault")

        with patch.dict(os.environ, {}, clear=True):
            parser = argparse.ArgumentParser()
            add_vault_argument(parser)
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parser.parse_args([])
            self.assertEqual(parser.parse_args(["--vault", "explicit-vault"]).vault,
                             "explicit-vault")

    def test_install_surface_contains_contract_declaration_and_fixture(self):
        self.assertIn("contract_copies.json", SHARED_FILES)
        self.assertIn("fixtures/problem_note_conformance.json", SHARED_FILES)
        self.assertIn("contract_copies.json", generated_files("Codex"))

    def test_spotify_ledger_is_shared_toolkit(self):
        self.assertIn("spotify_ledger.py", SHARED_FILES)
        self.assertIn("spotify_ledger.py", generated_files("Codex"))

    def test_missing_contract_declaration_is_a_validation_failure(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            problems = validate_contract_copies(tmp)
        self.assertEqual(len(problems), 1)
        self.assertIn("declaration missing", problems[0])

    def test_custom_install_copies_nested_contract_assets(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            destination = root / "installed"
            vault = root / "vault"
            vault.mkdir()
            completed = subprocess.run([
                sys.executable, str(Path(__file__).resolve().parent / "install.py"),
                "--target", "Custom", "--destination", str(destination),
                "--vault", str(vault),
            ], capture_output=True, text=True)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            for relative in (
                    "contract_copies.json",
                    "fixtures/problem_note_conformance.json"):
                self.assertTrue((destination / relative).is_file(), relative)


if __name__ == "__main__":
    unittest.main()
