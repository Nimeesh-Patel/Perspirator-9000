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

from adapters import (ADAPTERS, VAULT_ENV,
                      adapter_directories, add_adapter_directory_arguments,
                      add_vault_argument, parse_target, render)
from contract_copy import validate_contract_copies
from installation import load_manifest, write_manifest


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

    def test_missing_contract_declaration_is_a_validation_failure(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            problems = validate_contract_copies(tmp)
        self.assertEqual(len(problems), 1)
        self.assertIn("declaration missing", problems[0])

    def test_install_migrates_both_hosts_to_one_toolkit(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            codex = root / "codex"
            claude = root / "claude"
            vault = root / "vault"
            vault.mkdir()
            for directory in (codex, claude):
                directory.mkdir()
                (directory / "policy_index.py").write_text("old copy", encoding="utf-8")
                (directory / "other.md").write_text("user file", encoding="utf-8")
                write_manifest(directory, ["policy_index.py"])
            repo = Path(__file__).resolve().parent
            completed = subprocess.run([
                sys.executable, str(repo / "install.py"),
                "--target", "All", "--codex-dir", str(codex), "--claude-dir", str(claude),
                "--vault", str(vault),
            ], capture_output=True, text=True)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            expected = render((repo / "SKILL.md").read_text(encoding="utf-8"), vault, repo)
            for directory, filename in ((codex, "SKILL.md"), (claude, "perspirate.md")):
                self.assertEqual((directory / filename).read_text(encoding="utf-8"), expected)
                self.assertEqual(set(load_manifest(directory)["files"]), {filename})
                self.assertFalse((directory / "policy_index.py").exists())
                self.assertEqual((directory / "other.md").read_text(encoding="utf-8"), "user file")


if __name__ == "__main__":
    unittest.main()
