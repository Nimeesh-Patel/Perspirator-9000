#!/usr/bin/env python3
"""Fetch and structurally deduplicate public X/Twitter status URLs.

This tool establishes source facts only. It canonicalises status IDs, fetches
public syndication data, preserves reply/quote/media context, and reports
possible repeated text. It does not decide which posts share a problem or
write Problem Notes.

Examples:
    python x_posts.py URL [URL ...]
    python x_posts.py --file links.txt --out posts.json
    Get-Clipboard | python x_posts.py --stdin --out posts.json
"""

import argparse
import html
import json
import math
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

STATUS_RE = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/"
    r"(?:[A-Za-z0-9_]+|i)/status/(\d+)",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://\S+")
LEADING_MENTIONS_RE = re.compile(r"^(?:@\w+\s+)+")
SYNDICATION_ENDPOINT = "https://cdn.syndication.twimg.com/tweet-result"


def status_ids(text):
    """Return status IDs in first-seen order, including duplicates."""
    return [match.group(1) for match in STATUS_RE.finditer(text)]


def collect_inputs(positional, files, use_stdin):
    """Return every status ID occurrence from all requested inputs."""
    chunks = list(positional)
    for filename in files:
        chunks.append(Path(filename).read_text(encoding="utf-8-sig"))
    if use_stdin:
        chunks.append(sys.stdin.read())
    return status_ids("\n".join(chunks))


def unique_with_duplicates(values):
    """Preserve first-seen order and report later occurrences."""
    seen = set()
    unique = []
    duplicates = []
    for value in values:
        if value in seen:
            duplicates.append(value)
        else:
            seen.add(value)
            unique.append(value)
    return unique, duplicates


def syndication_token(status_id):
    """Deterministic non-empty token accepted by X's public syndication endpoint."""
    value = (int(status_id) / 1e15) * math.pi
    integer = int(value)
    fraction = value - integer
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"

    if integer == 0:
        integer_text = "0"
    else:
        digits = []
        while integer:
            integer, remainder = divmod(integer, 36)
            digits.append(alphabet[remainder])
        integer_text = "".join(reversed(digits))

    fraction_digits = []
    for _ in range(12):
        fraction *= 36
        digit = int(fraction)
        fraction_digits.append(alphabet[digit])
        fraction -= digit
    base36 = f"{integer_text}.{''.join(fraction_digits)}"
    return re.sub(r"(0+|\.)", "", base36)


def fetch_json(status_id, timeout=20, retries=3):
    params = urlencode({
        "id": status_id,
        "lang": "en",
        "token": syndication_token(status_id),
    })
    request = Request(
        f"{SYNDICATION_ENDPOINT}?{params}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.5 * attempt)
    raise RuntimeError(str(last_error))


def expanded_text(post):
    text = html.unescape(post.get("text", ""))
    for entity in post.get("entities", {}).get("urls", []):
        short = entity.get("url")
        expanded = entity.get("expanded_url")
        if short and expanded:
            text = text.replace(short, expanded)
    return text


def compact_post(post):
    if not post:
        return None
    user = post.get("user") or {}
    screen_name = user.get("screen_name")
    status_id = post.get("id_str")
    canonical_url = (
        f"https://x.com/{screen_name}/status/{status_id}"
        if screen_name and status_id
        else None
    )
    return {
        "id": status_id,
        "url": canonical_url,
        "author": {
            "name": user.get("name"),
            "screen_name": screen_name,
        },
        "created_at": post.get("created_at"),
        "text": expanded_text(post),
    }


def compact_record(requested_id, post):
    primary = compact_post(post)
    if not primary or primary["id"] != requested_id or not primary["text"]:
        raise ValueError("empty, mismatched, or unavailable post response")
    primary.update({
        "parent": compact_post(post.get("parent")),
        "quoted": compact_post(post.get("quoted_tweet")),
        "media": [
            {
                "type": item.get("type"),
                "url": item.get("media_url_https"),
                "alt_text": item.get("ext_alt_text"),
            }
            for item in post.get("mediaDetails", [])
        ],
    })
    return primary


def normalized_text(text):
    text = LEADING_MENTIONS_RE.sub("", text)
    text = URL_RE.sub("", text)
    return " ".join(text.casefold().split())


def repeated_text_groups(records):
    """Return possible duplicate-content groups without collapsing them."""
    groups = {}
    for record in records:
        key = normalized_text(record["text"])
        groups.setdefault(key, []).append(record["id"])
    return [ids for key, ids in groups.items() if key and len(ids) > 1]


def build_result(input_ids, fetcher=fetch_json):
    unique_ids, duplicate_ids = unique_with_duplicates(input_ids)
    records = []
    errors = []
    for status_id in unique_ids:
        try:
            records.append(compact_record(status_id, fetcher(status_id)))
        except Exception as exc:  # one unavailable post must not hide the rest
            errors.append({"id": status_id, "error": str(exc)})
    return {
        "input_occurrences": len(input_ids),
        "unique_status_ids": len(unique_ids),
        "duplicate_status_ids": duplicate_ids,
        "possible_duplicate_text_groups": repeated_text_groups(records),
        "records": records,
        "errors": errors,
    }


def arguments():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("urls", nargs="*", help="X/Twitter status URLs")
    parser.add_argument("--file", action="append", default=[],
                        help="UTF-8 text file containing status URLs; repeatable")
    parser.add_argument("--stdin", action="store_true",
                        help="also read status URLs from standard input")
    parser.add_argument("--out", help="write JSON here instead of stdout")
    parser.add_argument("--indent", type=int, default=2,
                        help="JSON indentation (default: 2)")
    return parser.parse_args()


def main():
    args = arguments()
    ids = collect_inputs(args.urls, args.file, args.stdin)
    if not ids:
        raise SystemExit("error: no X/Twitter status URLs found")
    result = build_result(ids)
    payload = json.dumps(result, ensure_ascii=False, indent=args.indent) + "\n"
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8", newline="\n")
        print(
            f"wrote {len(result['records'])} posts "
            f"({len(result['errors'])} errors) to {args.out}",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(payload)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
