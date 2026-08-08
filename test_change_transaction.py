import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import change_transaction as tx


class ChangeTransactionTests(unittest.TestCase):
    def test_create_records_reversible_metadata(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            target = Path(tmp) / "new.md"
            operation = tx.write_operation(target, b"after")
            result = tx.apply_writes([operation])
            self.assertEqual(result["status"], "applied")
            self.assertEqual(target.read_bytes(), b"after")
            self.assertEqual(result["operations"][0]["rollback"]["action"],
                             "delete-if-hash-matches")

    def test_stale_precondition_refuses_every_write(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            first, second = root / "first.md", root / "second.md"
            first.write_bytes(b"before")
            operations = [tx.write_operation(first, b"after"),
                          tx.write_operation(second, b"created")]
            first.write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "preconditions changed"):
                tx.apply_writes(operations)
            self.assertFalse(second.exists())

    def test_unexplained_observed_state_is_indeterminate(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            target = Path(tmp) / "note.md"
            target.write_bytes(b"before")
            operation = tx.write_operation(target, b"after")
            original_write = Path.write_bytes

            def corrupt(_payload):
                original_write(target, b"neither")
                raise OSError("ambiguous write")

            with patch.object(Path, "write_bytes", side_effect=corrupt):
                result = tx.apply_writes([operation])
            self.assertEqual(result["status"], "indeterminate")
            self.assertTrue(result["do_not_retry"])


if __name__ == "__main__":
    unittest.main()
