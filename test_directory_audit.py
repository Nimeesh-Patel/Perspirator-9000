import tempfile
import unittest
import zipfile
from pathlib import Path

from directory_audit import (audit, compare_zip_folder,
                             discover_archive_pairs, exact_duplicate_groups,
                             scan_tree)


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


if __name__ == "__main__":
    unittest.main()
