#!/usr/bin/env python3
"""Bounded, read-only AnkiConnect context for Perspirator mechanisms.

Anki holds facts no other provider has: which cards exist, what a card's
current fields say, and its current scheduling aggregates. Those are evidence about
the vault's external consumer, and they have already refuted vault-side
conjectures — on 2026-08-05 the card fronts were what showed that a
frontmatter block had leaked into flashcards.

This owns no rendering. Rendering a Problem Note into card HTML belongs to one
implementation, Interest's `AnkiSyncService`, reachable from a shell through
`dart run tool/sync_anki_notes.dart`. A second renderer here would be one
current rule asserted in two places, which is how the `<br>` divergence of
2026-08-03 happened.

Read-only by construction: writes are whitelisted out, so a destructive Anki
operation stays an explicit, approved act rather than a side effect of a query.

Examples:
    python anki_query.py decks
    python anki_query.py find "deck:Default"
    python anki_query.py notes 1780572893499 --fields
    python anki_query.py cards 1780572893499
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

DEFAULT_ENDPOINT = "http://127.0.0.1:8765"

READ_ONLY = {
    "version", "deckNames", "deckNamesAndIds", "getNumCardsReviewedToday",
    "findNotes", "findCards", "notesInfo", "cardsInfo", "getTags",
    "modelNames", "modelFieldNames", "getDeckConfig", "cardsToNotes",
    "getNoteTags", "getCollectionStatsHTML",
}

TAG_RE = re.compile(r"<[^>]+>")


def plain(html, limit=90):
    """Card fields are HTML; a bounded text view is what a reader needs."""
    text = re.sub(r"\s+", " ", TAG_RE.sub(" ", html or "")).strip()
    return text[:limit]


def invoke(action, endpoint=DEFAULT_ENDPOINT, timeout=10, **params):
    """One whitelisted read, returning explicit unavailability rather than {}.

    A closed Anki and an empty collection are different answers; conflating
    them is the failure this adapter exists to prevent.
    """
    if action not in READ_ONLY:
        raise ValueError(f"not a read-only AnkiConnect action: {action}")
    payload = json.dumps(
        {"action": action, "version": 6, "params": params}).encode("utf-8")
    request = urllib.request.Request(
        endpoint, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.load(response)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {"ok": False, "status": "unavailable", "action": action,
                "data": None, "error": f"{type(exc).__name__}: {exc}"}
    except json.JSONDecodeError as exc:
        return {"ok": False, "status": "error", "action": action,
                "data": None, "error": f"malformed response: {exc}"}
    if (not isinstance(body, dict) or "result" not in body
            or "error" not in body):
        return {"ok": False, "status": "error", "action": action,
                "data": None, "error": "malformed AnkiConnect response envelope"}
    if body["error"] is not None:
        return {"ok": False, "status": "error", "action": action,
                "data": None, "error": body["error"]}
    data = body["result"]
    if data is None:
        return {"ok": False, "status": "error", "action": action,
                "data": None, "error": "read response has no result"}
    list_actions = {"deckNames", "findNotes", "findCards", "notesInfo",
                    "cardsInfo", "getTags", "modelNames", "modelFieldNames",
                    "cardsToNotes", "getNoteTags"}
    if action in list_actions and not isinstance(data, list):
        return {"ok": False, "status": "error", "action": action,
                "data": None, "error": f"{action} result must be a list"}
    if action == "version" and (not isinstance(data, int) or isinstance(data, bool)):
        return {"ok": False, "status": "error", "action": action,
                "data": None, "error": "version result must be an integer"}
    return {"ok": True, "status": "ok", "action": action,
            "data": data, "error": None}


def reachable(endpoint=DEFAULT_ENDPOINT, timeout=10):
    """Ask Anki its version: state only a running AnkiConnect holds."""
    return invoke("version", endpoint=endpoint, timeout=timeout)


def notes(note_ids, endpoint=DEFAULT_ENDPOINT, timeout=10):
    """Note records, with each id explicitly present or missing."""
    ids = [int(value) for value in note_ids]
    result = invoke("notesInfo", endpoint=endpoint, timeout=timeout, notes=ids)
    if not result["ok"]:
        return result
    records = result["data"]
    def invalid(message):
        return {**result, "ok": False, "status": "error", "data": None,
                "error": message}
    if not isinstance(records, list) or len(records) != len(ids):
        return invalid("notesInfo did not return one record per requested identity")
    found = []
    for wanted, record in zip(ids, records):
        if not isinstance(record, dict):
            return invalid("notesInfo returned an invalid note record")
        if record:
            if record.get("noteId") != wanted:
                return invalid(f"notesInfo returned a different identity for {wanted}")
            fields = record.get("fields")
            if (not isinstance(fields, dict)
                    or any(not isinstance(value, dict)
                           or not isinstance(value.get("value"), str)
                           for value in fields.values())):
                return invalid(f"notesInfo returned invalid fields for {wanted}")
            found.append({
                "note_id": wanted, "present": True,
                "model": record.get("modelName"),
                "tags": record.get("tags") or [],
                "cards": record.get("cards") or [],
                "fields": {name: value.get("value", "")
                           for name, value in fields.items()},
            })
        else:
            found.append({"note_id": wanted, "present": False})
    result["data"] = found
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--json", action="store_true",
                        help="emit the full record instead of a bounded view")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("decks")
    sub.add_parser("reachable")
    find = sub.add_parser("find")
    find.add_argument("query")
    find.add_argument("--limit", type=int, default=25)
    note = sub.add_parser("notes")
    note.add_argument("ids", nargs="+")
    note.add_argument("--fields", action="store_true")
    card = sub.add_parser("cards")
    card.add_argument("ids", nargs="+")
    args = parser.parse_args(argv)

    call = {"endpoint": args.endpoint, "timeout": args.timeout}
    if args.action == "reachable":
        result = reachable(**call)
    elif args.action == "decks":
        result = invoke("deckNames", **call)
    elif args.action == "find":
        result = invoke("findNotes", query=args.query, **call)
        if result["ok"]:
            total = len(result["data"] or [])
            result["data"] = {"total": total,
                              "shown": (result["data"] or [])[:args.limit]}
    elif args.action == "notes":
        result = notes(args.ids, **call)
    else:
        ids = [int(value) for value in args.ids]
        result = invoke("cardsInfo", cards=ids, **call)

    if not result["ok"]:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 2
    if args.json:
        print(json.dumps(result["data"], ensure_ascii=False, indent=1))
        return 0

    data = result["data"]
    if args.action == "notes":
        for record in data:
            if not record["present"]:
                print(f"{record['note_id']}  MISSING")
                continue
            print(f"{record['note_id']}  cards={len(record['cards'])} "
                  f"tags={','.join(record['tags']) or '-'}")
            if args.fields:
                for name, value in record["fields"].items():
                    print(f"    {name}: {plain(value)}")
    elif args.action == "cards":
        for record in data:
            print(f"{record['cardId']}  deck={record['deckName']} "
                  f"reps={record['reps']} lapses={record['lapses']} "
                  f"ivl={record['interval']}")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
