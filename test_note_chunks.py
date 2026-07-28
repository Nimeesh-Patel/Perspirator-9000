#!/usr/bin/env python3
"""Tests for note_chunks.py. Synthetic fixtures only; no vault access.

Run: python test_note_chunks.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import note_chunks as nc
import policy_index as pi

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


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        p_problem = build(root, "asking dumb questions.md", PROBLEM_NOTE)
        p_multi = build(root, "memory/perspirator/runs/review.md", MULTILINE_LIST)
        p_fence = build(root, "fenced.md", FENCED)
        build(root, ".trash/ignored.md", PROBLEM_NOTE)
        build(root, "Attachments/skip.md", PROBLEM_NOTE)

        # --- side ---------------------------------------------------------
        cs = nc.chunks_for_note(p_problem, root)
        sides = [c["side"] for c in cs]
        check("problem side before the separator", sides[0] == "problem", str(sides))
        check("answer side after the separator", sides[1:] == ["answer", "answer"], str(sides))
        check("no separator -> side none",
              all(c["side"] == "none" for c in nc.chunks_for_note(p_fence, root)))

        # --- the regression that mattered ---------------------------------
        multi = nc.chunks_for_note(p_multi, root)
        first = next(c for c in multi if c["text"].startswith("1. **Policy"))
        check("multi-line list item is ONE chunk",
              first["text"].count("\n") == 2 and "changelog rule" in first["text"],
              f"{first['text'].count(chr(10))+1} lines")
        check("a second list item is a separate chunk",
              any(c["text"].startswith("2. **Second") for c in multi))

        # --- provenance ---------------------------------------------------
        check("heading path recorded", first["heading"] == ["Conflicts found"],
              str(first["heading"]))
        check("corpus memory vs vault",
              first["corpus"] == "memory" and cs[0]["corpus"] == "vault")
        check("note path is vault-relative posix",
              first["note"] == "memory/perspirator/runs/review.md", first["note"])
        raw = p_multi.read_text(encoding="utf-8").replace("\r\n", "\n")
        check("offsets slice back to the chunk text",
              raw[first["start"]:first["end"]] == first["text"])
        check("links captured per chunk",
              cs[1]["links"] == ["precautionary principle"], str(cs[1]["links"]))

        # --- fences -------------------------------------------------------
        fenced = nc.chunks_for_note(p_fence, root)
        block = next((c for c in fenced if c["text"].startswith("```")), None)
        check("fenced block stays whole across blank lines",
              block is not None and "line three" in block["text"])

        # --- filters and excludes ----------------------------------------
        every = nc.all_chunks(root)
        check("excluded folders are skipped",
              not any(c["note"].startswith((".trash", "Attachments")) for c in every))
        check("corpus filter", all(c["corpus"] == "memory"
                                   for c in nc.all_chunks(root, corpus="memory")))
        check("side filter", all(c["side"] == "problem"
                                 for c in nc.all_chunks(root, side="problem")))

        # --- frontmatter and links ---------------------------------------
        cat, up = nc.parse_frontmatter("category: Morality\nup:\n  - '[[epistemology]]'")
        check("frontmatter category + up", cat == "Morality" and up == ["epistemology"],
              f"{cat} {up}")
        check("frontmatter_fields reads top-level keys",
              nc.frontmatter_fields(PROBLEM_NOTE).get("category") == "Morality")

        refs, existing, notes = nc.vault_links(root)
        check("vault_links finds referrers",
              p_problem in refs.get("precautionary principle", set()))
        check("vault_links lists existing stems", "fenced" in existing)

        # --- failure cases ------------------------------------------------
        check("missing file returns no chunks",
              nc.chunks_for_note(root / "nope.md", root) == [])
        check("empty note returns no chunks",
              nc.chunks_for_note(build(root, "empty.md", ""), root) == [])
        check("frontmatter-only note returns no chunks",
              nc.chunks_for_note(build(root, "fm.md", "---\ntitle: x\n---\n"), root) == [])

        # --- active policy surface ---------------------------------------
        build(root, "memory/policies/Policy Loader.md", """---
title: Policy Loader
type: configuration
status: active
---
configuration only
""")
        policy = build(root, "memory/policies/Explanatory.md", """---
title: Explanatory Implementation
type: policy
status: active
---
## Problem

How should a mechanism vary?

## Conjecture

Prefer an explanatory implementation.
""")
        build(root, "memory/policies/Draft.md", """---
title: Draft
type: policy
status: draft
---
""")
        surface = pi.active_policy_surface(root)
        check("policy surface contains active policies but not configuration/drafts",
              surface == [{
                  "title": "Explanatory Implementation",
                  "path": "memory/policies/Explanatory.md",
                  "problem": "How should a mechanism vary?",
              }], str(surface))
        policy.write_text(policy.read_text(encoding="utf-8").replace(
            "## Conjecture\n\nPrefer an explanatory implementation.\n", ""),
            encoding="utf-8")
        try:
            pi.active_policy_surface(root)
            check("malformed active policy is refused", False, "no ValueError")
        except ValueError as exc:
            check("malformed active policy is refused",
                  "missing Conjecture" in str(exc), str(exc))

    print()
    print(f"{'FAILED: ' + ', '.join(FAILS) if FAILS else 'all checks passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
