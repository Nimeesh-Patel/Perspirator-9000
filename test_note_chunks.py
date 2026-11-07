#!/usr/bin/env python3
"""Contract tests for structural parsing and retrieval-unit formation."""

import json
import tempfile
import unittest
from pathlib import Path

import note_chunks as nc
import policy_index as pi
import doctor
from problem_half import parse_note


PROBLEM_NOTE = """---
up: null
category: Morality
---
why is asking dumb questions important?
***
because not asking them is a form of the [[precautionary principle]].

it also prevents [[the growth of knowledge]].
"""

MULTILINE_LIST = """---
title: Runtime review
---

## Conflicts found

1. **Policy delegation vs policy selection.** The runtime does not say
   whether "current" means every file, only `status: active`, or the
   Policy Index. This matters because the changelog rule still stands.
2. **Second problem.** A shorter one.

## Notes

Prose paragraph.
"""

FENCED = """# Title

```text
line one
line two

line three
```

after the fence.
"""


def build(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def words(text):
    return len(text.split())


class ContractSuite(unittest.TestCase):
    def test_contract_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p_problem = build(root, "asking dumb questions.md", PROBLEM_NOTE)
            p_multi = build(root, "memory/perspirator/runs/review.md", MULTILINE_LIST)
            p_fence = build(root, "fenced.md", FENCED)
            build(root, ".trash/ignored.md", PROBLEM_NOTE)
            build(root, "Attachments/skip.md", PROBLEM_NOTE)

            parsed = parse_note(PROBLEM_NOTE)
            self.assertTrue(parsed["problem"].startswith("why is")
                  and parsed["conjecture"].startswith("because not"), "shared parser exposes both sides")
            self.assertTrue(parsed["normalized"][parsed["problem_start"]:parsed["problem_end"]]
                  == parsed["problem"]
                  and parsed["normalized"][parsed["conjecture_start"]:parsed["conjecture_end"]]
                  == parsed["conjecture"], "parser offsets are inspectable")
            fixture_path = Path(__file__).parent / "fixtures" / "problem_note_conformance.json"
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            conforming = []
            for case in fixture["cases"]:
                value = parse_note(case["text"])
                conforming.append(
                    value["has_separator"] == case["has_separator"]
                    and value["problem"] == case["problem"]
                    and value["conjecture"] == case["conjecture"]
                    and value["has_separator"]
                    == case["reviewable"])
            self.assertTrue(all(conforming), ("Python parser conforms to shared Problem Note fixtures", str([fixture["cases"][i]["name"]
                                        for i, ok in enumerate(conforming) if not ok])))
            units = nc.chunks_for_note(p_problem, root)
            self.assertTrue(len(units) == 2, ("short Problem Note has exactly two units", str(len(units))))
            self.assertTrue(units[0]["unit"] == "problem_identity"
                  and units[0]["side"] == "problem"
                  and units[0]["text"] == parsed["problem"], "problem identity is complete")
            self.assertTrue(units[1]["unit"] == "conjecture"
                  and units[1]["side"] == "answer"
                  and units[1]["text"] == parsed["conjecture"], "conjecture remains one content unit")
            self.assertTrue(parsed["problem"] in units[1]["embedding_text"]
                  and parsed["conjecture"] in units[1]["embedding_text"], "conjecture embedding carries problem identity")
            self.assertTrue(units[1]["links"] == ["precautionary principle", "the growth of knowledge"], ("links captured from intact conjecture", str(units[1]["links"])))

            long_answer = """why preserve authored boundaries?
***
## Source one

alpha beta gamma delta epsilon.

source one continues with criticism.

---

## Source two

zeta eta theta iota kappa.
"""
            p_long = build(root, "long.md", long_answer)
            long_units = nc.chunks_for_note(p_long, root, max_tokens=12, token_length=words)
            answers = [unit for unit in long_units if unit["side"] == "answer"]
            self.assertTrue(len(answers) > 1 and all(unit["unit"] == "conjecture" for unit in answers), ("oversize conjecture splits only after whole-unit test", str([(u["strategy"], u["text"]) for u in answers])))
            self.assertTrue(all("why preserve authored boundaries?" in unit["embedding_text"]
                      for unit in answers), "each oversize segment retains identity context")
            self.assertTrue(any(unit["heading"] == ["Source two"] for unit in answers), ("authored heading paths survive splitting", str([unit["heading"] for unit in answers])))

            giant = "why avoid blind windows?\n***\n" + " ".join(f"word{i}" for i in range(35))
            p_giant = build(root, "giant.md", giant)
            giant_answers = [unit for unit in nc.chunks_for_note(
                p_giant, root, max_tokens=12, token_length=words) if unit["side"] == "answer"]
            self.assertTrue(len(giant_answers) > 1
                  and all(unit["strategy"] == "token-window" for unit in giant_answers), ("single oversized authored block uses token-window fallback", str([unit["strategy"] for unit in giant_answers])))
            self.assertTrue(" ".join(unit["text"] for unit in giant_answers)
                  == " ".join(f"word{i}" for i in range(35)), "token windows preserve all source words in order")

            plain_text = " ".join(f"plain{i}" for i in range(30))
            p_plain = build(root, "long plain.md", plain_text)
            plain_units = nc.chunks_for_note(
                p_plain, root, max_tokens=12, token_length=words)
            self.assertTrue(len(plain_units) > 1
                  and all(unit["unit"] == "block"
                          and unit["strategy"] == "token-window"
                          for unit in plain_units), "oversized non-problem block uses token-window units")
            self.assertTrue(" ".join(unit["text"] for unit in plain_units) == plain_text, "non-problem windows preserve source order")
            multi = nc.chunks_for_note(p_multi, root)
            first = next(unit for unit in multi if unit["text"].startswith("1. **Policy"))
            self.assertTrue(first["text"].count("\n") == 2 and "changelog rule" in first["text"], "non-problem multi-line list item stays one block")
            self.assertTrue(first["unit"] == "block" and first["strategy"] == "authored-blocks", "non-problem formation is explicit")
            self.assertTrue(any(unit["text"].startswith("2. **Second") for unit in multi), "a second list item is separate")
            self.assertTrue(first["heading"] == ["Conflicts found"], "heading path recorded")
            self.assertTrue(first["corpus"] == "memory" and units[0]["corpus"] == "vault", "corpus memory vs vault")
            raw = p_multi.read_text(encoding="utf-8").replace("\r\n", "\n")
            self.assertTrue(raw[first["start"]:first["end"]] == first["text"], "offsets slice back to display text")

            fenced = nc.chunks_for_note(p_fence, root)
            block = next((unit for unit in fenced if unit["text"].startswith("```")), None)
            self.assertTrue(block is not None and "line three" in block["text"], "fenced block stays whole across blank lines")
            self.assertTrue(all(unit["side"] == "none" for unit in fenced), "no separator uses side none")

            every = nc.all_chunks(root)
            self.assertTrue(not any(unit["note"].startswith((".trash", "Attachments")) for unit in every), "excluded folders are skipped")
            self.assertTrue(all(unit["corpus"] == "memory"
                                       for unit in nc.all_chunks(root, corpus="memory")), "corpus filter")
            self.assertTrue(all(unit["side"] == "problem"
                                     for unit in nc.all_chunks(root, side="problem")), "side filter")

            category, up = nc.parse_frontmatter(
                "category: Morality\nup:\n  - '[[epistemology]]'")
            self.assertTrue(category == "Morality" and up == ["epistemology"], "frontmatter category + up")
            self.assertTrue(nc.frontmatter_fields(PROBLEM_NOTE).get("category") == "Morality", "frontmatter_fields reads top-level keys")
            category, up = nc.parse_frontmatter(
                'up:\n- [[first parent]]\n- [[second parent]]\ncategory: Default')
            self.assertTrue(category == 'Default' and up == ['first parent', 'second parent'], 'frontmatter accepts unindented YAML up lists')
            refs, existing, _ = nc.vault_links(root)
            self.assertTrue(p_problem in refs.get("precautionary principle", set()), "vault_links finds referrers")
            self.assertTrue("fenced" in existing, "vault_links lists existing stems")

            self.assertTrue(nc.chunks_for_note(root / "nope.md", root) == [], "missing file returns no units")
            self.assertTrue(nc.chunks_for_note(build(root, "empty.md", ""), root) == [], "empty note returns no units")
            self.assertTrue(nc.chunks_for_note(build(root, "fm.md", "---\ntitle: x\n---\n"), root) == [], "frontmatter-only note returns no units")

            build(root, "memory/perspirator/proposals/open.md",
                  "---\nstatus: partially-applied\n---\n")
            build(root, "memory/perspirator/proposals/done.md",
                  "---\nstatus: completed\n---\n")
            build(root, "memory/perspirator/proposals/README.md", "# Proposals\n")
            self.assertTrue(doctor.lifecycle_problems(root, [{
                      "name": "proposals", "role": "proposal",
                      "path": "memory/perspirator/proposals",
                      "validation": "frontmatter-state",
                      "state_field": "status",
                      "forbidden_states": ["completed"],
                      "retire_when": "the decision is terminal",
                  }]) == ["proposals: done.md is terminal (status: completed)"], "terminal proposals are surfaced by the general lifecycle check")

            build(root, "memory/policies/Policy Loader.md", """---
title: Policy Loader
type: configuration
status: active
---
configuration only
""")
            policy = build(root, "memory/policies/Explanatory.md", """---
title: Criticisable Implementation
type: policy
status: active
---
## Problem

How should a mechanism vary?

## Conjecture

Keep implementation criticisable.
""")
            build(root, "memory/policies/Draft.md", """---
title: Draft
type: policy
status: draft
---
""")
            surface = pi.active_policy_surface(root)
            self.assertTrue(surface == [{"title": "Criticisable Implementation",
                               "path": "memory/policies/Explanatory.md",
                               "problem": "How should a mechanism vary?"}], ("policy surface contains active policies but not configuration/drafts", str(surface)))
            policy.write_text(policy.read_text(encoding="utf-8").replace(
                "## Conjecture\n\nKeep implementation criticisable.\n", ""), encoding="utf-8")
            try:
                pi.active_policy_surface(root)
                self.assertTrue(False, ("malformed active policy is refused", "no ValueError"))
            except ValueError as exc:
                self.assertTrue("missing Conjecture" in str(exc), "malformed active policy is refused")

    def test_invalid_utf8_is_not_replaced_inside_explanations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.md"
            path.write_bytes(b"Problem \xff\n***\nConjecture")
            with self.assertRaisesRegex(ValueError, "not valid UTF-8"):
                nc.read_note(path)


if __name__ == "__main__":
    unittest.main()
