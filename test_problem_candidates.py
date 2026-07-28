import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import problem_candidates as pc


class ProblemCandidateTests(unittest.TestCase):
    def test_embedding_and_lexical_recurrence_share_one_substrate(self):
        left = ("How can a criticism-preserving institution remain corrigible "
                "when its leaders become attached to authority?")
        right = ("What lets an organisation replace entrenched governors while "
                 "retaining practices that expose mistakes?")
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            memory = vault / "memory"
            memory.mkdir()
            (memory / "Left.md").write_text(
                f"# Left\n\n## Problems\n\n{left}\n", encoding="utf-8")
            (memory / "Right.md").write_text(
                f"# Right\n\n## Questions\n\n{right}\n", encoding="utf-8")
            loaded = {
                "meta": [
                    {"stem": "Left", "text": left},
                    {"stem": "Right", "text": right},
                ],
                "vectors": np.array([[1.0, 0.0], [0.95, 0.0]], dtype="float32"),
            }
            with patch.object(pc, "load_index", return_value=loaded):
                hits = pc.signal_recurrence(
                    vault, set(), embedding_threshold=0.9,
                    lexical_threshold=0.9, refresh_index=False)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["matched_by"], ["embedding"])
            self.assertLess(hits[0]["lexical_score"], 0.9)

    def test_lexical_overlap_is_retained_as_independent_critic(self):
        left = "How can a recurring cultural criticism preserve rational correction?"
        right = "Why can recurring cultural criticism prevent rational correction?"
        self.assertGreater(pc.lexical_overlap(left, right), 0.5)


if __name__ == "__main__":
    unittest.main()
