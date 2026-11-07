#!/usr/bin/env python3
"""Read-only AnkiConnect queries and dated AnkiDroid capture context.

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
    python anki_query.py snapshot --file capture.json --vault "<vault>" --query is:due
"""

import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import urllib.error
from urllib.parse import parse_qs, quote, urlsplit
import urllib.request

from adapters import add_vault_argument
from context_query import relative_name
from contracts import emit_json_utf8, validate_provider_result
from note_chunks import frontmatter_fields
from role_harness import exact_path, json_text, reparse, snapshot as file_snapshot

DEFAULT_ENDPOINT = "http://127.0.0.1:8765"

READ_ONLY = {
    "version", "deckNames", "deckNamesAndIds", "getNumCardsReviewedToday",
    "findNotes", "findCards", "notesInfo", "cardsInfo", "getTags",
    "modelNames", "modelFieldNames", "getDeckConfig", "cardsToNotes",
    "getNoteTags", "getCollectionStatsHTML",
}

TAG_RE = re.compile(r"<[^>]+>")
# Interest's leading source header: the current form, marked by its class,
# and the form cards carried before 2026-11-08 until every card is re-synced.
SOURCE_HEADER = re.compile(
    r'\A(?:<div class="interest-source"[^>]*>'
    r'|<div style="text-align:right;font-size:0.75em;margin-bottom:6px;opacity:0.6;">)'
    r'(.*?)</div>', re.DOTALL)
STATE_COLUMNS = ("deck_id", "original_deck_id", "type", "queue", "due",
                 "original_due", "interval", "reps", "lapses")


class SourceAnchors(HTMLParser):
    """Read only Interest's leading source header, never arbitrary body links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs.extend(value for name, value in attrs if name == "href")


def integer(value, minimum=0):
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def capture_time(value):
    if not isinstance(value, str):
        raise ValueError("capture time must be timezone-aware ISO text")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("capture time has no timezone")
    return result


def validate_capture(data):
    """Validate minimum facts without treating present-day fields as exhaustive."""
    validate_provider_result(data)
    if (data["provider"] != "ankidroid" or data["capability"] != "card-state"
            or data["status"] not in {"complete", "partial", "unavailable"}):
        raise ValueError("not a supported AnkiDroid card-state capture")
    queries = data["scope"].get("queries")
    if (not isinstance(queries, list) or not queries
            or any(not isinstance(q, str) or not q.strip() for q in queries)
            or len(queries) != len(set(queries))):
        raise ValueError("capture scope requires distinct native queries")
    started = capture_time(data["freshness"].get("started_at"))
    finished = capture_time(data["freshness"].get("finished_at"))
    offset = data["freshness"].get("timezone_offset_minutes")
    if (finished < started or not isinstance(offset, int) or isinstance(offset, bool)
            or abs(offset) > 1440):
        raise ValueError("capture time interval or timezone offset is invalid")
    observed = set()
    for group in data["records"]:
        if not isinstance(group, dict):
            raise ValueError("capture query observation must be an object")
        query = group.get("query")
        if not isinstance(query, str) or query not in queries or query in observed:
            raise ValueError("capture query observation is absent, duplicate or outside scope")
        observed.add(query)
        status, total = group.get("status"), group.get("total")
        cards, errors = group.get("cards"), group.get("errors")
        if (status not in {"complete", "partial", "unavailable"}
                or not integer(group.get("observed_at_ms"), 1)
                or not isinstance(cards, list) or not isinstance(errors, list)
                or (total is not None and not integer(total))
                or (total is None and status != "unavailable")
                or (total is not None and len(cards) > total)
                or (status == "unavailable" and cards)):
            raise ValueError("invalid native query status, count or observation")
        if not (int(started.timestamp() * 1000) <= group["observed_at_ms"]
                <= int(finished.timestamp() * 1000) + 1):
            raise ValueError("native query time is outside the declared capture interval")
        seen = set()
        for card in cards:
            if (not isinstance(card, dict) or not integer(card.get("card_id"), 1)
                    or not integer(card.get("note_id"), 1)
                    or not integer(card.get("ordinal"))
                    or not isinstance(card.get("state"), dict)
                    or "source_front_html" not in card
                    or not isinstance(card.get("source_front_html"), (str, type(None)))
                    or ("source_error" in card and not isinstance(card["source_error"], str))):
                raise ValueError("invalid native card identity or state")
            if any(not isinstance(card["state"].get(key), int)
                   or isinstance(card["state"][key], bool) for key in STATE_COLUMNS):
                raise ValueError("capture is missing integer native scheduling columns")
            if card["card_id"] in seen:
                raise ValueError("duplicate card identity in one native query")
            seen.add(card["card_id"])
        if status == "complete" and (total != len(cards) or errors
                                      or any(c.get("source_error") for c in cards)):
            raise ValueError("complete native query hides omissions or errors")
    if data["status"] == "complete" and (observed != set(queries) or data["errors"]
            or any(g["status"] != "complete" for g in data["records"])):
        raise ValueError("complete capture hides missing queries or partial observations")
    return data


def bind_source(card, vault, max_bytes):
    """Verify one exact href target and ID, not collection-wide uniqueness or sync."""
    unresolved = {"status": "unresolved", "scope": "exact-source-href-target-only"}
    front = card.get("source_front_html")
    match = SOURCE_HEADER.match(front) if isinstance(front, str) else None
    if match is None:
        return {**unresolved, "reason": card.get("source_error") or "no supported Interest source header"}
    links = SourceAnchors()
    links.feed(match[1])
    if len(links.hrefs) != 1 or not isinstance(links.hrefs[0], str):
        return {**unresolved, "status": "ambiguous", "reason": "source header does not have one exact href"}
    uri = links.hrefs[0]
    try:
        parsed = urlsplit(uri)
        params = parse_qs(parsed.query, strict_parsing=True, errors="strict")
        if (parsed.scheme != "obsidian" or parsed.netloc != "open" or parsed.path
                or parsed.fragment or set(params) != {"vault", "file"}
                or any(len(values) != 1 for values in params.values())
                or params["vault"][0] != vault.name):
            raise ValueError("source href does not name this vault and one exact note")
        # Interest removes the final .md suffix, even for names containing dots.
        relative = relative_name(params["file"][0] + ".md")
        path = exact_path(relative, vault, inside=vault)
        expected = ("obsidian://open?vault=" + quote(vault.name, safe="-_.!~*'()")
                    + "&file=" + quote(path.relative_to(vault).as_posix()[:-3], safe="-_.!~*'()"))
        if uri != expected:
            raise ValueError("source href differs from the current exact renderer identity")
        source = file_snapshot("source-note", path, max_bytes=max_bytes)
        fields = frontmatter_fields(source["text"].removeprefix("\ufeff"))
        if fields.get("anki_note_id") != str(card["note_id"]):
            return {**unresolved, "status": "identity-mismatch", "note": relative,
                    "reason": "current source anki_note_id differs from captured note identity"}
        return {"status": "matched", "scope": unresolved["scope"], "note": relative,
                "path": str(path), "sha256": source["sha256"], "bytes": source["bytes"],
                "locator": uri, "observed_at": datetime.now(timezone.utc).isoformat(),
                "content_sync": "not-established-by-identity-and-current-hash"}
    except (OSError, ValueError, UnicodeError) as exc:
        return {**unresolved, "status": "unavailable", "reason": str(exc)}


def snapshot_context(file, vault, *, query=None, limit=25,
                     max_input_bytes=4 * 1024 * 1024, max_bytes=256 * 1024,
                     include_fronts=False, now=None):
    """Return bounded captured facts and exact current source identities."""
    if not all(integer(value, 1) for value in (limit, max_input_bytes, max_bytes)):
        raise ValueError("capture byte and card limits must be positive integers")
    vault = Path(vault).expanduser().absolute()
    if any(reparse(p) for p in (vault, *vault.parents) if p.exists()):
        raise ValueError("vault crosses a reparse boundary")
    vault = vault.resolve(strict=True)
    if not vault.is_dir():
        raise ValueError("vault must be an existing directory")
    path = exact_path(str(file), Path.cwd())
    item = file_snapshot("anki-capture", path, max_bytes=max_input_bytes)
    data = validate_capture(json_text(item["text"]))
    if query is not None and query not in data["scope"]["queries"]:
        raise ValueError("requested query is not in the captured scope")
    now = datetime.now(timezone.utc) if now is None else now
    finished = capture_time(data["freshness"]["finished_at"])
    age = (now - finished).total_seconds()
    original_status, observations, conflicts, cached, previous = data["status"], [], [], {}, {}
    partial = original_status != "complete"
    for group in data["records"]:
        if query is not None and group["query"] != query:
            continue
        selected = group["cards"][:limit]
        cards = []
        for card in selected:
            identity = (card["note_id"], card.get("source_front_html"), card.get("source_error"))
            if identity not in cached:
                cached[identity] = bind_source(card, vault, max_input_bytes)
            binding = cached[identity]
            partial |= binding["status"] != "matched"
            returned = {**card, "vault_source": binding}
            if not include_fronts:
                returned.pop("source_front_html")
                returned["source_front_html_reference"] = {
                    "query": group["query"], "card_id": card["card_id"],
                    "field": "source_front_html", "artifact": "capture_file"}
            cards.append(returned)
            fingerprint = {k: card.get(k) for k in ("note_id", "ordinal", "state", "source_front_html")}
            old = previous.get(card["card_id"])
            if old and old[1] != fingerprint:
                conflicts.append({"card_id": card["card_id"], "queries": [old[0], group["query"]],
                                  "differing_fields": [k for k in fingerprint if fingerprint[k] != old[1][k]],
                                  "meaning": "separate observations, not an atomic collection snapshot"})
            previous[card["card_id"]] = (group["query"], fingerprint)
        partial |= len(selected) < len(group["cards"])
        observations.append({**group, "cards": cards, "captured_cards": len(group["cards"]),
                             "returned_cards": len(cards), "consumer_truncated": len(cards) < len(group["cards"])})
    result = {**data, "status": ("unavailable" if original_status == "unavailable" else
                                  "partial" if partial or conflicts else "complete"),
              "capture_status": original_status, "records": observations,
              "scope": {**data["scope"], "consumer": {"query": query, "limit_per_query": limit,
                        "vault": str(vault), "source_identity_scope": "exact-source-href-target-only",
                        "global_id_uniqueness": "not-inventoried", "full_source_bodies_returned": False,
                        "native_front_html_returned": include_fronts,
                        "conflict_detection_scope": "returned-card-observations"}},
              "freshness": {**data["freshness"], "inspected_at": now.isoformat(),
                            "capture_age_seconds": age, "live": False, "phone_clock_verified": False},
              "capture_file": {k: item[k] for k in ("path", "sha256", "bytes")},
              "observation_conflicts": conflicts}
    if age < 0:
        result["freshness"]["clock_warning"] = "capture timestamp is ahead of this host clock"
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) + 1 > max_bytes:
        raise ValueError("capture context exceeds max-bytes; select fewer cards or an explicit query")
    return result


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
    capture = sub.add_parser("snapshot", help="read an explicit AnkiDroid export, never the live phone")
    capture.add_argument("--file", required=True)
    add_vault_argument(capture)
    capture.add_argument("--query", help="select one exact native query already present in the capture")
    capture.add_argument("--limit", type=int, default=25, help="maximum returned cards per captured query")
    capture.add_argument("--include-fronts", action="store_true",
                         help="include exact captured native front HTML; default returns its artifact reference")
    capture.add_argument("--max-input-bytes", type=int, default=4 * 1024 * 1024)
    capture.add_argument("--max-bytes", type=int, default=256 * 1024, help="refuse an oversized context, never silently cut facts")
    args = parser.parse_args(argv)

    if args.action == "snapshot":
        try:
            result = snapshot_context(args.file, args.vault, query=args.query, limit=args.limit,
                                      max_input_bytes=args.max_input_bytes, max_bytes=args.max_bytes,
                                      include_fronts=args.include_fronts)
        except (OSError, ValueError, TypeError) as exc:
            emit_json_utf8({"ok": False, "status": "error", "action": "snapshot", "error": str(exc)})
            return 2
        emit_json_utf8(result, indent=None)
        return 2 if result["status"] == "unavailable" else 0

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
