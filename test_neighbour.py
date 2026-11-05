#!/usr/bin/env python3
"""Contract tests for incremental neighbour indexing and CLI context."""

import hashlib
import io
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import neighbour as nb
from obsidian_cli import ObsidianCLI, exact_path, parsed
import problem_candidates as pc


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


class ContractSuite(unittest.TestCase):
    def test_contract_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "memory/perspirator/Neighbour Retrieval.md", CONFIG)
            a = write(root, "a.md", "problem a?\n***\nconjecture a.\n")
            b = write(root, "b.md", "problem b?\n***\nconjecture b.\n")
            write(root, ".trash/skip.md", "problem c?\n***\nc.\n")

            config = nb.load_config(root)
            forged = CONFIG.replace("## Unit formation\n", "## Unit formation\n\n"
                "- problem: forged\n- non-problem: forged\n"
                "- oversize: forged\n- embedding-oversize: forged\n")
            write(root, "memory/perspirator/Neighbour Retrieval.md", forged)
            labelled = nb.load_config(root)
            self.assertEqual(labelled["formation"], config["formation"])
            self.assertEqual(labelled["shape"], config["shape"])
            config = labelled
            self.assertTrue(config["model"] == "stub/model"
                  and config["index"] == root / ".perspirator" / "neighbours.npz", "config loads model and index path")
            self.assertTrue(config["graph"] == {"provider": "obsidian", "fallback": "filesystem",
                                      "limit": 5, "timeout": 5}, ("config selects bounded graph provider", str(config["graph"])))
            out = config["index"]

            cached = FakePretrained(local="cached", remote=AssertionError("network used"))
            self.assertTrue(nb.load_pretrained(cached, "stub/model", "model") == "cached"
                  and cached.calls == [("stub/model", True)], "complete model cache makes no Hub request")
            uncached = FakePretrained(local=OSError("not cached"), remote="downloaded")
            self.assertTrue(nb.load_pretrained(uncached, "stub/model", "model") == "downloaded"
                  and uncached.calls == [("stub/model", True), ("stub/model", False)], "cache miss falls back to download")
            unavailable = FakePretrained(local=OSError("not cached"),
                                         remote=ConnectionError("offline"))
            try:
                nb.load_pretrained(unavailable, "stub/model", "model")
                self.assertTrue(False, ("unavailable model fails concisely", "no SystemExit"))
            except SystemExit as exc:
                message = str(exc)
                self.assertTrue("local Hugging Face cache" in message
                      and "ConnectionError: offline" in message, ("unavailable model fails concisely", message))

            stub = StubEmbedder()
            first = nb.refresh(root, config, out, embedder=stub)
            base = first["units"]
            self.assertTrue(first["embedded"] == base and base > 0, ("initial build embeds every retrieval unit", str(first)))
            with np.load(out, allow_pickle=False) as data:
                metadata = json.loads(str(data["meta"]))
            problem_units = [item for item in metadata if item["note"] == "a.md"]
            self.assertTrue([item["unit"] for item in problem_units]
                  == ["problem_identity", "conjecture"]
                  and problem_units[1]["embedding_text"].startswith("a\nproblem a?\n***"), ("Problem Note index stores identity plus contextual conjecture", str(problem_units)))
            self.assertTrue(not any(item["note"].startswith(".trash") for item in metadata), "exempt folder excluded")
            loaded = nb.load_index(root, index=out, refresh_index=False)
            self.assertTrue(len(loaded["meta"]) == len(loaded["vectors"]) == base, "shared loader aligns metadata and vectors")
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
            self.assertTrue(race_writer in (1, 2) and race_vectors == 1, "concurrent refresh writes leave one complete readable index")
            corpus = ["the growth of knowledge is unpredictable",
                      "knowledge grows through criticism",
                      "memetic warfare spreads rational memes",
                      "a tradition of criticism corrects errors",
                      "knowledge and criticism and errors and growth"]
            idf = nb.inverse_document_frequency(corpus)
            words = [nb.content_words(text) for text in corpus]
            self.assertTrue(idf["knowledge"] < idf["memetic"] / 2, ("a ubiquitous word carries almost no weight", f"knowledge={idf['knowledge']:.2f} memetic={idf['memetic']:.2f}"))
            self.assertTrue(nb.lexical_coverage(nb.content_words("memetic warfare"),
                                      words[2], idf) == 1.0, "a rare term scores its unit fully")
            self.assertTrue(nb.lexical_coverage(nb.content_words("memetic warfare"),
                                      words[0], idf) == 0.0, "a unit sharing only a common word scores near zero")

            meta = [{"note": f"n{i}.md", "text": text} for i, text in enumerate(corpus)]
            eligible = list(range(len(corpus)))
            cosine = [0.9, 0.8, 0.1, 0.7, 0.6]
            lexical = nb.lexical_scores("memetic warfare", meta, eligible, cosine)
            order, matched = nb.merge_rankers(eligible, meta, cosine, lexical, 4, "both")
            self.assertTrue(2 in order and "lexical" in matched[2], ("a lexical-only hit reaches the list cosine would have buried", f"{order} {matched}"))
            self.assertTrue(order[0] == 0 and "embedding" in matched[0], ("the top cosine hit is still first", str(order)))
            embed_only, _ = nb.merge_rankers(eligible, meta, cosine, lexical, 4, "embedding")
            self.assertTrue(embed_only == nb.collapse_by_note(eligible, meta, cosine, 4), ("--rank embedding reproduces the pure cosine order", str(embed_only)))
            self.assertTrue(all("lexical" not in matched[i] for i in order if lexical[i] == 0), "a zero-lexical unit is never nominated by the lexical ranker")

            def unit(note, kind, heading=()):
                return {"note": note, "unit": kind, "heading": list(heading)}
            moved = np.array([1.0, 0.0], "float32")
            same = np.array([0.0, 1.0], "float32")
            near = np.array([0.06, 0.998], "float32")
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
            self.assertTrue(written == 1 and events[0]["note"] == "a.md"
                  and events[0]["similarity"] == 0.0, ("a rewritten unit is logged with how far it moved", str(events)))
            self.assertTrue(all(e["note"] != "b.md" for e in events), "an unchanged unit is not logged")
            self.assertTrue(all(e["note"] != "c.md" for e in events), "blocks are not tracked, because they reorder")

            vectors["n1"] = near
            drift_log.unlink()
            nb.record_drift(drift_log, old_units, old_ids, vectors, new_units,
                            new_ids, vectors)
            events = [json.loads(line) for line in
                      drift_log.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(len(events) == 1 and 0.9 < events[0]["similarity"] < 1.0, ("a near-identical rewording is still recorded, not thresholded away", str(events)))


            split_old = [unit("d.md", "conjecture"), unit("d.md", "conjecture")]
            split_new = [unit("d.md", "conjecture"), unit("d.md", "conjecture"),
                         unit("d.md", "conjecture")]
            vecs = {"p1": same, "p2": same, "q1": same, "q2": same, "q3": same}
            log2 = root / "drift2.jsonl"
            n = nb.record_drift(log2, split_old, ["p1", "p2"], vecs,
                                split_new, ["q1", "q2", "q3"], vecs)
            self.assertTrue(n == 0, ("re-segmenting an unchanged conjecture is not reported as drift", f"{n} events"))
            self.assertTrue(nb.drift_key(unit("a.md", "conjecture"))
                  != nb.drift_key(unit("a.md", "problem_identity")), "drift identity separates the two sides of one note")

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
            self.assertTrue(len(recurrence) == 1 and recurrence[0]["matched_by"] == ["embedding"], "recurrence reuses the shared substrate")
            self.assertTrue(all("derived" not in hit["where"] for hit in recurrence), "candidate folder exclusions suppress derived report boilerplate")
            (root / "memory" / "Left.md").unlink()
            (root / "memory" / "Right.md").unlink()
            (root / "memory" / "perspirator" / "runs" / "derived.md").unlink()

            unchanged_stub = StubEmbedder()
            unchanged = nb.refresh(root, config, out, embedder=unchanged_stub)
            self.assertTrue(unchanged["embedded"] == 0 and unchanged_stub.calls == [], "unchanged vault does not call the model")

            a.write_text("problem a?\n***\nconjecture a, revised.\n", encoding="utf-8")
            bump(a)
            edited_stub = StubEmbedder()
            edited = nb.refresh(root, config, out, embedder=edited_stub)
            self.assertTrue(edited["notes_changed"] == 1 and edited["embedded"] == 1, ("edit re-embeds only changed contextual conjecture", str(edited)))
            self.assertTrue(edited_stub.calls == ["a\nproblem a?\n***\nconjecture a, revised."], ("embedded text retains the unchanged problem identity", str(edited_stub.calls)))

            write(root, "c.md", "problem c?\n***\nconjecture c.\n")
            added = nb.refresh(root, config, out, embedder=StubEmbedder())
            self.assertTrue(added["units"] == base + 2 and added["embedded"] == 2, ("new Problem Note adds exactly two units", str(added)))
            b.unlink()
            removed = nb.refresh(root, config, out, embedder=StubEmbedder())
            self.assertTrue(removed["notes_removed"] == 1 and removed["units"] == base
                  and removed["embedded"] == 0, ("deleted Problem Note drops both units", str(removed)))

            summary = nb.public_header({"model": "stub/model", "dim": 8, "units": base,
                                        "built": "now", "shape": config["shape"],
                                        "notes": {"a.md": [1, 2]}})
            self.assertTrue("notes" not in summary and summary["shape"]["formation"]
                  == config["formation"], ("public query provenance omits unbounded freshness stamps", str(summary)))
            meta = [{"note": "same.md"}, {"note": "same.md"}, {"note": "other.md"}]
            scores = np.array([0.8, 0.9, 0.7])
            self.assertTrue(nb.collapse_by_note([0, 1, 2], meta, scores, 5) == [1, 2], "ranking collapses multiple unit hits to best note hit")

            self.assertTrue(parsed("No links found.") == []
                  and parsed("No backlinks found.") == [], "Obsidian no-result sentinels become empty collections")
            pair_meta = [
                {'note': 'a.md'}, {'note': 'a.md'},
                {'note': 'b.md'}, {'note': 'c.md'},
            ]
            linked = write(
                root, 'linked.md',
                'problem?\n***\nThe link is on this side: [[b]].\n')
            self.assertTrue(nb.note_link_stems(root, 'linked.md') == {'b'}, 'complete-note link evidence crosses retrieval-unit boundaries')
            linked.unlink()
            pair_vectors = np.array([
                [1.0, 0.0], [0.8, 0.2], [0.95, 0.05], [0.0, 1.0],
            ], dtype='float32')
            pairs = nb.top_note_pairs([0, 1, 2, 3], pair_meta, pair_vectors, 3)
            self.assertTrue(len(pairs) == 3
                  and (pair_meta[pairs[0][1]]['note'],
                       pair_meta[pairs[0][2]]['note']) == ('a.md', 'b.md')
                  and round(pairs[0][0], 2) == 0.95, ('pair ranking collapses units to the best note pair', str(pairs)))

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
            self.assertTrue(context["backlinks"] == ["source.md"]
                  and context["links"] == ["target one.md"]
                  and context["properties"] == {"type": "Problem"}, ("CLI context combines links backlinks and properties", str(context)))
            command_calls = [(argv, kwargs) for argv, kwargs in calls
                             if argv[1:2] != ["vault"]]
            self.assertTrue(calls[0][0] == ["obsidian", "vault", "info=name"], ("CLI probes the open vault name before path commands", str(calls[0])))

            def other_vault_runner(argv, **kwargs):
                if argv[1:] == ["vault", "info=name"]:
                    return Completed("a different vault")
                return Completed("")
            wrong = ObsidianCLI(root, runner=other_vault_runner).note_context("n.md")
            self.assertTrue(wrong["status"] == "wrong-vault", ("CLI refuses context when another vault is open", str(wrong)))
            self.assertTrue(all("path=folder/note.md" in argv for argv, _ in command_calls[:3])
                  and all(kwargs["shell"] is False for _, kwargs in command_calls[:3]), ("CLI targets exact path and never invokes a shell", str(command_calls[:3])))
            cached_call_count = len(calls)
            self.assertTrue(cli.note_context("folder/note.md") == context
                  and len(calls) == cached_call_count, "CLI context cache avoids repeated subprocesses")

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
            self.assertTrue(first_context["status"] == "error"
                  and second_context["status"] == "ok", ("failed path context stays retryable within the session", str(second_context)))
            searched = cli.search("knowledge growth", path="memory")
            self.assertTrue(searched["data"] == ["a.md"]
                  and "limit=1" in calls[-1][0], ("generic CLI adapter exposes bounded search", str(calls[-1])))
            try:
                exact_path("../outside.md")
                self.assertTrue(False, ("exact paths reject traversal", "no ValueError"))
            except ValueError:
                self.assertTrue(True, "exact paths reject traversal")

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
            self.assertTrue(mixed["provider"] == "mixed"
                  and mixed["status"] == "partial-fallback"
                  and [item["provider"] for item in mixed["notes"]]
                  == ["obsidian", "filesystem"]
                  and mixed["notes"][1]["status"] == "fallback", ("one graph failure falls back only for that note", str(mixed)))
            all_failed = nb.expand_graph(root, graph_results, config, "obsidian", {},
                                        cli=ContextCLI({"a.md", "c.md"}))
            self.assertTrue(all_failed["provider"] == "filesystem"
                  and all_failed["status"] == "fallback"
                  and len(all_failed["issues"]) == 2, ("total graph failure remains explicit", str(all_failed)))
            no_fallback = dict(config)
            no_fallback["graph"] = dict(config["graph"], fallback="none")
            refused = nb.expand_graph(root, graph_results, no_fallback, "obsidian", {},
                                      cli=ContextCLI({"c.md"}))
            self.assertTrue(refused["provider"] == "obsidian"
                  and refused["status"] == "unavailable", ("disabled graph fallback preserves the Obsidian error", str(refused)))

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
                           "dim": 2, "built": "2026-10-31 12:00", "shape": config["shape"]},
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
            self.assertTrue(len(batch.batches) == 1 and len(batch.batches[0]) == 2
                  and len(payload["queries"]) == 2
                  and [item["query_note"] for item in payload["queries"]]
                  == ["a.md", "c.md"], ("repeated files share one embedding batch and result envelope", str(payload)))

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
            self.assertTrue(bool(relative_batch.batches)
                  and payload['query_note'] == 'a.md', ('relative query file resolves against the supplied vault', str(payload)))

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
            self.assertTrue(text_batch.batches == [["problem a?", "problem c?"]]
                  and len(payload["queries"]) == 2, ("repeated texts share one embedding batch and result envelope", str(payload)))

            cfg_path = root / "memory" / "perspirator" / "Neighbour Retrieval.md"
            cfg_path.write_text(CONFIG.replace("stub/model", "other/model"), encoding="utf-8")
            try:
                nb.refresh(root, nb.load_config(root), out, embedder=StubEmbedder())
                self.assertTrue(False, ("model change is refused", "no SystemExit"))
            except SystemExit as exc:
                self.assertTrue("comparable" in str(exc), "model change is refused")

            cfg_path.write_text(CONFIG.replace("- max-tokens: 256", "- max-tokens: 128"),
                                encoding="utf-8")
            try:
                nb.refresh(root, nb.load_config(root), out, embedder=StubEmbedder())
                self.assertTrue(False, ("formation-shape change is refused", "no SystemExit"))
            except SystemExit as exc:
                self.assertTrue("retrieval-unit shape" in str(exc), "formation-shape change is refused")

            scoped_text = CONFIG.replace("- corpus: vault, memory", "- corpus: memory")
            cfg_path.write_text(scoped_text, encoding="utf-8")
            scoped_config = nb.load_config(root)
            nb.refresh(root, scoped_config, out, rebuild=True, embedder=StubEmbedder())
            with np.load(out, allow_pickle=False) as data:
                scoped = json.loads(str(data["meta"]))
            self.assertTrue(scoped and all(item["corpus"] == "memory" for item in scoped), "rebuild after shape change re-scopes index")

            cfg_path.write_text(CONFIG.replace("status: active", "status: draft"),
                                encoding="utf-8")
            try:
                nb.load_config(root)
                self.assertTrue(False, ("inactive config is refused", "no SystemExit"))
            except SystemExit as exc:
                self.assertTrue("not status: active" in str(exc), "inactive config is refused")
            cfg_path.unlink()
            try:
                nb.load_config(root)
                self.assertTrue(False, "missing config stops rather than improvising")
            except SystemExit as exc:
                self.assertTrue("STOP" in str(exc) and "improvise" in str(exc), "missing config stops rather than improvising")


class NanosecondRefreshTests(unittest.TestCase):
    def test_lexical_cache_tracks_actual_corpus_owner_and_stays_bounded(self):
        first = [{"text": "alpha"}, {"text": "other"}]
        second = [{"text": "bravo"}, {"text": "different"}]
        with patch.object(nb, "_IDF_CACHE", {}), \
                patch.object(nb, "id", return_value=7, create=True):
            self.assertGreater(nb.lexical_scores("alpha", first, [0, 1], [0, 0])[0], 0)
            self.assertGreater(nb.lexical_scores("bravo", second, [0, 1], [0, 0])[0], 0)
            self.assertEqual(nb.lexical_scores("alpha", second, [0, 1], [0, 0])[0], 0)
            self.assertEqual(len(nb._IDF_CACHE), 1)

    def test_graph_clipping_exposes_omitted_received_items(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(root, "a.md", "[[one]] [[two]] [[three]]")
            refs = {"a": {root / "b.md", root / "c.md", root / "d.md"}}
            context = nb.filesystem_context(root, "a.md", refs, 2)
            self.assertEqual(context["coverage"]["observed"],
                             {"links": 3, "backlinks": 3})
            self.assertEqual(context["coverage"]["omitted"],
                             {"links": 1, "backlinks": 1})

            cli = ObsidianCLI(root, limit=2)
            with patch.object(cli, "probe", return_value={"ok": True}), \
                    patch.object(cli, "path_command", side_effect=[
                        {"ok": True, "data": ["b.md", "c.md", "d.md"]},
                        {"ok": True, "data": ["one", "two", "three"]},
                        {"ok": True, "data": {}},
                    ]):
                native = cli.note_context("a.md")
            self.assertEqual(native["coverage"]["scope"], "received-provider-response")
            self.assertEqual(native["coverage"]["omitted"],
                             {"links": 1, "backlinks": 1})
            unavailable = nb.filesystem_context(root, "missing.md", refs, 2)
            self.assertEqual(unavailable["status"], "unavailable")
            self.assertNotIn("observed", unavailable["coverage"])

    def test_equal_size_edit_in_same_second_replaces_cached_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(root, "memory/perspirator/Neighbour Retrieval.md", CONFIG)
            note = write(root, "a.md", "problem a?\n***\nconjecture a.\n")
            start = 1700000000100000000
            os.utime(note, ns=(start, start))
            config = nb.load_config(root)
            nb.refresh(root, config, config["index"], embedder=StubEmbedder())
            before_size = note.stat().st_size
            note.write_text("problem b?\n***\nconjecture b.\n", encoding="utf-8")
            os.utime(note, ns=(start + 1000000, start + 1000000))
            self.assertEqual(note.stat().st_size, before_size)
            report = nb.refresh(root, config, config["index"], embedder=StubEmbedder())
            self.assertEqual(report["notes_changed"], 1)
            loaded = nb.load_index(root, refresh_index=False)
            text = "\n".join(item["text"] for item in loaded["meta"]
                             if item["note"] == "a.md")
            self.assertIn("problem b?", text)
            self.assertNotIn("problem a?", text)


class MatchFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        write(self.root, "memory/perspirator/Neighbour Retrieval.md", CONFIG)
        write(self.root, "a.md", "problem a?\n***\nconjecture a.\n")
        removed = write(self.root, "b.md", "problem b?\n***\nconjecture b.\n")
        config = nb.load_config(self.root)
        nb.refresh(self.root, config, config["index"], embedder=StubEmbedder())
        self.loaded = nb.load_index(self.root, refresh_index=False)
        self.built = datetime.strptime(self.loaded["header"]["built"], "%Y-%m-%d %H:%M")
        self.now = self.built + timedelta(days=1, hours=23, minutes=59)
        removed.unlink()

    def run_match(self, texts, no_refresh=True, as_json=True):
        args = SimpleNamespace(
            vault=str(self.root), index=None, no_refresh=no_refresh,
            file=None, stdin=False, text=texts,
            corpus="all", side="all", unit="all", folder=None,
            k=100, graph="none", json=as_json, rank="embedding")
        output = io.StringIO()
        now = self.now

        class FixedDateTime(datetime):
            @classmethod
            def now(cls):
                return now

        with patch.object(nb, "load_index", return_value=self.loaded) as load, \
                patch.object(nb, "Embedder", return_value=StubEmbedder()), \
                patch.object(nb, "datetime", FixedDateTime), \
                redirect_stdout(output):
            self.assertEqual(nb.cmd_match(args), 0)
        load.assert_called_once_with(str(self.root), index=None,
                                     refresh_index=not no_refresh)
        return json.loads(output.getvalue()) if as_json else output.getvalue()

    def test_single_query_reports_whole_day_age_and_deleted_nomination(self):
        payload = self.run_match(["problem b?"])
        self.assertIn("b.md", [item["note"] for item in payload["results"]])
        self.assertEqual(payload["header"]["freshness"], {
            "built": self.loaded["header"]["built"], "index_age_days": 1,
            "refreshed": False, "missing_paths": ["b.md"],
        })

    def test_multiple_queries_share_freshness_and_refresh_flag(self):
        payload = self.run_match(["problem a?", "problem b?"], no_refresh=False)
        self.assertEqual(len(payload["queries"]), 2)
        self.assertEqual(payload["header"]["freshness"], {
            "built": self.loaded["header"]["built"], "index_age_days": 1,
            "refreshed": True, "missing_paths": ["b.md"],
        })

    def test_text_output_exposes_freshness_and_missing_paths(self):
        output = self.run_match(["problem b?"], as_json=False)
        self.assertIn("built " + self.loaded["header"]["built"], output)
        self.assertIn("age 1 whole days; refreshed false", output)
        self.assertIn("missing nominated paths:\n  b.md", output)

    def test_freshness_stats_only_unique_returned_paths(self):
        seen = []
        exists = Path.exists

        def observed_exists(path):
            seen.append(path)
            return exists(path)

        matches = [{"results": [{"note": "a.md"}, {"note": "a.md"}]}]
        with patch.object(Path, "exists", observed_exists):
            facts = nb.match_freshness(self.root, self.loaded["header"], matches,
                                       False, now=self.now)
        self.assertEqual(seen, [self.root / "a.md"])
        self.assertEqual(facts["missing_paths"], [])


if __name__ == "__main__":
    unittest.main()
