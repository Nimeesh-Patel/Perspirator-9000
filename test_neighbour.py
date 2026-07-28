#!/usr/bin/env python3
"""Tests for neighbour.py index freshness and config handling.

Uses a stub embedder, so no model download and no network. Synthetic vault.
Run: python test_neighbour.py
"""

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402

import neighbour as nb  # noqa: E402
import problem_candidates as pc  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


class StubEmbedder:
    """Deterministic pseudo-vectors; counts what it was asked to embed."""

    def __init__(self):
        self.calls = []

    def __call__(self, texts, batch=32):
        self.calls.extend(texts)
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode()).digest()[:8]
            v = np.frombuffer(h, dtype=np.uint8).astype("float32")
            out.append(v / (np.linalg.norm(v) or 1.0))
        return np.vstack(out)


class FakePretrained:
    """Minimal from_pretrained stand-in for local-first loader tests."""

    def __init__(self, local=None, remote=None):
        self.local = local
        self.remote = remote
        self.calls = []

    def from_pretrained(self, model_name, **kwargs):
        local_only = kwargs.get("local_files_only", False)
        self.calls.append((model_name, local_only))
        result = self.local if local_only else self.remote
        if isinstance(result, Exception):
            raise result
        return result


CONFIG = """---
title: Neighbour Retrieval
status: active
---
# Neighbour Retrieval

## Model

- stub/model

## What is indexed

- corpus: vault, memory
- side: problem, answer, none

## Exempt

- .obsidian
- .trash

## Index location

- <vault>/.perspirator/neighbours.npz
"""


def write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def bump(path):
    """Ensure a visible mtime change even on coarse filesystem clocks."""
    text = path.read_text(encoding="utf-8")
    path.write_text(text, encoding="utf-8")
    import os
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 10))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write(root, "memory/perspirator/Neighbour Retrieval.md", CONFIG)
        a = write(root, "a.md", "problem a?\n***\nconjecture a.\n")
        b = write(root, "b.md", "problem b?\n***\nconjecture b.\n")
        write(root, ".trash/skip.md", "problem c?\n***\nc.\n")

        config = nb.load_config(root)
        check("config: model read from the note", config["model"] == "stub/model")
        check("config: index location resolves <vault>",
              config["index"] == root / ".perspirator" / "neighbours.npz",
              str(config["index"]))
        out = config["index"]

        cached = FakePretrained(local="cached", remote=AssertionError("network used"))
        check("model loader: complete cache makes no Hub request",
              nb.load_pretrained(cached, "stub/model", "model") == "cached"
              and cached.calls == [("stub/model", True)],
              str(cached.calls))

        uncached = FakePretrained(local=OSError("not cached"), remote="downloaded")
        check("model loader: cache miss falls back to download",
              nb.load_pretrained(uncached, "stub/model", "model") == "downloaded"
              and uncached.calls == [("stub/model", True), ("stub/model", False)],
              str(uncached.calls))

        unavailable = FakePretrained(local=OSError("not cached"),
                                     remote=ConnectionError("offline"))
        try:
            nb.load_pretrained(unavailable, "stub/model", "model")
            check("model loader: unavailable model fails concisely", False, "no SystemExit")
        except SystemExit as e:
            message = str(e)
            check("model loader: unavailable model fails concisely",
                  "local Hugging Face cache" in message
                  and "could not be downloaded" in message
                  and "ConnectionError: offline" in message,
                  message)

        stub = StubEmbedder()
        r1 = nb.refresh(root, config, out, embedder=stub)
        base = r1["chunks"]
        check("initial build embeds every chunk",
              r1["embedded"] == base and base > 0, str(r1))
        check("exempt folder excluded", not any(
            m["note"].startswith(".trash")
            for m in json.loads(str(np.load(out)["meta"]))))
        loaded = nb.load_index(root, index=out, refresh_index=False)
        check("shared index loader exposes aligned metadata and vectors",
              len(loaded["meta"]) == len(loaded["vectors"]) == base)
        check("lexical overlap remains an independent neighbour signal",
              nb.lexical_overlap(
                  "recurring cultural criticism preserves rational correction",
                  "recurring cultural criticism prevents rational correction") > 0.5)
        left = ("How can a criticism-preserving institution remain corrigible "
                "when its leaders become attached to authority?")
        right = ("What lets an organisation replace entrenched governors while "
                 "retaining practices that expose mistakes?")
        write(root, "memory/Left.md", f"## Problems\n\n{left}\n")
        write(root, "memory/Right.md", f"## Questions\n\n{right}\n")
        recurrence_index = {
            "meta": [
                {"stem": "Left", "text": left},
                {"stem": "Right", "text": right},
            ],
            "vectors": np.array([[1.0, 0.0], [0.95, 0.0]], dtype="float32"),
        }
        with patch.object(pc, "load_index", return_value=recurrence_index):
            recurrence = pc.signal_recurrence(
                root, set(), embedding_threshold=0.9,
                lexical_threshold=0.9, refresh_index=False)
        check("candidate recurrence reuses the neighbour substrate",
              len(recurrence) == 1
              and recurrence[0]["matched_by"] == ["embedding"]
              and recurrence[0]["lexical_score"] < 0.9)
        (root / "memory" / "Left.md").unlink()
        (root / "memory" / "Right.md").unlink()

        stub2 = StubEmbedder()
        r2 = nb.refresh(root, config, out, embedder=stub2)
        check("unchanged vault re-embeds nothing",
              r2["embedded"] == 0 and r2["notes_changed"] == 0 and r2["chunks"] == base,
              str(r2))
        check("unchanged vault does not call the model", stub2.calls == [])

        a.write_text("problem a?\n***\nconjecture a, revised.\n", encoding="utf-8")
        bump(a)
        stub3 = StubEmbedder()
        r3 = nb.refresh(root, config, out, embedder=stub3)
        check("edited note re-embeds only its changed chunk",
              r3["notes_changed"] == 1 and r3["embedded"] == 1, str(r3))
        check("the changed text is what got embedded",
              stub3.calls == ["conjecture a, revised."], str(stub3.calls))

        write(root, "c.md", "problem c?\n***\nconjecture c.\n")
        stub4 = StubEmbedder()
        r4 = nb.refresh(root, config, out, embedder=stub4)
        check("new note adds chunks without touching the rest",
              r4["chunks"] == base + 2 and r4["embedded"] == 2, str(r4))

        b.unlink()
        stub5 = StubEmbedder()
        r5 = nb.refresh(root, config, out, embedder=stub5)
        check("deleted note drops out of the index",
              r5["notes_removed"] == 1 and r5["chunks"] == base and r5["embedded"] == 0,
              str(r5))
        notes = {m["note"] for m in json.loads(str(np.load(out)["meta"]))}
        check("deleted note leaves no metadata behind", "b.md" not in notes, str(notes))

        # a changed model must never be silently mixed
        cfg_path = root / "memory" / "perspirator" / "Neighbour Retrieval.md"
        cfg_path.write_text(CONFIG.replace("stub/model", "other/model"), encoding="utf-8")
        try:
            nb.refresh(root, nb.load_config(root), out, embedder=StubEmbedder())
            check("model change is refused", False, "no SystemExit")
        except SystemExit as e:
            check("model change is refused", "not" in str(e) and "comparable" in str(e))

        # a changed indexing shape must not be silently reused
        cfg_path.write_text(CONFIG.replace("- corpus: vault, memory", "- corpus: memory"),
                            encoding="utf-8")
        try:
            nb.refresh(root, nb.load_config(root), out, embedder=StubEmbedder())
            check("indexing-shape change is refused", False, "no SystemExit")
        except SystemExit as e:
            check("indexing-shape change is refused", "different set of chunks" in str(e))

        # rebuild after a shape change succeeds and re-scopes
        cfg = nb.load_config(root)
        nb.refresh(root, cfg, out, rebuild=True, embedder=StubEmbedder())
        scoped = json.loads(str(np.load(out)["meta"]))
        check("rebuild after shape change re-scopes the index",
              scoped and all(m["corpus"] == "memory" for m in scoped),
              f"{len(scoped)} chunks, corpora={ {m['corpus'] for m in scoped} }")

        cfg_path.write_text(CONFIG.replace("status: active", "status: draft"),
                            encoding="utf-8")
        try:
            nb.load_config(root)
            check("inactive config note is refused", False, "no SystemExit")
        except SystemExit as e:
            check("inactive config note is refused", "not status: active" in str(e))

        cfg_path.unlink()
        try:
            nb.load_config(root)
            check("missing config note stops rather than improvising", False)
        except SystemExit as e:
            check("missing config note stops rather than improvising",
                  "STOP" in str(e) and "improvise" in str(e))

    print()
    print(f"{'FAILED: ' + ', '.join(FAILS) if FAILS else 'all checks passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
