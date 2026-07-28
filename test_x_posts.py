import tempfile
import unittest
from pathlib import Path

import x_posts


def fixture(status_id, text, screen_name="Example"):
    return {
        "id_str": status_id,
        "text": text,
        "created_at": "2026-01-01T00:00:00.000Z",
        "entities": {
            "urls": [{
                "url": "https://t.co/a",
                "expanded_url": "https://example.com/a",
            }],
        },
        "user": {"name": "Example Person", "screen_name": screen_name},
    }


class XPostsTests(unittest.TestCase):
    def test_extracts_supported_hosts_and_deduplicates_ids(self):
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

    def test_stable_syndication_token(self):
        self.assertEqual(
            x_posts.syndication_token("2080638479411695634"),
            "51kio2isxqoew",
        )

    def test_compacts_context_decodes_html_and_expands_urls(self):
        post = fixture("123", "A &amp; B https://t.co/a", "alice")
        post["parent"] = fixture("122", "Parent", "bob")
        post["quoted_tweet"] = fixture("121", "Quote", "carol")
        post["mediaDetails"] = [{
            "type": "photo",
            "media_url_https": "https://pbs.example/image.png",
            "ext_alt_text": "diagram",
        }]
        record = x_posts.compact_record("123", post)
        self.assertEqual(record["text"], "A & B https://example.com/a")
        self.assertEqual(record["url"], "https://x.com/alice/status/123")
        self.assertEqual(record["parent"]["id"], "122")
        self.assertEqual(record["quoted"]["id"], "121")
        self.assertEqual(record["media"][0]["alt_text"], "diagram")

    def test_reports_possible_duplicate_text_without_collapsing_records(self):
        posts = {
            "1": fixture("1", "@a Same claim https://t.co/a", "one"),
            "2": fixture("2", "Same claim", "two"),
        }
        result = x_posts.build_result(
            ["1", "2"], fetcher=lambda status_id: posts[status_id])
        self.assertEqual(
            [record["id"] for record in result["records"]],
            ["1", "2"],
        )
        self.assertEqual(result["possible_duplicate_text_groups"], [["1", "2"]])

    def test_collects_file_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "links.txt"
            source.write_text(
                "https://x.com/alice/status/123\n", encoding="utf-8")
            self.assertEqual(
                x_posts.collect_inputs([], [str(source)], False),
                ["123"],
            )


if __name__ == "__main__":
    unittest.main()
