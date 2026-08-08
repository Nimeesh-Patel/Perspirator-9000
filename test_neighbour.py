#!/usr/bin/env python3
"""Contract tests for incremental neighbour indexing and CLI context."""

import hashlib
import io
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402

import neighbour as nb  # noqa: E402
from obsidian_cli import ObsidianCLI, exact_path, parsed  # noqa: E402
import problem_candidates as pc  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


class StubEmbedder:
    def __init__(self):
        self.calls = []

    def token_length(self, text):
        return len(text.split())

    def __call__(self, texts, batch=32):
        self.calls.extend(texts)
        out = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()[:8]
            vector = np.frombuffer(digest, dtype=np.uint8).astype("float32")
            out.append(vector / (np.linalg.norm(vector) or 1.0))
        return np.vstack(out)


class FakePretrained:
    def __init__(self, local=None, remote=None):
        self.local, self.remote, self.calls = local, remote, []

    def from_pretrained(self, model_name, **kwargs):
        local_only = kwargs.get("local_files_only", False)
        self.calls.append((model_name, local_only))
        result = self.local if local_only else self.remote
        if isinstance(result, Exception):
            raise result
        return result


class Completed:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


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
- unit: problem_identity, conjecture, block

## Unit formation

- problem: identity-and-contextual-conjecture
- non-problem: authored-blocks
- oversize: authored-boundaries-then-token-windows
- embedding-oversize: mean-pooled-token-windows
- max-tokens: 256

## Graph expansion

- provider: obsidian
- fallback: filesystem
- limit: 5
- timeout-seconds: 5

## Exempt

- .obsidian
- .trash

## Index location

- <vault>/.perspirator/neighbours.npz
"""


def write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def bump(path):
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
        check("config loads model and index path",
              config["model"] == "stub/model"
              and config["index"] == root / ".perspirator" / "neighbours.npz")
        check("config makes formation criticisable",
              config["formation"]["problem"] == "identity-and-contextual-conjecture"
              and config["formation"]["embedding-oversize"]
              == "mean-pooled-token-windows"
              and config["formation"]["max-tokens"] == 256)
        check("config selects bounded graph provider",
              config["graph"] == {"provider": "obsidian", "fallback": "filesystem",
                                  "limit": 5, "timeout": 5}, str(config["graph"]))
        out = config["index"]

        cached = FakePretrained(local="cached", remote=AssertionError("network used"))
        check("complete model cache makes no Hub request",
              nb.load_pretrained(cached, "stub/model", "model") == "cached"
              and cached.calls == [("stub/model", True)])
        uncached = FakePretrained(local=OSError("not cached"), remote="downloaded")
        check("cache miss falls back to download",
              nb.load_pretrained(uncached, "stub/model", "model") == "downloaded"
              and uncached.calls == [("stub/model", True), ("stub/model", False)])
        unavailable = FakePretrained(local=OSError("not cached"),
                                     remote=ConnectionError("offline"))
        try:
            nb.load_pretrained(unavailable, "stub/model", "model")
            check("unavailable model fails concisely", False, "no SystemExit")
        except SystemExit as exc:
            message = str(exc)
            check("unavailable model fails concisely",
                  "local Hugging Face cache" in message
                  and "ConnectionError: offline" in message, message)

        stub = StubEmbedder()
        first = nb.refresh(root, config, out, embedder=stub)
        base = first["units"]
        check("initial build embeds every retrieval unit",
              first["embedded"] == base and base > 0, str(first))
        with np.load(out, allow_pickle=False) as data:
            metadata = json.loads(str(data["meta"]))
        problem_units = [item for item in metadata if item["note"] == "a.md"]
        check("Problem Note index stores identity plus contextual conjecture",
              [item["unit"] for item in problem_units]
              == ["problem_identity", "conjecture"]
              and problem_units[1]["embedding_text"].startswith("a\nproblem a?\n***"),
              str(problem_units))
        check("exempt folder excluded",
              not any(item["note"].startswith(".trash") for item in metadata))
        loaded = nb.load_index(root, index=out, refresh_index=False)
        check("shared loader aligns metadata and vectors",
              len(loaded["meta"]) == len(loaded["vectors"]) == base)
        race = root / ".perspirator" / "race.npz"
        archives = [
            {"vectors": np.array([[1.0, 0.0]], dtype="float32"),
             "ids": json.dumps([f"id-{number}"]),
             "meta": json.dumps([{"note": f"{number}.md"}]),
             "header": json.dumps({"writer": number})}
            for number in (1, 2)
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda values: nb._atomic_savez(race, **values), archives))
        with np.load(race, allow_pickle=False) as race_data:
            race_writer = json.loads(str(race_data["header"]))["writer"]
            race_vectors = len(race_data["vectors"])
        check("concurrent refresh writes leave one complete readable index",
              race_writer in (1, 2) and race_vectors == 1)
        corpus = ["the growth of knowledge is unpredictable",
                  "knowledge grows through criticism",
                  "memetic warfare spreads rational memes",
                  "a tradition of criticism corrects errors",
                  "knowledge and criticism and errors and growth"]
        idf = nb.inverse_document_frequency(corpus)
        words = [nb.content_words(text) for text in corpus]
        check("a ubiquitous word carries almost no weight",
              idf["knowledge"] < idf["memetic"] / 2,
              f"knowledge={idf['knowledge']:.2f} memetic={idf['memetic']:.2f}")
        check("a rare term scores its unit fully",
              nb.lexical_coverage(nb.content_words("memetic warfare"),
                                  words[2], idf) == 1.0)
        check("a two-word query is not zeroed",
              nb.lexical_coverage(nb.content_words("memetic warfare"),
                                  words[2], idf) > 0)
        check("a unit sharing only a common word scores near zero",
              nb.lexical_coverage(nb.content_words("memetic warfare"),
                                  words[0], idf) == 0.0)

        meta = [{"note": f"n{i}.md", "text": text} for i, text in enumerate(corpus)]
        eligible = list(range(len(corpus)))
        cosine = [0.9, 0.8, 0.1, 0.7, 0.6]
        lexical = nb.lexical_scores("memetic warfare", meta, eligible, cosine)
        order, matched = nb.merge_rankers(eligible, meta, cosine, lexical, 4, "both")
        check("a lexical-only hit reaches the list cosine would have buried",
              2 in order and "lexical" in matched[2], f"{order} {matched}")
        check("the top cosine hit is still first",
              order[0] == 0 and "embedding" in matched[0], str(order))
        embed_only, _ = nb.merge_rankers(eligible, meta, cosine, lexical, 4, "embedding")
        check("--rank embedding reproduces the pure cosine order",
              embed_only == nb.collapse_by_note(eligible, meta, cosine, 4),
              str(embed_only))
        check("a zero-lexical unit is never nominated by the lexical ranker",
              all("lexical" not in matched[i] for i in order if lexical[i] == 0))

        import numpy as _np
        def unit(note, kind, heading=()):
            return {"note": note, "unit": kind, "heading": list(heading)}
        moved = _np.array([1.0, 0.0], "float32")
        same = _np.array([0.0, 1.0], "float32")
        near = _np.array([0.06, 0.998], "float32")
        # a.md's conjecture is SPLIT into two segments before and one after:
        # a per-segment key would compare unlike passages and cry drift.
        old_units = [unit("a.md", "conjecture"), unit("a.md", "conjecture"),
                     unit("b.md", "conjecture"), unit("c.md", "block")]
        new_units = [unit("a.md", "conjecture"),
                     unit("b.md", "conjecture"), unit("c.md", "block")]
        old_ids, new_ids = ["o1", "o1b", "o2", "o3"], ["n1", "n2", "n3"]
        vectors = {"o1": same, "o1b": same, "o2": same, "o3": same,
                   "n1": moved, "n2": same, "n3": moved}
        drift_log = root / "drift.jsonl"
        written = nb.record_drift(drift_log, old_units, old_ids, vectors,
                                  new_units, new_ids, vectors)
        events = [json.loads(line) for line in
                  drift_log.read_text(encoding="utf-8").splitlines()]
        check("a rewritten unit is logged with how far it moved",
              written == 1 and events[0]["note"] == "a.md"
              and events[0]["similarity"] == 0.0, str(events))
        check("an unchanged unit is not logged",
              all(e["note"] != "b.md" for e in events))
        check("blocks are not tracked, because they reorder",
              all(e["note"] != "c.md" for e in events))

        vectors["n1"] = near
        drift_log.unlink()
        nb.record_drift(drift_log, old_units, old_ids, vectors, new_units,
                        new_ids, vectors)
        events = [json.loads(line) for line in
                  drift_log.read_text(encoding="utf-8").splitlines()]
        check("a near-identical rewording is still recorded, not thresholded away",
              len(events) == 1 and 0.9 < events[0]["similarity"] < 1.0,
              str(events))


        split_old = [unit("d.md", "conjecture"), unit("d.md", "conjecture")]
        split_new = [unit("d.md", "conjecture"), unit("d.md", "conjecture"),
                     unit("d.md", "conjecture")]
        vecs = {"p1": same, "p2": same, "q1": same, "q2": same, "q3": same}
        log2 = root / "drift2.jsonl"
        n = nb.record_drift(log2, split_old, ["p1", "p2"], vecs,
                            split_new, ["q1", "q2", "q3"], vecs)
        check("re-segmenting an unchanged conjecture is not reported as drift",
              n == 0, f"{n} events")
        check("drift identity survives a rewrite that changes chunk_id",
              nb.drift_key(unit("a.md", "conjecture", ("H1",)))
              == nb.drift_key(unit("a.md", "conjecture", ("H1",))))
        check("drift identity separates the two sides of one note",
              nb.drift_key(unit("a.md", "conjecture"))
              != nb.drift_key(unit("a.md", "problem_identity")))

        left = ("How can a criticism-preserving institution remain corrigible "
                "when its leaders become attached to authority?")
        right = ("What lets an organisation replace entrenched governors while "
                 "retaining practices that expose mistakes?")
        write(root, "memory/Left.md", f"## Problems\n\n{left}\n")
        write(root, "memory/Right.md", f"## Questions\n\n{right}\n")
        write(root, "memory/perspirator/runs/derived.md",
              f"## Problems\n\n{left}\n")
        recurrence_index = {
            "meta": [{"stem": "Left", "text": left},
                     {"stem": "Right", "text": right}],
            "vectors": np.array([[1.0, 0.0], [0.95, 0.0]], dtype="float32"),
        }
        with patch.object(pc, "load_index", return_value=recurrence_index):
            recurrence = pc.signal_recurrence(
                root, set(), embedding_threshold=0.9,
                lexical_threshold=0.9, refresh_index=False,
                excluded={"memory/perspirator/runs"})
        check("recurrence reuses the shared substrate",
              len(recurrence) == 1 and recurrence[0]["matched_by"] == ["embedding"])
        check("candidate folder exclusions suppress derived report boilerplate",
              all("derived" not in hit["where"] for hit in recurrence))
        (root / "memory" / "Left.md").unlink()
        (root / "memory" / "Right.md").unlink()

        unchanged_stub = StubEmbedder()
        unchanged = nb.refresh(root, config, out, embedder=unchanged_stub)
        check("unchanged vault does not call the model",
              unchanged["embedded"] == 0 and unchanged_stub.calls == [])

        a.write_text("problem a?\n***\nconjecture a, revised.\n", encoding="utf-8")
        bump(a)
        edited_stub = StubEmbedder()
        edited = nb.refresh(root, config, out, embedder=edited_stub)
        check("edit re-embeds only changed contextual conjecture",
              edited["notes_changed"] == 1 and edited["embedded"] == 1, str(edited))
        check("embedded text retains the unchanged problem identity",
              edited_stub.calls == ["a\nproblem a?\n***\nconjecture a, revised."],
              str(edited_stub.calls))

        write(root, "c.md", "problem c?\n***\nconjecture c.\n")
        added = nb.refresh(root, config, out, embedder=StubEmbedder())
        check("new Problem Note adds exactly two units",
              added["units"] == base + 2 and added["embedded"] == 2, str(added))
        b.unlink()
        removed = nb.refresh(root, config, out, embedder=StubEmbedder())
        check("deleted Problem Note drops both units",
              removed["notes_removed"] == 1 and removed["units"] == base
              and removed["embedded"] == 0, str(removed))

        summary = nb.public_header({"model": "stub/model", "dim": 8, "units": base,
                                    "built": "now", "shape": config["shape"],
                                    "notes": {"a.md": [1, 2]}})
        check("public query provenance omits unbounded freshness stamps",
              "notes" not in summary and summary["shape"]["formation"]
              == config["formation"], str(summary))
        meta = [{"note": "same.md"}, {"note": "same.md"}, {"note": "other.md"}]
        scores = np.array([0.8, 0.9, 0.7])
        check("ranking collapses multiple unit hits to best note hit",
              nb.collapse_by_note([0, 1, 2], meta, scores, 5) == [1, 2])

        check("Obsidian no-result sentinels become empty collections",
              parsed("No links found.") == []
              and parsed("No backlinks found.") == [])
        pair_meta = [
            {'note': 'a.md'}, {'note': 'a.md'},
            {'note': 'b.md'}, {'note': 'c.md'},
        ]
        linked = write(
            root, 'linked.md',
            'problem?\n***\nThe link is on this side: [[b]].\n')
        check('complete-note link evidence crosses retrieval-unit boundaries',
              nb.note_link_stems(root, 'linked.md') == {'b'})
        linked.unlink()
        pair_vectors = np.array([
            [1.0, 0.0], [0.8, 0.2], [0.95, 0.05], [0.0, 1.0],
        ], dtype='float32')
        pairs = nb.top_note_pairs([0, 1, 2, 3], pair_meta, pair_vectors, 3)
        check('pair ranking collapses units to the best note pair',
              len(pairs) == 3
              and (pair_meta[pairs[0][1]]['note'],
                   pair_meta[pairs[0][2]]['note']) == ('a.md', 'b.md')
              and round(pairs[0][0], 2) == 0.95,
              str(pairs))

        calls = []
        def fake_runner(argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[1:] == ["vault", "info=name"]:
                return Completed(root.name)
            command = argv[2]
            if command == "backlinks":
                return Completed('[{"file":"source.md"}]')
            if command == "links":
                return Completed("target one.md\ntarget two.md\n")
            if command == "properties":
                return Completed('{"type":"Problem"}')
            if command == "search":
                return Completed('["a.md"]')
            return Completed("")

        cli = ObsidianCLI(root, runner=fake_runner, limit=1)
        context = cli.note_context("folder/note.md")
        check("CLI context combines links backlinks and properties",
              context["backlinks"] == ["source.md"]
              and context["links"] == ["target one.md"]
              and context["properties"] == {"type": "Problem"}, str(context))
        command_calls = [(argv, kwargs) for argv, kwargs in calls
                         if argv[1:2] != ["vault"]]
        check("CLI probes the open vault name before path commands",
              calls[0][0] == ["obsidian", "vault", "info=name"], str(calls[0]))

        def other_vault_runner(argv, **kwargs):
            if argv[1:] == ["vault", "info=name"]:
                return Completed("a different vault")
            return Completed("")
        wrong = ObsidianCLI(root, runner=other_vault_runner).note_context("n.md")
        check("CLI refuses context when another vault is open",
              wrong["status"] == "wrong-vault", str(wrong))
        check("CLI targets exact path and never invokes a shell",
              all("path=folder/note.md" in argv for argv, _ in command_calls[:3])
              and all(kwargs["shell"] is False for _, kwargs in command_calls[:3]),
              str(command_calls[:3]))
        cached_call_count = len(calls)
        check("CLI context cache avoids repeated subprocesses",
              cli.note_context("folder/note.md") == context
              and len(calls) == cached_call_count)

        flaky_backlinks = [True]
        def flaky_runner(argv, **kwargs):
            if argv[1:] == ["vault", "info=name"]:
                return Completed(root.name)
            command = argv[2]
            if command == "backlinks" and flaky_backlinks and flaky_backlinks.pop(0):
                return Completed(stderr="temporary failure", returncode=1)
            if command == "backlinks":
                return Completed("[]")
            if command == "properties":
                return Completed("{}")
            return Completed("")

        flaky = ObsidianCLI(root, runner=flaky_runner)
        first_context = flaky.note_context("retry.md")
        second_context = flaky.note_context("retry.md")
        check("failed path context stays retryable within the session",
              first_context["status"] == "error"
              and second_context["status"] == "ok", str(second_context))
        searched = cli.search("knowledge growth", path="memory")
        check("generic CLI adapter exposes bounded search",
              searched["data"] == ["a.md"]
              and "limit=1" in calls[-1][0], str(calls[-1]))
        try:
            exact_path("../outside.md")
            check("exact paths reject traversal", False, "no ValueError")
        except ValueError:
            check("exact paths reject traversal", True)

        class ContextCLI:
            def __init__(self, failing):
                self.failing = set(failing)

            def note_context(self, note):
                if note in self.failing:
                    return {"provider": "obsidian", "status": "unavailable",
                            "path": note, "error": "timed out"}
                return {"provider": "obsidian", "status": "ok", "path": note,
                        "backlinks": [], "links": [], "properties": {}}

        graph_results = [{"note": "a.md"}, {"note": "c.md"}]
        mixed = nb.expand_graph(root, graph_results, config, "obsidian", {},
                                cli=ContextCLI({"c.md"}))
        check("one graph failure falls back only for that note",
              mixed["provider"] == "mixed"
              and mixed["status"] == "partial-fallback"
              and [item["provider"] for item in mixed["notes"]]
              == ["obsidian", "filesystem"]
              and mixed["notes"][1]["status"] == "fallback", str(mixed))
        all_failed = nb.expand_graph(root, graph_results, config, "obsidian", {},
                                    cli=ContextCLI({"a.md", "c.md"}))
        check("total graph failure remains explicit",
              all_failed["provider"] == "filesystem"
              and all_failed["status"] == "fallback"
              and len(all_failed["issues"]) == 2, str(all_failed))
        no_fallback = dict(config)
        no_fallback["graph"] = dict(config["graph"], fallback="none")
        refused = nb.expand_graph(root, graph_results, no_fallback, "obsidian", {},
                                  cli=ContextCLI({"c.md"}))
        check("disabled graph fallback preserves the Obsidian error",
              refused["provider"] == "obsidian"
              and refused["status"] == "unavailable", str(refused))

        class BatchEmbedder:
            def __init__(self):
                self.batches = []

            def __call__(self, texts):
                self.batches.append(list(texts))
                return np.array([[1.0, 0.0], [0.0, 1.0]], dtype="float32")

        batch = BatchEmbedder()
        batch_loaded = {
            "vault": root, "config": config, "stale": None,
            "header": {"model": "stub/model", "chunks": 2, "units": 2,
                       "dim": 2, "built": "now", "shape": config["shape"]},
            "meta": [
                {"note": "a.md", "stem": "a", "heading": [], "side": "problem",
                 "unit": "problem_identity", "strategy": "whole",
                 "corpus": "vault", "text": "problem a?"},
                {"note": "c.md", "stem": "c", "heading": [], "side": "problem",
                 "unit": "problem_identity", "strategy": "whole",
                 "corpus": "vault", "text": "problem c?"},
            ],
            "vectors": np.array([[1.0, 0.0], [0.0, 1.0]], dtype="float32"),
        }
        batch_args = SimpleNamespace(
            vault=str(root), index=None, no_refresh=True,
            file=[str(a), str(root / "c.md")], stdin=False, text=None,
            corpus="all", side="all", unit="all", folder=None,
            k=1, graph="none", json=True)
        output = io.StringIO()
        with patch.object(nb, "load_index", return_value=batch_loaded), \
                patch.object(nb, "Embedder", return_value=batch), \
                redirect_stdout(output):
            nb.cmd_match(batch_args)
        payload = json.loads(output.getvalue())
        check("repeated files share one embedding batch and result envelope",
              len(batch.batches) == 1 and len(batch.batches[0]) == 2
              and len(payload["queries"]) == 2
              and [item["query_note"] for item in payload["queries"]]
              == ["a.md", "c.md"], str(payload))

        relative_batch = BatchEmbedder()
        relative_args = SimpleNamespace(
            vault=str(root), index=None, no_refresh=True,
            file=['a.md'], stdin=False, text=None,
            corpus='all', side='all', unit='all', folder=None,
            k=1, graph='none', json=True)
        output = io.StringIO()
        with patch.object(nb, 'load_index', return_value=batch_loaded), \
                patch.object(nb, 'Embedder', return_value=relative_batch), \
                redirect_stdout(output):
            nb.cmd_match(relative_args)
        payload = json.loads(output.getvalue())
        check('relative query file resolves against the supplied vault',
              bool(relative_batch.batches)
              and payload['query_note'] == 'a.md',
              str(payload))

        text_batch = BatchEmbedder()
        text_args = SimpleNamespace(
            vault=str(root), index=None, no_refresh=True,
            file=None, stdin=False, text=["problem a?", "problem c?"],
            corpus="all", side="all", unit="all", folder=None,
            k=1, graph="none", json=True)
        output = io.StringIO()
        with patch.object(nb, "load_index", return_value=batch_loaded), \
                patch.object(nb, "Embedder", return_value=text_batch), \
                redirect_stdout(output):
            nb.cmd_match(text_args)
        payload = json.loads(output.getvalue())
        check("repeated texts share one embedding batch and result envelope",
              text_batch.batches == [["problem a?", "problem c?"]]
              and len(payload["queries"]) == 2, str(payload))

        cfg_path = root / "memory" / "perspirator" / "Neighbour Retrieval.md"
        cfg_path.write_text(CONFIG.replace("stub/model", "other/model"), encoding="utf-8")
        try:
            nb.refresh(root, nb.load_config(root), out, embedder=StubEmbedder())
            check("model change is refused", False, "no SystemExit")
        except SystemExit as exc:
            check("model change is refused", "comparable" in str(exc))

        cfg_path.write_text(CONFIG.replace("- max-tokens: 256", "- max-tokens: 128"),
                            encoding="utf-8")
        try:
            nb.refresh(root, nb.load_config(root), out, embedder=StubEmbedder())
            check("formation-shape change is refused", False, "no SystemExit")
        except SystemExit as exc:
            check("formation-shape change is refused", "retrieval-unit shape" in str(exc))

        scoped_text = CONFIG.replace("- corpus: vault, memory", "- corpus: memory")
        cfg_path.write_text(scoped_text, encoding="utf-8")
        scoped_config = nb.load_config(root)
        nb.refresh(root, scoped_config, out, rebuild=True, embedder=StubEmbedder())
        with np.load(out, allow_pickle=False) as data:
            scoped = json.loads(str(data["meta"]))
        check("rebuild after shape change re-scopes index",
              scoped and all(item["corpus"] == "memory" for item in scoped))

        cfg_path.write_text(CONFIG.replace("status: active", "status: draft"),
                            encoding="utf-8")
        try:
            nb.load_config(root)
            check("inactive config is refused", False, "no SystemExit")
        except SystemExit as exc:
            check("inactive config is refused", "not status: active" in str(exc))
        cfg_path.unlink()
        try:
            nb.load_config(root)
            check("missing config stops rather than improvising", False)
        except SystemExit as exc:
            check("missing config stops rather than improvising",
                  "STOP" in str(exc) and "improvise" in str(exc))

    print()
    print(f"{'FAILED: ' + ', '.join(FAILS) if FAILS else 'all checks passed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
