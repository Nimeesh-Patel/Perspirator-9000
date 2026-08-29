#!/usr/bin/env python3
"""Contract tests for structural parsing and retrieval-unit formation."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import note_chunks as nc
import policy_index as pi
import doctor
from problem_half import parse_note

FAILS = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


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


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build(root, ".perspirator/transactions/run/before/ignored.md", PROBLEM_NOTE)
        p_problem = build(root, "asking dumb questions.md", PROBLEM_NOTE)
        p_multi = build(root, "memory/perspirator/runs/review.md", MULTILINE_LIST)
        p_fence = build(root, "fenced.md", FENCED)
        build(root, ".trash/ignored.md", PROBLEM_NOTE)
        build(root, "Attachments/skip.md", PROBLEM_NOTE)
        build(root, "Interesting/Templates/default.md", "---\ntags: []\n---\n\n***\n")

        parsed = parse_note(PROBLEM_NOTE)
        check("shared parser exposes both sides", parsed["problem"].startswith("why is")
              and parsed["conjecture"].startswith("because not"))
        check("parser offsets are inspectable",
              parsed["normalized"][parsed["problem_start"]:parsed["problem_end"]]
              == parsed["problem"]
              and parsed["normalized"][parsed["conjecture_start"]:parsed["conjecture_end"]]
              == parsed["conjecture"])
        fixture_path = Path(__file__).parent / "fixtures" / "problem_note_conformance.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        conforming = []
        for case in fixture["cases"]:
            value = parse_note(case["text"])
            conforming.append(
                value["has_separator"] == case["has_separator"]
                and value["problem"] == case["problem"]
                and value["conjecture"] == case["conjecture"]
                and bool(value["problem"] and value["conjecture"])
                == case["reviewable"])
        check("Python parser conforms to shared Problem Note fixtures",
              all(conforming), str([fixture["cases"][i]["name"]
                                    for i, ok in enumerate(conforming) if not ok]))
        units = nc.chunks_for_note(p_problem, root)
        check("short Problem Note has exactly two units", len(units) == 2, str(len(units)))
        check("problem identity is complete",
              units[0]["unit"] == "problem_identity"
              and units[0]["side"] == "problem"
              and units[0]["text"] == parsed["problem"])
        check("conjecture remains one content unit",
              units[1]["unit"] == "conjecture"
              and units[1]["side"] == "answer"
              and units[1]["text"] == parsed["conjecture"])
        check("conjecture embedding carries problem identity",
              parsed["problem"] in units[1]["embedding_text"]
              and parsed["conjecture"] in units[1]["embedding_text"])
        check("links captured from intact conjecture",
              units[1]["links"] == ["precautionary principle", "the growth of knowledge"],
              str(units[1]["links"]))

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
        check("oversize conjecture splits only after whole-unit test",
              len(answers) > 1 and all(unit["unit"] == "conjecture" for unit in answers),
              str([(u["strategy"], u["text"]) for u in answers]))
        check("each oversize segment retains identity context",
              all("why preserve authored boundaries?" in unit["embedding_text"]
                  for unit in answers))
        check("authored heading paths survive splitting",
              any(unit["heading"] == ["Source two"] for unit in answers),
              str([unit["heading"] for unit in answers]))

        giant = "why avoid blind windows?\n***\n" + " ".join(f"word{i}" for i in range(35))
        p_giant = build(root, "giant.md", giant)
        giant_answers = [unit for unit in nc.chunks_for_note(
            p_giant, root, max_tokens=12, token_length=words) if unit["side"] == "answer"]
        check("single oversized authored block uses token-window fallback",
              len(giant_answers) > 1
              and all(unit["strategy"] == "token-window" for unit in giant_answers),
              str([unit["strategy"] for unit in giant_answers]))
        check("token windows preserve all source words in order",
              " ".join(unit["text"] for unit in giant_answers)
              == " ".join(f"word{i}" for i in range(35)))

        plain_text = " ".join(f"plain{i}" for i in range(30))
        p_plain = build(root, "long plain.md", plain_text)
        plain_units = nc.chunks_for_note(
            p_plain, root, max_tokens=12, token_length=words)
        check("oversized non-problem block uses token-window units",
              len(plain_units) > 1
              and all(unit["unit"] == "block"
                      and unit["strategy"] == "token-window"
                      for unit in plain_units))
        check("non-problem windows preserve source order",
              " ".join(unit["text"] for unit in plain_units) == plain_text)
        multi = nc.chunks_for_note(p_multi, root)
        first = next(unit for unit in multi if unit["text"].startswith("1. **Policy"))
        check("non-problem multi-line list item stays one block",
              first["text"].count("\n") == 2 and "changelog rule" in first["text"])
        check("non-problem formation is explicit",
              first["unit"] == "block" and first["strategy"] == "authored-blocks")
        check("a second list item is separate",
              any(unit["text"].startswith("2. **Second") for unit in multi))
        check("heading path recorded", first["heading"] == ["Conflicts found"])
        check("corpus memory vs vault",
              first["corpus"] == "memory" and units[0]["corpus"] == "vault")
        raw = p_multi.read_text(encoding="utf-8").replace("\r\n", "\n")
        check("offsets slice back to display text",
              raw[first["start"]:first["end"]] == first["text"])

        fenced = nc.chunks_for_note(p_fence, root)
        block = next((unit for unit in fenced if unit["text"].startswith("```")), None)
        check("fenced block stays whole across blank lines",
              block is not None and "line three" in block["text"])
        check("no separator uses side none", all(unit["side"] == "none" for unit in fenced))

        every = nc.all_chunks(root)
        indexed = json.loads(subprocess.run(
            [sys.executable, str(Path(__file__).parent / "problem_index.py"), str(root)],
            capture_output=True, text=True, check=True).stdout)
        check("problem index contains only root Problem Notes",
              not any(item["path"].startswith(".perspirator/") for item in indexed)
              and not any("/" in item["path"] for item in indexed)
              and any(item["path"] == "asking dumb questions.md" for item in indexed),
              str([item["path"] for item in indexed]))
        check("excluded folders are skipped",
              not any(unit["note"].startswith((".trash", "Attachments")) for unit in every))
        check("corpus filter", all(unit["corpus"] == "memory"
                                   for unit in nc.all_chunks(root, corpus="memory")))
        check("side filter", all(unit["side"] == "problem"
                                 for unit in nc.all_chunks(root, side="problem")))

        category, up = nc.parse_frontmatter(
            "category: Morality\nup:\n  - '[[epistemology]]'")
        check("frontmatter category + up",
              category == "Morality" and up == ["epistemology"])
        check("frontmatter_fields reads top-level keys",
              nc.frontmatter_fields(PROBLEM_NOTE).get("category") == "Morality")
        category, up = nc.parse_frontmatter(
            'up:\n- [[first parent]]\n- [[second parent]]\ncategory: Default')
        check('frontmatter accepts unindented YAML up lists',
              category == 'Default' and up == ['first parent', 'second parent'])
        refs, existing, _ = nc.vault_links(root)
        check("vault_links finds referrers",
              p_problem in refs.get("precautionary principle", set()))
        check("vault_links lists existing stems", "fenced" in existing)

        check("missing file returns no units",
              nc.chunks_for_note(root / "nope.md", root) == [])
        check("empty note returns no units",
              nc.chunks_for_note(build(root, "empty.md", ""), root) == [])
        check("frontmatter-only note returns no units",
              nc.chunks_for_note(build(root, "fm.md", "---\ntitle: x\n---\n"), root) == [])

        proposals = root / "memory/perspirator/proposals"
        build(root, "memory/perspirator/proposals/open.md",
              "---\nstatus: partially-applied\n---\n")
        build(root, "memory/perspirator/proposals/done.md",
              "---\nstatus: completed\n---\n")
        build(root, "memory/perspirator/proposals/README.md", "# Proposals\n")
        check("terminal proposals are surfaced by the general lifecycle check",
              doctor.lifecycle_problems(root, [{
                  "name": "proposals", "role": "proposal",
                  "path": "memory/perspirator/proposals",
                  "validation": "frontmatter-state",
                  "state_field": "status",
                  "forbidden_states": ["completed"],
                  "retire_when": "the decision is terminal",
              }]) == ["proposals: done.md is terminal (status: completed)"])

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
        check("policy surface contains active policies but not configuration/drafts",
              surface == [{"title": "Criticisable Implementation",
                           "path": "memory/policies/Explanatory.md",
                           "problem": "How should a mechanism vary?"}], str(surface))
        policy.write_text(policy.read_text(encoding="utf-8").replace(
            "## Conjecture\n\nKeep implementation criticisable.\n", ""), encoding="utf-8")
        try:
            pi.active_policy_surface(root)
            check("malformed active policy is refused", False, "no ValueError")
        except ValueError as exc:
            check("malformed active policy is refused", "missing Conjecture" in str(exc))

    print()
    print(f"{'FAILED: ' + ', '.join(FAILS) if FAILS else 'all checks passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
