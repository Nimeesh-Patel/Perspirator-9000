#!/usr/bin/env python3
"""Render Obsidian Problem Notes and synchronize them through AnkiConnect.

Markdown stays canonical. Existing anki_note_id values are updated in place; a
missing value creates one Basic note and reports the new identity for the
caller to patch back into frontmatter. This tool never edits the vault.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from markdown_it import MarkdownIt
except ImportError:
    MarkdownIt = None

from note_chunks import frontmatter_fields
from problem_half import parse_note

WIKILINK = re.compile(r"(!?)\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
BARE_URL = re.compile(r"(?<![\(<])(https?://[^\s<>]+)")
LINK_HEADER = ('<div style="text-align:right;font-size:0.75em;margin-bottom:6px;'
               'opacity:0.6;"><a href="{uri}">{label} ↗</a></div>')


def obsidian_uri(vault_name, target):
    return "obsidian://open?" + urllib.parse.urlencode(
        {"vault": vault_name, "file": target})


def expand_wikilinks(markdown, vault_name):
    def replace(match):
        _embedded, target, label = match.groups()
        target = target.strip()
        label = (label or target.split("#", 1)[0]).strip()
        return f"[{label}]({obsidian_uri(vault_name, target)})"
    return WIKILINK.sub(replace, markdown)


def render_markdown(markdown, vault_name):
    if MarkdownIt is None:
        raise RuntimeError(
            "anki_sync.py requires markdown-it-py (pip install markdown-it-py)")
    expanded = expand_wikilinks(markdown, vault_name)
    expanded = BARE_URL.sub(r"<\1>", expanded)
    return MarkdownIt("commonmark").render(expanded)


def frontmatter(text):
    return frontmatter_fields(text)


def numeric_note_id(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("anki_note_id must be numeric")
    try:
        note_id = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError("anki_note_id must be numeric") from None
    if note_id <= 0:
        raise ValueError("anki_note_id must be positive")
    return note_id


def deck_candidate(fields):
    category = fields.get("category")
    if isinstance(category, str) and category.strip() not in ("", "Default"):
        return category.strip()
    return "Default"


def note_payload(path, vault, vault_name=None):
    path, vault = Path(path).resolve(), Path(vault).resolve()
    try:
        relative = path.relative_to(vault)
    except ValueError:
        raise ValueError(f"note is outside vault: {path}") from None
    if path.suffix.lower() != ".md" or not path.is_file():
        raise ValueError(f"not a Markdown file: {path}")
    text = path.read_text(encoding="utf-8-sig")
    parsed = parse_note(text)
    if not parsed["has_separator"] or not parsed["problem"]:
        raise ValueError(f"not a non-empty Problem Note: {relative.as_posix()}")
    fields = frontmatter(text)
    vault_name = vault_name or vault.name
    target = relative.with_suffix("").as_posix()
    header = LINK_HEADER.format(
        uri=obsidian_uri(vault_name, target), label=relative.stem)
    return {
        "path": relative.as_posix(),
        "anki_note_id": numeric_note_id(fields.get("anki_note_id")),
        "deck_candidate": deck_candidate(fields),
        "model": "Basic",
        "fields": {
            "Front": header + render_markdown(parsed["problem"], vault_name),
            "Back": render_markdown(parsed["conjecture"] or "", vault_name),
        },
    }


def http_invoke(endpoint, action, params=None):
    body = json.dumps({"action": action, "version": 6,
                       "params": params or {}}).encode("utf-8")
    request = urllib.request.Request(
        endpoint, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"AnkiConnect unavailable at {endpoint}: {exc}") from None
    if result.get("error"):
        raise RuntimeError(f"AnkiConnect {action} failed: {result['error']}")
    return result.get("result")


def synchronize(payloads, invoke, apply=False):
    decks = set(invoke("deckNames") or []) if apply else set()
    actions = []
    for payload in payloads:
        note_id = payload["anki_note_id"]
        if not apply:
            deck = payload["deck_candidate"]
        else:
            deck = payload["deck_candidate"] if payload["deck_candidate"] in decks else "Default"
        if note_id is not None:
            if apply:
                if not invoke("notesInfo", {"notes": [note_id]}):
                    raise RuntimeError(
                        f"existing anki_note_id does not resolve: {payload['path']} -> {note_id}")
                invoke("updateNoteFields", {"note": {
                    "id": note_id, "fields": payload["fields"]}})
            action = "update"
        else:
            if apply:
                note_id = invoke("addNote", {"note": {
                    "deckName": deck, "modelName": payload["model"],
                    "fields": payload["fields"],
                    "options": {"allowDuplicate": False}, "tags": []}})
                if not note_id:
                    raise RuntimeError(
                        f"AnkiConnect returned no identity for {payload['path']}")
            action = "create"
        actions.append({"path": payload["path"], "action": action,
                        "anki_note_id": note_id, "deck": deck})
    return actions


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True)
    parser.add_argument("--file", action="append", required=True)
    parser.add_argument("--vault-name")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8765")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    vault = Path(args.vault).expanduser().resolve()
    try:
        payloads = [note_payload(vault / item, vault, args.vault_name)
                    for item in args.file]
        invoke = lambda action, params=None: http_invoke(
            args.endpoint, action, params)
        actions = synchronize(payloads, invoke, apply=args.apply)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"applied": args.apply, "actions": actions},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
