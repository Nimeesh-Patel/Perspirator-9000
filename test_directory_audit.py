import tempfile
import unittest
import zipfile
import subprocess
from pathlib import Path

from directory_audit import (audit, compare_zip_folder,
                             compare_zip_git_head, discover_archive_pairs,
                             exact_duplicate_groups, scan_tree)


class DirectoryAuditTests(unittest.TestCase):
    def test_census_and_exact_duplicates_are_separate_facts(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            (root / "a.txt").write_bytes(b"same")
            (root / "nested").mkdir()
            (root / "nested" / "b.bin").write_bytes(b"same")
            (root / "unique.txt").write_bytes(b"different")

            files, boundaries, directories = scan_tree(root)
            groups = exact_duplicate_groups(files)

            self.assertEqual(len(files), 3)
            self.assertEqual(directories, 1)
            self.assertEqual(boundaries, [])
            self.assertEqual(groups[0]["paths"], ["a.txt", "nested/b.bin"])
            self.assertEqual(groups[0]["bytes_beyond_one_copy"], 4)

    def test_crc_verified_archive_and_extraction_are_identical(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            folder = root / "package"
            folder.mkdir()
            (folder / "one.txt").write_text("one", encoding="utf-8")
            (folder / "sub").mkdir()
            (folder / "sub" / "two.txt").write_text("two", encoding="utf-8")
            archive_path = root / "package.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.write(folder / "one.txt", "one.txt")
                archive.write(folder / "sub" / "two.txt", "sub/two.txt")

            result = compare_zip_folder(archive_path, folder, verify_crc=True)

            self.assertEqual(result["status"], "identical-representation")
            self.assertEqual(result["crc_matches"], 2)
            self.assertEqual(result["folder_extra"], 0)

    def test_common_archive_root_can_map_to_extracted_contents(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            folder = root / "package"
            folder.mkdir()
            (folder / "one.txt").write_text("one", encoding="utf-8")
            archive_path = root / "package.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.write(folder / "one.txt", "package/one.txt")

            result = compare_zip_folder(archive_path, folder, verify_crc=True)

            self.assertEqual(result["mapping"], "strip-common-root")
            self.assertEqual(result["status"], "identical-representation")

    def test_changed_extraction_is_not_collapsed_into_identity(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            folder = root / "package"
            folder.mkdir()
            (folder / "one.txt").write_text("changed", encoding="utf-8")
            archive_path = root / "package.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("one.txt", "original")

            result = compare_zip_folder(archive_path, folder, verify_crc=True)

            self.assertEqual(result["status"], "different")
            self.assertEqual(result["size_mismatches"], 1)

    def test_prefix_discovery_nominates_dated_archive_folder(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            folder = root / "export-2026-01-01"
            folder.mkdir()
            archive_path = root / "export-2026-01-01-content-hash.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("record.txt", "record")

            pairs = discover_archive_pairs(root)

            self.assertEqual(pairs, [(archive_path, folder, "directory-name-prefix")])

    def test_full_audit_preserves_semantic_limitations(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            (root / "item.txt").write_text("item", encoding="utf-8")

            result = audit(root, hash_duplicates=False, verify_archives=False)

            self.assertEqual(result["schema_version"], 1)
            self.assertIsNone(result["exact_duplicates"])
            self.assertIn("Usefulness", result["limitations"][2])

    def test_explicit_pair_separates_nomination_from_verification(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            folder = root / "renamed-output"
            folder.mkdir()
            (folder / "item.txt").write_text("item", encoding="utf-8")
            archive_path = root / "source-bundle.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("renamed-output/item.txt", "item")

            result = audit(
                root,
                hash_duplicates=False,
                verify_archives=True,
                explicit_archive_pairs=[(archive_path, folder)],
            )

            self.assertEqual(result["archive_pairs"][0]["discovery_rule"], "explicit")
            self.assertEqual(result["archive_pairs"][0]["status"],
                             "identical-representation")

    def test_zip_can_be_proved_identical_to_git_head(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email",
                            "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name",
                            "Directory Audit Test"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "core.autocrlf",
                            "false"], check=True)
            (repo / "one.txt").write_text("one\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "one.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "one"], check=True)
            archive_path = root / "repo-main.zip"
            subprocess.run([
                "git", "-C", str(repo), "archive", "--format=zip",
                "--prefix=repo-main/", f"--output={archive_path}", "HEAD",
            ], check=True)

            result = compare_zip_git_head(archive_path, repo)

            self.assertEqual(result["status"], "identical-head")
            self.assertEqual(result["same_as_head"], 1)
            self.assertFalse(result["working_tree_dirty"])

    def test_git_comparison_exposes_divergence_and_dirty_state(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email",
                            "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name",
                            "Directory Audit Test"], check=True)
            (repo / "changed.txt").write_text("head\n", encoding="utf-8")
            (repo / "extra.txt").write_text("extra\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "head"], check=True)
            (repo / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")
            archive_path = root / "snapshot.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("snapshot/changed.txt", "archive\n")
                archive.writestr("snapshot/missing.txt", "missing\n")

            result = compare_zip_git_head(archive_path, repo)

            self.assertEqual(result["status"], "divergent")
            self.assertEqual(result["changed_from_head"], 1)
            self.assertEqual(result["missing_from_head"], 1)
            self.assertEqual(result["head_extra_paths"], ["extra.txt"])
            self.assertTrue(result["working_tree_dirty"])


if __name__ == "__main__":
    unittest.main()
