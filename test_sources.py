"""Offline contract tests for source adapters and source-to-note staging."""

import tempfile
import unittest
from pathlib import Path

import source_to_notes as stn
import highlights_to_notes as hln
import readera_highlights
import x_posts


SOURCES = [
    {"id": "a", "text": "First exact idea.", "url": "https://example.com/a"},
    {"id": "b", "text": "A conflicting idea.", "url": "https://example.com/b"},
]


def x_fixture(status_id, text, screen_name="Example"):
    return {
        "id_str": status_id,
        "text": text,
        "created_at": "2026-01-01T00:00:00.000Z",
        "entities": {"urls": [{
            "url": "https://t.co/a",
            "expanded_url": "https://example.com/a",
        }]},
        "user": {"name": "Example Person", "screen_name": screen_name},
    }


class SourceAdapterTests(unittest.TestCase):
    def test_x_identity_and_duplicate_handling(self):
        text = """
        https://x.com/alice/status/123?s=20
        https://twitter.com/i/status/456
        https://mobile.x.com/alice/status/123
        https://example.com/alice/status/999
        """
        ids = x_posts.status_ids(text)
        self.assertEqual(ids, ["123", "456", "123"])
        self.assertEqual(
            x_posts.unique_with_duplicates(ids),
            (["123", "456"], ["123"]),
        )

    def test_x_preserves_context_and_only_reports_text_repetition(self):
        post = x_fixture("123", "A &amp; B https://t.co/a", "alice")
        post["parent"] = x_fixture("122", "Parent", "bob")
        post["quoted_tweet"] = x_fixture("121", "Quote", "carol")
        post["mediaDetails"] = [{
            "type": "photo",
            "media_url_https": "https://pbs.example/image.png",
            "ext_alt_text": "diagram",
        }]
        record = x_posts.compact_record("123", post)
        self.assertEqual(record["text"], "A & B https://example.com/a")
        self.assertEqual(record["text_source"], "syndication")
        self.assertEqual(record["parent"]["id"], "122")
        self.assertEqual(record["quoted"]["id"], "121")
        self.assertEqual(record["media"][0]["alt_text"], "diagram")

        note = x_fixture("124", "Truncated text")
        note["note_tweet"] = {"id": "opaque"}
        complete = "Complete long-form Note Tweet text."
        record = x_posts.compact_record(
            "124", note, note_fetcher=lambda status_id: complete)
        self.assertEqual(record["text"], complete)
        self.assertEqual(record["text_source"], "note_tweet_full")

        payload = {
            "code": 200,
            "tweet": {
                "id": "124",
                "text": complete,
                "is_note_tweet": True,
            },
        }
        self.assertEqual(
            x_posts.fetch_note_text(
                "124", requester=lambda url, timeout, retries: payload),
            complete,
        )
        payload["tweet"]["id"] = "mismatch"
        with self.assertRaisesRegex(RuntimeError, "unavailable or mismatched"):
            x_posts.fetch_note_text(
                "124", requester=lambda url, timeout, retries: payload)

        article_post = x_fixture(
            "125", "https://t.co/a", "article_author")
        article_post["entities"]["urls"][0]["expanded_url"] = (
            "https://x.com/i/article/999")
        article_payload = {
            "code": 200,
            "tweet": {
                "id": "125",
                "article": {
                    "id": "999",
                    "title": "An &amp; Article",
                    "content": {"blocks": [
                        {"text": "First paragraph."},
                        {"text": ""},
                        {"text": "Second &amp; final paragraph."},
                    ]},
                },
            },
        }
        article = x_posts.fetch_article(
            "125", "999",
            requester=lambda url, timeout, retries: article_payload)
        self.assertEqual(
            article["text"],
            "An & Article\n\nFirst paragraph.\n\nSecond & final paragraph.",
        )
        article_record = x_posts.compact_record(
            "125", article_post,
            article_fetcher=lambda status_id, article_id: article)
        self.assertEqual(article_record["text_source"], "x_article_full")
        self.assertEqual(article_record["article"]["id"], "999")
        commentary_post = x_fixture(
            "126", "Commentary https://t.co/a", "article_author")
        commentary_post["entities"]["urls"][0]["expanded_url"] = (
            "https://x.com/i/article/999")
        record = x_posts.compact_record(
            "126", commentary_post,
            article_fetcher=lambda status_id, article_id: article)
        self.assertEqual(
            record["text"],
            "Commentary https://x.com/i/article/999\n\n" + article["text"],
        )
        self.assertEqual(article_record["text"], article["text"])

        article_payload["tweet"]["article"]["id"] = "mismatch"
        with self.assertRaisesRegex(RuntimeError, "unavailable or mismatched"):
            x_posts.fetch_article(
                "125", "999",
                requester=lambda url, timeout, retries: article_payload)

        posts = {
            "1": x_fixture("1", "@a Same claim https://t.co/a", "one"),
            "2": x_fixture("2", "Same claim", "two"),
        }
        result = x_posts.build_result(
            ["1", "2"], fetcher=lambda status_id: posts[status_id])
        self.assertEqual([record["id"] for record in result["records"]], ["1", "2"])
        self.assertEqual(result["possible_duplicate_text_groups"], [["1", "2"]])


class SourceToNotesTests(unittest.TestCase):
    def test_plan_requires_complete_unique_assignment(self):
        sources = stn.source_records({"records": SOURCES})
        plan = {"notes": [{
            "title": "One",
            "problem": "What is the problem?",
            "source_ids": ["a"],
        }]}
        with self.assertRaisesRegex(ValueError, "unassigned source ids: b"):
            stn.validate_plan(plan, sources)
        plan["notes"].append({
            "title": "Two",
            "problem": "What conflicts?",
            "source_ids": ["a", "b"],
        })
        with self.assertRaisesRegex(ValueError, "assigned to both"):
            stn.validate_plan(plan, sources)

    def test_rendering_preserves_problem_note_and_source_contracts(self):
        sources = stn.source_records(SOURCES)
        plan = {"notes": [{
            "title": "A live conflict",
            "problem": "How can both claims stand?",
            "up": ["Known parent's criticism"],
            "source_ids": ["a", "b"],
        }]}
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            (vault / "Known parent's criticism.md").write_text("parent", encoding="utf-8")
            notes, unassigned = stn.validate_plan(plan, sources, vault=vault)
            text = stn.render_note(notes[0], sources)
        self.assertEqual(unassigned, [])
        self.assertIn("up:\n- \"[[Known parent's criticism]]\"\ncategory: \"Default\"", text)
        self.assertIn("How can both claims stand?\n\n***\n\n", text)
        self.assertIn("First exact idea.\n\nhttps://example.com/a", text)
        self.assertIn("A conflicting idea.\n\nhttps://example.com/b", text)
        self.assertNotIn("date:", text)

    def test_writer_refuses_to_replace_an_existing_note(self):
        sources = stn.source_records([SOURCES[0]])
        note = {
            "filename": "Existing.md",
            "problem": "What exists?",
            "category": "Default",
            "up": [],
            "source_ids": ["a"],
        }
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Existing.md"
            target.write_text("user content", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                stn.write_notes([note], sources, Path(directory))
            self.assertEqual(target.read_text(encoding="utf-8"), "user content")

    def test_existing_append_requires_explanation_and_exact_problem(self):
        sources = stn.source_records([SOURCES[0]])
        original = (
            b"---\r\nup: null\r\ncategory: Default\r\n---\r\n\r\n"
            b"What exists?\r\n\r\n***\r\n\r\nOriginal conjecture.\r\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            target = vault / "Existing.md"
            target.write_bytes(original)
            plan = {"notes": [{
                "file": "Existing.md",
                "existing": True,
                "problem": "What exists?",
                "source_ids": ["a"],
            }]}
            with self.assertRaisesRegex(ValueError, "same_problem explanation"):
                stn.validate_plan(plan, sources, vault=vault)

            plan["notes"][0]["same_problem"] = "Both address what exists."
            plan["notes"][0]["problem"] = "What existed?"
            with self.assertRaisesRegex(ValueError, "exactly match"):
                stn.validate_plan(plan, sources, vault=vault)

            plan["notes"][0]["problem"] = "What exists?"
            notes, unassigned = stn.validate_plan(plan, sources, vault=vault)
            self.assertEqual(unassigned, [])
            self.assertTrue(notes[0]["existing"])
            self.assertEqual(notes[0]["same_problem"], "Both address what exists.")

    def test_existing_append_stages_then_requires_explicit_live_guard(self):
        sources = stn.source_records([SOURCES[0]])
        original = (
            b"---\r\nup: null\r\ncategory: Default\r\n---\r\n\r\n"
            b"What exists?\r\n\r\n***\r\n\r\nOriginal conjecture.\r\n"
        )
        plan = {"notes": [{
            "file": "Existing.md",
            "existing": True,
            "problem": "What exists?",
            "same_problem": "The source answers the existing question directly.",
            "source_ids": ["a"],
        }]}
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            target = vault / "Existing.md"
            target.write_bytes(original)
            notes, _ = stn.validate_plan(plan, sources, vault=vault)

            stage = vault / "stage"
            staged = stn.write_notes(notes, sources, stage, vault=vault)[0]
            staged_bytes = staged.read_bytes()
            self.assertTrue(staged_bytes.startswith(original))
            self.assertIn(
                b"\r\n---\r\n\r\nFirst exact idea.\r\n\r\nhttps://example.com/a\r\n",
                staged_bytes,
            )
            self.assertEqual(target.read_bytes(), original)

            with self.assertRaisesRegex(ValueError, "--append-existing"):
                stn.write_notes(notes, sources, vault, vault=vault)
            stn.write_notes(
                notes, sources, vault, vault=vault, append_existing=True)
            written = target.read_bytes()
            self.assertTrue(written.startswith(original))
            self.assertEqual(written.count(b"https://example.com/a"), 1)

            with self.assertRaisesRegex(ValueError, "already exists in root note"):
                stn.validate_plan(plan, sources, vault=vault)

    def test_existing_append_refuses_a_stale_target(self):
        sources = stn.source_records([SOURCES[0]])
        plan = {"notes": [{
            "file": "Existing.md",
            "existing": True,
            "problem": "What exists?",
            "same_problem": "The source answers the existing question directly.",
            "source_ids": ["a"],
        }]}
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            target = vault / "Existing.md"
            target.write_text(
                "What exists?\n***\nOriginal conjecture.\n", encoding="utf-8")
            notes, _ = stn.validate_plan(plan, sources, vault=vault)
            target.write_text(
                "What exists?\n***\nRevised conjecture.\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed after validation"):
                stn.write_notes(
                    notes, sources, vault, vault=vault, append_existing=True)


if __name__ == "__main__":
    unittest.main()


def readera_backup(path, docs, meta=None):
    """Write a minimal ReadEra .bak: a zip carrying library.json."""
    import zipfile, json as _json
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("library.json", _json.dumps({"docs": docs}))
        archive.writestr("meta.json", _json.dumps(meta or {"date": 1779491971471}))
    return path


def citation(uri, body, extra=None, note_type=3, page=1, index=0.1, modified=1):
    record = {
        "note_uri": uri, "note_body": body, "note_type": note_type,
        "note_page": page, "note_index": index, "note_mark": 2,
        "note_insert_time": 1736670573885, "note_modified_time": modified,
    }
    if extra is not None:
        record["note_extra"] = extra
    return record


def document(sha1, citations, title="A Book", file_title="", authors="An Author",
             deleted=0):
    return {
        "data": {"doc_sha1": sha1, "doc_title": title,
                 "doc_file_name_title": file_title, "doc_authors": authors,
                 "doc_delete_time": deleted, "doc_format": "EPUB"},
        "citations": citations,
    }


class ReadEraAdapterTests(unittest.TestCase):
    def test_annotation_is_preserved_and_kept_distinguishable(self):
        with tempfile.TemporaryDirectory() as tmp:
            bak = readera_backup(Path(tmp) / "a.bak", [
                document("sha-a", [citation("u1", "The book's sentence.",
                                            "My conjecture about it.")])])
            result = readera_highlights.build_result([bak])
            self.assertEqual(result["highlights"], 1)
            self.assertEqual(result["annotated"], 1)
            record = result["records"][0]
            self.assertEqual(
                record["text"],
                "> The book's sentence.\n\nMy conjecture about it.")
            self.assertEqual(record["quote"], "The book's sentence.")
            self.assertEqual(record["annotation"], "My conjecture about it.")
            self.assertEqual(record["locator"], "readera://sha-a/u1")

    def test_titleless_document_recovers_its_filename_instead_of_being_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            bak = readera_backup(Path(tmp) / "a.bak", [
                document("sha-b", [citation("u2", "Kept.")], title="",
                         file_title="The Evolution of Culture (1)")])
            result = readera_highlights.build_result([bak])
            self.assertEqual(result["highlights"], 1)
            book = result["records"][0]["book"]
            self.assertEqual(book["title"], "The Evolution of Culture (1)")
            self.assertEqual(book["title_source"], "filename")
            self.assertEqual(result["recovered_titles_from_filename"],
                             ["The Evolution of Culture (1)"])

    def test_unrepresentable_citations_are_reported_not_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            bak = readera_backup(Path(tmp) / "a.bak", [
                document("sha-c", [
                    citation("u3", "Fine."),
                    citation("u4", "Unknown kind.", note_type=9),
                    citation("u5", "   "),
                    citation("", "No identity."),
                ])])
            result = readera_highlights.build_result([bak])
            self.assertEqual(result["highlights"], 1)
            self.assertEqual(len(result["errors"]), 3)
            self.assertTrue(any("note_type" in e["error"] for e in result["errors"]))

    def test_snapshots_merge_by_identity_and_newer_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = readera_backup(Path(tmp) / "old.bak", [
                document("sha-d", [citation("u6", "Original.", modified=1)])],
                meta={"date": 1000})
            new = readera_backup(Path(tmp) / "new.bak", [
                document("sha-d", [citation("u6", "Revised.", "Added later.",
                                            modified=2),
                                   citation("u7", "Brand new.")])],
                meta={"date": 2000})
            result = readera_highlights.build_result([old, new])
            self.assertEqual(result["highlights"], 2)
            by_id = {r["id"]: r for r in result["records"]}
            self.assertEqual(by_id["u6"]["quote"], "Revised.")
            self.assertEqual(by_id["u6"]["annotation"], "Added later.")

    def test_deleted_documents_and_broken_archives_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            gone = readera_backup(Path(tmp) / "gone.bak", [
                document("sha-e", [citation("u8", "Should not appear.")],
                         deleted=123)])
            self.assertEqual(readera_highlights.build_result([gone])["highlights"], 0)

            junk = Path(tmp) / "junk.bak"
            junk.write_bytes(b"not a zip")
            self.assertIn("zip", readera_highlights.build_result([junk])["errors"][0]["error"])

            import zipfile as _zip
            wrong = Path(tmp) / "wrong.bak"
            with _zip.ZipFile(wrong, "w") as archive:
                archive.writestr("prefs.xml", "<preferences/>")
            error = readera_highlights.build_result([wrong])["errors"][0]["error"]
            self.assertIn("prefs.xml", error)

    def test_authors_split_without_reordering_names(self):
        self.assertEqual(
            readera_highlights.split_authors("Camiller Patrick, Popper Karl"),
            ["Camiller Patrick", "Popper Karl"])

    def test_locator_generalises_beyond_http_and_must_be_unique(self):
        book = {"id": "u1", "text": "A highlight.",
                "locator": "readera://sha-a/u1"}
        web = {"id": "x1", "text": "A post.", "url": "https://x.com/a/status/1"}
        parsed = stn.source_records([book, web])
        self.assertEqual(parsed["u1"]["locator"], "readera://sha-a/u1")
        self.assertEqual(parsed["x1"]["locator"], "https://x.com/a/status/1")

        with self.assertRaises(ValueError):
            stn.source_records([{"id": "b", "text": "t", "locator": "not-a-uri"}])
        with self.assertRaises(ValueError):
            stn.source_records([book, dict(book, id="u2")])


    def test_deletion_in_readera_is_reported_and_excluded_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = readera_backup(Path(tmp) / "old.bak", [
                document("sha-f", [citation("keep", "Still here."),
                                   citation("gone", "Deleted later.")])],
                meta={"date": 1000})
            new = readera_backup(Path(tmp) / "new.bak", [
                document("sha-f", [citation("keep", "Still here.")])],
                meta={"date": 2000})

            current = readera_highlights.build_result([old, new])
            self.assertEqual(current["highlights"], 1)
            self.assertEqual([w["id"] for w in current["withdrawn"]], ["gone"])

            union = readera_highlights.build_result([old, new],
                                                    include_withdrawn=True)
            self.assertEqual(union["highlights"], 2)

            # Argument order must not decide what is current; the snapshot date does.
            reversed_order = readera_highlights.build_result([new, old])
            self.assertEqual(reversed_order["highlights"], 1)

            # A lone archive has nothing to be withdrawn against.
            self.assertEqual(readera_highlights.build_result([old])["withdrawn"], [])


class HighlightsToNotesTests(unittest.TestCase):
    @staticmethod
    def bundle(records):
        return {"records": records}

    @staticmethod
    def highlight(uid, text, title="A Book", authors=("An Author",), page=1):
        return {"id": uid, "text": text, "locator": f"readera://sha/{uid}",
                "page": page, "created": "2026-01-02T03:04:05+00:00",
                "book": {"title": title, "authors": list(authors),
                         "title_source": "metadata"}}

    def write_bundle(self, directory, records):
        import json as _json
        path = Path(directory) / "b.json"
        path.write_text(_json.dumps(self.bundle(records)), encoding="utf-8")
        return path

    def test_appending_preserves_every_existing_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"; vault.mkdir()
            note = vault / "A Book.md"
            original = ("---\ncollection: Books\n---\n\n# A Book\n\n"
                        "## Highlights (ReadEra)\n\n> Old one.\n\n^reold\nPage 1\n")
            note.write_text(original, encoding="utf-8")
            src = self.write_bundle(tmp, [self.highlight("new1", "> Fresh.")])
            actions, problems = hln.plan(hln.read_bundle(src), vault)
            hln.apply(actions, vault, vault)
            after = note.read_text(encoding="utf-8")
            self.assertEqual(problems, [])
            self.assertTrue(after.startswith(original.rstrip("\n")))
            self.assertIn("^renew1", after)

    def test_a_highlight_already_anchored_is_not_added_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"; vault.mkdir()
            (vault / "A Book.md").write_text(
                "---\ncollection: Books\n---\n\n## Highlights (ReadEra)\n\n"
                "> Kept.\n\n^rehad\nPage 1\n", encoding="utf-8")
            src = self.write_bundle(tmp, [self.highlight("had", "> Kept.")])
            actions, _ = hln.plan(hln.read_bundle(src), vault)
            self.assertEqual(actions[0]["new"], [])
            self.assertEqual(actions[0]["skipped"], 1)
            self.assertEqual(hln.apply(actions, vault, vault), [])

    def test_an_anchor_identifies_the_book_despite_a_truncated_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"; vault.mkdir()
            (vault / "The Fabric of Reality_ The Science of Parallel Univers.md"
             ).write_text("---\ncollection: Books\n---\n\n"
                          "## Highlights (ReadEra)\n\n> One.\n\n^reknown\nPage 1\n",
                          encoding="utf-8")
            src = self.write_bundle(tmp, [
                self.highlight("known", "> One.", title="The Fabric of Reality: "
                               "The Science of Parallel Universes--and Its Implications"),
                self.highlight("fresh", "> Two.", title="The Fabric of Reality: "
                               "The Science of Parallel Universes--and Its Implications")])
            actions, problems = hln.plan(hln.read_bundle(src), vault)
            self.assertEqual(problems, [])
            self.assertEqual(actions[0]["how"], "anchor")
            self.assertEqual(len(actions[0]["new"]), 1)

    def test_create_refuses_to_overwrite_a_note_that_is_not_a_book(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"; vault.mkdir()
            problem_note = vault / "The Enlightenment.md"
            prose = ("---\nup: null\ncategory: Default\nanki_note_id: 17\n---\n\n"
                     "What is the Enlightenment?\n***\n[[tradition of criticism]]\n")
            problem_note.write_text(prose, encoding="utf-8")
            src = self.write_bundle(
                tmp, [self.highlight("x1", "> A quote.", title="The Enlightenment")])
            actions, problems = hln.plan(hln.read_bundle(src), vault)
            self.assertEqual(actions, [])
            self.assertEqual(len(problems), 1)
            self.assertIn("already exists", problems[0])
            hln.apply(actions, vault, vault)
            self.assertEqual(problem_note.read_text(encoding="utf-8"), prose)

    def test_a_new_book_note_is_created_with_wikilinked_authors(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"; vault.mkdir()
            src = self.write_bundle(tmp, [
                self.highlight("n1", "> Quoted.", title="Fresh: A Book",
                               authors=("Ada Lovelace", "Alan Turing"))])
            actions, _ = hln.plan(hln.read_bundle(src), vault)
            hln.apply(actions, vault, vault)
            created = (vault / "Fresh_ A Book.md").read_text(encoding="utf-8")
            self.assertIn("collection: Books", created)
            self.assertIn('- "[[Ada Lovelace]]"', created)
            self.assertIn("*[[Ada Lovelace]], [[Alan Turing]]*", created)
            self.assertIn("^ren1", created)

    def test_blocks_land_inside_the_section_when_others_follow_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"; vault.mkdir()
            (vault / "A Book.md").write_text(
                "---\ncollection: Books\n---\n\n## Highlights (ReadEra)\n\n"
                "> First.\n\n^reone\nPage 1\n\n## My thoughts\n\nMine.\n",
                encoding="utf-8")
            src = self.write_bundle(tmp, [self.highlight("two", "> Second.")])
            actions, _ = hln.plan(hln.read_bundle(src), vault)
            hln.apply(actions, vault, vault)
            after = (vault / "A Book.md").read_text(encoding="utf-8")
            self.assertLess(after.index("^retwo"), after.index("## My thoughts"))
            self.assertIn("Mine.", after)

    def test_an_ambiguous_title_is_refused_rather_than_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"; vault.mkdir()
            for stem in ("The Enlight", "The Enlighten"):
                (vault / f"{stem}.md").write_text(
                    "---\ncollection: Books\n---\n", encoding="utf-8")
            src = self.write_bundle(
                tmp, [self.highlight("a1", "> Q.", title="The Enlightenment Era")])
            actions, problems = hln.plan(hln.read_bundle(src), vault)
            self.assertEqual(actions, [])
            self.assertIn("matches several notes", problems[0])
