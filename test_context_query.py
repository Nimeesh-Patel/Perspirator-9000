"""Bounded vault nomination without model downloads or a second context store."""

import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import context_query as cq
import neighbour as nb


class ContextQueryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.write("One.md", "---\ntitle: Shared title\ntype: note\nstatus: conjectural\n---\nWhy one?\n***\nAn answer.\n")
        self.write("nested/Two.md", "---\ntitle: Shared title\n---\nWhy two?\n***\nA different answer.\n")

    def write(self, relative, text):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def nominate(self, **kwargs):
        return cq.nominate(self.vault, max_bytes=1000000, **kwargs)

    def test_named_paths_open_only_the_supplied_originals_without_the_model(self):
        with patch.object(nb, "match_batch", side_effect=AssertionError("ranker loaded")), \
                patch.object(cq, "note_observation", wraps=cq.note_observation) as observe:
            result = self.nominate(named=["nested/Two.md", "missing.md"])
        named = result["channels"]["named"]
        self.assertEqual([entry["status"] for entry in named], ["matched", "unavailable"])
        self.assertEqual([item["note"] for item in result["records"]], ["nested/Two.md"])
        self.assertEqual([call.args[1] for call in observe.call_args_list],
                         ["nested/Two.md", "missing.md"])
        self.assertIsNone(result["freshness"]["index"])

    def ambiguous_name(self, name):
        return {"provider": "obsidian", "status": "ambiguous", "requested": name,
                "obsidian_choice": "One.md",
                "candidates": [{"note": "One.md", "matched_by": ["name"], "obsidian_choice": True},
                               {"note": "nested/Two.md", "matched_by": ["alias"],
                                "obsidian_choice": False}]}

    def test_a_bare_name_nominates_every_note_it_can_mean(self):
        import obsidian_cli
        with patch.object(obsidian_cli.ObsidianCLI, "name_candidates",
                          side_effect=self.ambiguous_name), \
                patch.object(nb, "match_batch", side_effect=AssertionError("ranker loaded")):
            result = self.nominate(named=["One"])
        entry = result["channels"]["named"][0]
        self.assertEqual(entry["status"], "ambiguous")
        self.assertEqual(entry["obsidian_choice"], "One.md")
        self.assertEqual(sorted(item["note"] for item in result["records"]),
                         ["One.md", "nested/Two.md"])
        self.assertNotIn("ranking_by_facet", entry)
        self.assertTrue(any("ambiguous name stays unresolved" in limit and "--text" in limit
                            for limit in result["scope"]["unobserved"]))

    def test_the_question_orders_an_ambiguous_name(self):
        import obsidian_cli
        seen = {}

        def batch(vault, queries, **kwargs):
            seen["candidates"] = [query["candidates"] for query in queries]
            return {"header": {"freshness": {}}, "refresh": None, "queries": [{
                "results": [], "graph": {"provider": "none", "status": "disabled", "notes": []},
                "candidate_ranking": [
                    {"note": "nested/Two.md", "score": 0.8, "rank": 1},
                    {"note": "One.md", "score": 0.2, "rank": 2}]}]}

        with patch.object(obsidian_cli.ObsidianCLI, "name_candidates",
                          side_effect=self.ambiguous_name), \
                patch.object(nb, "match_batch", side_effect=batch):
            result = self.nominate(named=["One"], texts=["a different answer"])
        self.assertEqual(seen["candidates"], [["One.md", "nested/Two.md"]])
        ranking = result["channels"]["named"][0]["ranking_by_facet"][0]["ranking"]
        self.assertEqual([item["note"] for item in ranking], ["nested/Two.md", "One.md"])

    def test_an_unavailable_app_keeps_exact_paths_and_reports_the_name(self):
        import obsidian_cli
        down = {"provider": "obsidian", "status": "unavailable", "requested": "One",
                "error": "app closed", "candidates": [], "obsidian_choice": None}
        with patch.object(obsidian_cli.ObsidianCLI, "name_candidates", return_value=down):
            result = self.nominate(named=["One", "nested/Two.md"])
        self.assertEqual([entry["status"] for entry in result["channels"]["named"]],
                         ["unavailable", "matched"])
        self.assertEqual(result["status"], "partial")

    def test_explicit_paths_can_name_ambient_exempt_artifacts(self):
        self.write(".perspirator/Explicit.md", "Preserved exact artifact.\n")
        result = self.nominate(named=[".perspirator/Explicit.md"])
        source = result["records"][0]
        self.assertEqual(source["note"], ".perspirator/Explicit.md")
        self.assertEqual(source["reasons"][0]["ambient_exemptions"],
                         "not-applied-to-explicit-nomination")

    def test_raw_source_hash_and_normalized_problem_preview_are_distinct(self):
        path = self.write("Raw.md", "\ufeff---\r\ntitle: Raw source\r\n---\r\nWhy £?\r\n***\r\nOriginal conjecture.\r\n")
        result = self.nominate(named=["Raw.md"])
        source = result["records"][0]
        self.assertEqual(source["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(source["bytes"], len(path.read_bytes()))
        self.assertEqual(source["preview"]["text"], "Why £?")
        self.assertTrue(source["preview"]["conjecture_omitted"])
        self.assertEqual(source["declared"]["title"], "Raw source")
        self.assertIn("file=Raw.md", source["locator"])
        self.write("Long.md", "x" * 221)
        source = self.nominate(named=["Long.md"])["records"][0]
        self.assertEqual(source["preview"]["characters"], 220)
        self.assertTrue(source["preview"]["truncated"])
        self.assertFalse(source["preview"]["conjecture_omitted"])

    def test_unreadable_utf8_is_partial_and_cannot_become_absence(self):
        (self.vault / "Invalid.md").write_bytes(b"bad \xff bytes")
        result = self.nominate(named=["Invalid.md", "One.md"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["channels"]["named"][0]["status"], "unavailable")
        self.assertIn("not valid UTF-8", result["errors"][0]["error"])
        self.assertEqual([item["note"] for item in result["records"]], ["One.md"])

    def test_parent_traversal_absolute_paths_and_linked_inputs_are_refused(self):
        for value in ("../outside.md", "nested/../../outside.md", str(self.root / "outside.md"), "C:/outside.md"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.nominate(named=[value])
        outside = self.root / "outside.md"
        outside.write_text("Outside original", encoding="utf-8")
        linked = self.vault / "Linked.md"
        try:
            linked.symlink_to(outside)
        except OSError:
            return  # Windows may not expose link creation to this test process.
        result = self.nominate(named=["Linked.md"])
        self.assertEqual(result["status"], "partial")
        self.assertIn("reparse boundary", result["errors"][0]["error"])
        self.assertEqual(result["records"], [])

    def test_changed_original_invalidates_candidate_observation(self):
        original = cq.freshness

        def changed(artifacts):
            self.write("One.md", "A replacement after nomination.\n")
            return original(artifacts)

        with patch.object(cq, "freshness", side_effect=changed):
            result = self.nominate(named=["One.md"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["records"][0]["observation_status"], "changed")

    def test_case_variant_later_snapshot_cannot_certify_retained_earlier_record(self):
        old_hash = hashlib.sha256((self.vault / "One.md").read_bytes()).hexdigest()
        original = cq.note_observation

        def case_variants(vault, value):
            if value == "One.md":
                self.write("One.md", "A newer revision between spellings.\n")
            return original(vault, "One.md")

        with patch.object(cq, "note_observation", side_effect=case_variants):
            result = self.nominate(named=["one.md", "One.md"])
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["sha256"], old_hash)
        self.assertEqual(result["records"][0]["observation_status"], "changed")
        self.assertEqual(result["status"], "partial")

    def test_declared_provenance_and_history_metadata_are_preserved_without_interpretation(self):
        self.write("Historical.md", "---\ntitle: Historical\nfrom: agent\nprovenance: chat-record\nadoption: unadopted\ndate: 2026-08-01\naliases: [Former title]\n---\nAn old interpretation.\n")
        result = self.nominate(named=["Historical.md"])
        source = result["records"][0]
        self.assertEqual(source["declared"], {
            "title": "Historical", "from": "agent", "provenance": "chat-record",
            "adoption": "unadopted", "date": "2026-08-01", "aliases": "[Former title]"})
        self.assertIn("not interpreted", source["metadata_representation"])

    def test_expected_ranked_capability_failure_preserves_named_sources_and_each_failed_facet(self):
        for failure in (SystemExit("STOP: no retrieval index"), ImportError("no optional model"),
                        OSError("index unreadable"), ValueError("invalid retrieval configuration")):
            with self.subTest(failure=type(failure).__name__), \
                    patch.object(nb, "match_batch", side_effect=failure):
                result = self.nominate(named=["One.md"], texts=["first", "second"])
            self.assertEqual(result["status"], "partial")
            self.assertEqual([item["note"] for item in result["records"]], ["One.md"])
            self.assertEqual(result["channels"]["named"][0]["status"], "matched")
            facets = result["channels"]["ranked"]
            self.assertEqual([item["text"] for item in facets], ["first", "second"])
            self.assertTrue(all(item["status"] == "unavailable" for item in facets))
            self.assertTrue(all(item["error"] == str(failure) for item in facets))
            self.assertTrue(all("results" not in item for item in facets))
        with patch.object(nb, "match_batch", side_effect=RuntimeError("implementation defect")):
            with self.assertRaisesRegex(RuntimeError, "implementation defect"):
                self.nominate(named=["One.md"], texts=["first"])

    def test_rejected_original_cannot_survive_in_ranked_preview_or_graph(self):
        hit = {"note": "Linked.md", "snippet": "out-of-scope-body", "rank": 1}
        result = {"results": [hit], "graph": {
            "provider": "filesystem", "status": "ok",
            "notes": [{"path": "Linked.md", "properties": {"data": "out-of-scope-body"}}]}}
        batch = {"header": {"freshness": {}}, "queries": [result], "refresh": None}
        original = cq.note_observation

        def reject_link(vault, value):
            if value == "Linked.md":
                raise ValueError("reparse boundary: Linked.md")
            return original(vault, value)

        with patch.object(nb, "match_batch", return_value=batch), \
                patch.object(cq, "note_observation", side_effect=reject_link):
            manifest = self.nominate(texts=["question"], named=["One.md"])
        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(manifest["channels"]["ranked"][0]["results"], [])
        self.assertEqual(manifest["channels"]["ranked"][0]["graph"]["notes"], [])
        self.assertNotIn(b"out-of-scope-body", cq.canonical(manifest))

    def test_each_text_facet_keeps_evidence_graph_and_extension_fields(self):
        hit = {"note": "One.md", "rank": 1, "matched_by": ["lexical"],
               "score": 0.4, "extension": {"keep": "rank evidence"}}
        result = {"query_note": None, "results": [hit],
                  "graph": {"provider": "filesystem", "status": "fallback",
                            "notes": [], "unobserved": ["native graph"]},
                  "distribution": {"eligible": 3}, "extension": {"keep": "query"}}
        batch = {"header": {"freshness": {"refreshed": False}},
                 "queries": [result, result], "refresh": None}
        with patch.object(nb, "match_batch", return_value=batch) as shared:
            manifest = self.nominate(texts=["first criterion", "second cause"], named=["One.md"],
                                     refresh_index=False, graph="none")
        shared.assert_called_once()
        self.assertEqual([query["text"] for query in shared.call_args.args[1]],
                         ["first criterion", "second cause"])
        self.assertEqual([query["text"] for query in manifest["channels"]["ranked"]],
                         ["first criterion", "second cause"])
        self.assertEqual(manifest["channels"]["ranked"][0]["distribution"], {"eligible": 3})
        self.assertEqual(manifest["channels"]["ranked"][0]["extension"], {"keep": "query"})
        self.assertEqual(len(manifest["records"]), 1)
        self.assertEqual(len(manifest["records"][0]["reasons"]), 3)
        self.assertEqual(manifest["records"][0]["reasons"][1]["evidence"]["extension"], hit["extension"])
        self.assertFalse(shared.call_args.kwargs["refresh_index"])

    def test_byte_limit_refuses_complete_output_and_cli_is_create_only(self):
        with self.assertRaisesRegex(ValueError, "no content was truncated"):
            cq.nominate(self.vault, named=["One.md"], max_bytes=10)
        output = self.root / "manifest.json"
        arguments = ["nominate", "--vault", str(self.vault), "--named", "One.md",
                     "--max-bytes", "1000000", "--out", str(output)]
        self.assertEqual(cq.main(arguments), 0)
        raw = output.read_bytes()
        self.assertLessEqual(len(raw), 1000000)
        self.assertEqual(json.loads(raw)["records"][0]["note"], "One.md")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cq.main(arguments), 1)
        self.assertEqual(output.read_bytes(), raw)
        missing = self.root / "too-small.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cq.main(arguments[:-3] + ["10", "--out", str(missing)]), 1)
        self.assertFalse(missing.exists())

    def test_shared_batch_defers_link_scan_and_reuses_resources(self):
        # The batch API is tested with opaque vectors; no optional numerical or
        # transformer dependency is needed to verify orchestration identity.
        config = {"model": "stub", "formation": {"max-tokens": 256},
                  "graph": {"provider": "obsidian", "timeout": 1, "limit": 3},
                  "exempt": ["omitted"]}
        index_path = self.vault / ".perspirator/neighbours.npz"
        refresh_report = {"chunks": 0, "units": 0, "embedded": 0, "reused": 0,
                          "notes_changed": 0, "notes_removed": 0, "seconds": 0.0,
                          "path": index_path}
        loaded = {"vault": self.vault, "config": config, "stale": refresh_report,
                  "header": {"model": "stub", "dim": 2, "units": 0,
                             "built": "2026-11-05 00:00", "shape": "{}"},
                  "meta": [], "vectors": object()}
        embedding = unittest.mock.Mock(return_value=["vector1", "vector2"])
        with patch.object(nb, "load_index", return_value=loaded) as load, \
                patch.object(nb, "Embedder", return_value=embedding) as embedder, \
                patch.object(nb, "vault_links", return_value=({}, set(), [])) as links, \
                patch.object(nb, "ObsidianCLI") as cli, \
                patch.object(nb, "match_query", return_value={"results": []}) as match:
            result = nb.match_batch(str(self.vault), [{"text": "first"}, {"text": "second"}])
            links.assert_not_called()
            first_refs, second_refs = [call.args[-1] for call in match.call_args_list]
            self.assertIs(first_refs(), second_refs())
            links.assert_called_once_with(self.vault, excludes=["omitted"])
            cli.assert_called_once()
        load.assert_called_once()
        embedder.assert_called_once_with("stub", 256)
        embedding.assert_called_once_with(["first", "second"])
        self.assertIs(match.call_args_list[0].kwargs["cli"], match.call_args_list[1].kwargs["cli"])
        self.assertEqual(len(result["queries"]), 2)
        self.assertEqual(json.loads(cq.canonical(result))["refresh"]["path"], str(index_path))
        self.assertIs(loaded["stale"]["path"], index_path)


if __name__ == "__main__":
    unittest.main()
