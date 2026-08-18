#!/usr/bin/env python3
"""Read-only Calibre library evidence through a loopback Content Server.

The Calibre GUI owns the live library. This adapter never opens ``metadata.db``
and never invokes a mutating ``calibredb`` command. It uses only the documented
``calibredb list --for-machine`` surface against a Content Server whose address
is a literal loopback IP.

The Content Server must be configured separately in Calibre to listen only on
127.0.0.1 (or ::1), with local write disabled. Authentication is optional for
this same-computer read path. If it is configured, both the username and a
password file are required so the secret does not appear in the process
command line.

Examples:
    python calibre_query.py --library-id Calibre_Library status
    python calibre_query.py --library-id Calibre_Library list
    python calibre_query.py --library-id Calibre_Library \
      --username perspirator --password-file C:/private/calibre-password.txt \
      search 'title:"the selfish gene"'

Redirect a successful JSON result to a file and pass it later with
``--fallback`` if stale evidence is preferable to no evidence. The fallback is
validated against the exact operation, server, library, query, and limit.
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from contracts import provider_result, validate_provider_result


PROVIDER = "calibre"
DEFAULT_SERVER = "http://127.0.0.1:8081"
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
FIELDS = (
    "title", "authors", "author_sort", "comments", "formats", "identifiers",
    "languages", "last_modified", "pubdate", "publisher", "rating", "series",
    "series_index", "tags", "timestamp", "uuid",
)
SORT_FIELDS = {
    "id", "author_sort", "authors", "formats", "last_modified", "pubdate",
    "publisher", "rating", "series", "series_index", "size", "tags",
    "timestamp", "title", "uuid",
}
LIBRARY_ID_RE = re.compile(r"^[A-Za-z0-9._~-]+$")
LOOPBACKS = {"127.0.0.1", "::1"}


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        if data.strip():
            self.parts.append(data.strip())


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def plain_text(value):
    if not isinstance(value, str):
        return value
    parser = _PlainText()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return " ".join(html.unescape(value).split())
    return " ".join(" ".join(parser.parts).split())


def validate_server(server):
    """Return a normalized URL or refuse any non-loopback target."""
    try:
        parsed = urlsplit(server)
        port = parsed.port  # force validation of malformed ports
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid Content Server URL: {exc}") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Content Server URL must use http or https")
    if parsed.hostname not in LOOPBACKS:
        raise ValueError(
            "Content Server must use the literal loopback address "
            "127.0.0.1 or ::1; hostnames and LAN/public addresses are refused")
    if parsed.username or parsed.password:
        raise ValueError("put credentials in --username/--password-file, not the URL")
    if parsed.query or parsed.fragment:
        raise ValueError("Content Server URL must not contain a query or fragment")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    netloc = f"{host}:{port}" if port is not None else host
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, netloc, path, "", ""))


def validate_library_id(library_id):
    if not isinstance(library_id, str) or not LIBRARY_ID_RE.fullmatch(library_id):
        raise ValueError(
            "library id must be the exact URL-safe Content Server library id")
    return library_id


def validate_auth(username, password_file):
    """Accept no auth or one complete pair; never substitute local-write."""
    username = username.strip() if isinstance(username, str) else None
    username = username or None
    password_file = password_file or None
    if bool(username) != bool(password_file):
        raise ValueError(
            "Content Server username and password file must be supplied together")
    if not username:
        return None, None
    path = Path(password_file).expanduser().resolve(strict=False)
    if not path.is_file():
        raise ValueError(f"password file is unavailable: {path}")
    return username.strip(), path


def library_url(server, library_id):
    return f"{server}/#{library_id}"


def scope_for(operation, server, library_id, *, query=None, limit=None,
              sort_by=None, ascending=False, authentication="not established"):
    return {
        "operation": operation,
        "server": server,
        "network_scope": "literal loopback client target",
        "server_bind_verified": False,
        "library_id": library_id,
        "query": query,
        "limit": limit,
        "sort_by": sort_by,
        "ascending": bool(ascending),
        "transport": "calibredb via Content Server",
        "authentication": authentication,
        "read_only_commands": ["list"],
        "local_write_used": False,
    }


def capability_for(operation):
    return {
        "status": "probe library readability",
        "list": "list books",
        "search": "search book metadata",
    }[operation]


def error_result(operation, status, scope, observed_at, message):
    return provider_result(
        PROVIDER, capability_for(operation), status,
        scope=scope,
        freshness={
            "observed_at": observed_at,
            "basis": "attempted live Content Server query",
        },
        records=[], errors=[{"error": message}],
        status_explanation=message,
    )


def command_for(executable, server, library_id, username, password_file, *,
                query=None, limit=DEFAULT_LIMIT, sort_by="last_modified",
                ascending=False, timeout=20):
    """Build the entire command from one read-only allowlist."""
    if sort_by not in SORT_FIELDS:
        raise ValueError(f"unsupported Calibre sort field: {sort_by}")
    # Callers are bounded to MAX_LIMIT. The provider itself requests one extra
    # row so it can prove whether that bounded view was truncated.
    if not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT + 1:
        raise ValueError(
            f"internal request limit must be between 1 and {MAX_LIMIT + 1}")
    command = [
        executable, "list",
        "--with-library", library_url(server, library_id),
    ]
    if username is not None:
        command.extend([
            "--username", username,
            "--password", f"<f:{password_file.as_posix()}>",
        ])
    command.extend([
        "--timeout", str(timeout),
        "--for-machine",
        "--fields", ",".join(FIELDS),
        "--sort-by", sort_by,
        "--limit", str(limit),
    ])
    if ascending:
        command.append("--ascending")
    if query is not None:
        command.extend(["--search", query])
    return command


def invoke(command, timeout, runner=subprocess.run):
    """Run one allowlisted read and preserve explicit failure uncertainty."""
    try:
        completed = runner(
            command, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        return {"ok": False, "status": "unavailable",
                "error": f"calibredb executable unavailable: {exc}"}
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "status": "indeterminate",
                "error": f"Content Server query timed out after {timeout}s: {exc}"}
    except OSError as exc:
        return {"ok": False, "status": "unavailable",
                "error": f"calibredb could not start: {type(exc).__name__}: {exc}"}

    if completed.returncode != 0:
        diagnostic = completed.stderr or completed.stdout
        if isinstance(diagnostic, bytes):
            # Diagnostics are not machine JSON. Preserve undecodable bytes as
            # visible escape sequences rather than fabricating U+FFFD.
            diagnostic = diagnostic.decode("utf-8", errors="backslashreplace")
        detail = (diagnostic or "no diagnostic").strip()
        return {"ok": False, "status": "unavailable",
                "error": f"calibredb exited {completed.returncode}: {detail[:2000]}"}
    try:
        raw = completed.stdout
        if isinstance(raw, bytes):
            # Calibre 7.22's --for-machine implementation writes json.dumps()
            # bytes encoded as UTF-8. Decode strictly: another code page is a
            # provider-contract failure, not text to guess or replace.
            raw = raw.decode("utf-8-sig", errors="strict")
        payload = json.loads(raw)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "status": "indeterminate",
                "error": f"successful calibredb call returned invalid UTF-8 JSON: {exc}"}
    if not isinstance(payload, list):
        return {"ok": False, "status": "indeterminate",
                "error": "successful calibredb call returned non-list JSON"}
    return {"ok": True, "status": "complete", "rows": payload}


def _as_text(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item is not None)
    if isinstance(value, dict):
        return ", ".join(f"{key}: {item}" for key, item in value.items())
    return str(value)


def book_record(row, server, library_id, observed_at):
    if not isinstance(row, dict):
        raise ValueError("book row is not an object")
    raw_id = row.get("id")
    try:
        book_id = int(raw_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"book row has invalid id: {raw_id!r}") from exc
    if book_id < 0:
        raise ValueError(f"book row has invalid id: {raw_id!r}")
    title = row.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"book {book_id} has no title")

    parts = [title.strip()]
    for label, field in (("Authors", "authors"), ("Series", "series"),
                         ("Tags", "tags"), ("Publisher", "publisher")):
        value = _as_text(row.get(field)).strip()
        if value:
            parts.append(f"{label}: {value}")
    comments = plain_text(row.get("comments"))
    if comments:
        parts.append(comments)

    return {
        "id": f"calibre:{library_id}:{book_id}",
        "provider_identity": {
            "library_id": library_id,
            "book_id": book_id,
            "uuid": row.get("uuid"),
        },
        "locator": f"calibre://show-book/{library_id}/{book_id}",
        "text": "\n".join(parts),
        "title": title.strip(),
        "authors": row.get("authors"),
        "formats": row.get("formats"),
        "tags": row.get("tags"),
        "last_modified": row.get("last_modified"),
        "metadata": dict(row),
        "provenance": {
            "provider": PROVIDER,
            "representation": "calibredb list --for-machine",
            "server": server,
            "network_scope": "loopback",
            "library_id": library_id,
            "observed_at": observed_at,
        },
    }


def _max_last_modified(records):
    values = [record.get("last_modified") for record in records
              if isinstance(record.get("last_modified"), str)]
    return max(values) if values else None


def live_result(operation, server, library_id, username, password_file, *,
                executable="calibredb", query=None, limit=DEFAULT_LIMIT,
                sort_by="last_modified", ascending=False, timeout=20,
                runner=subprocess.run, now=utc_now):
    observed_at = now()
    scope = scope_for(
        operation, server, library_id, query=query, limit=limit,
        sort_by=sort_by, ascending=ascending,
        authentication="username/password" if username else "none")
    # Fetch one sentinel beyond the caller's bound so truncation is explicit.
    request_limit = 1 if operation == "status" else limit + 1
    try:
        command = command_for(
            executable, server, library_id, username, password_file,
            query=query, limit=request_limit, sort_by=sort_by,
            ascending=ascending, timeout=timeout)
    except ValueError as exc:
        return error_result(operation, "unavailable", scope, observed_at, str(exc))

    transport = invoke(command, timeout, runner=runner)
    if not transport["ok"]:
        return error_result(
            operation, transport["status"], scope, observed_at,
            transport["error"])

    if operation == "status":
        scope.update({"readable": True, "sampled_rows": len(transport["rows"])})
        return provider_result(
            PROVIDER, capability_for(operation), "complete", scope=scope,
            freshness={"observed_at": observed_at,
                       "basis": "successful live Content Server query"},
            records=[], errors=[])

    rows = transport["rows"]
    truncated = len(rows) > limit
    rows = rows[:limit]
    records = []
    errors = []
    seen = set()
    for index, row in enumerate(rows):
        try:
            record = book_record(row, server, library_id, observed_at)
            if record["id"] in seen:
                raise ValueError(f"duplicate book identity: {record['id']}")
            seen.add(record["id"])
            records.append(record)
        except ValueError as exc:
            errors.append({"row": index, "error": str(exc)})

    scope.update({
        "returned": len(records),
        "raw_rows": len(rows),
        "truncated": truncated,
    })
    if errors:
        status = "partial" if records else "indeterminate"
    elif truncated:
        status = "partial"
    else:
        status = "complete"
    explanation = None
    if errors:
        explanation = "one or more live book rows could not be represented"
    elif truncated:
        explanation = f"more than {limit} books matched the bounded query"
    return provider_result(
        PROVIDER, capability_for(operation), status, scope=scope,
        freshness={
            "observed_at": observed_at,
            "basis": "successful live Content Server query",
            "source_last_modified_max": _max_last_modified(records),
        },
        records=records, errors=errors, status_explanation=explanation)


def stale_fallback(path, live, now=utc_now):
    """Use only an exact prior result, preserving why the live call failed."""
    if not path or live["status"] in {"complete", "partial"}:
        return live
    try:
        prior = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        validate_provider_result(prior)
        keys = ("operation", "server", "library_id", "query", "limit",
                "sort_by", "ascending", "authentication")
        if prior["provider"] != PROVIDER:
            raise ValueError("fallback comes from a different provider")
        if prior["status"] not in {"complete", "partial"}:
            raise ValueError(
                "fallback is not a prior complete or partial observation")
        if prior["capability"] != live["capability"]:
            raise ValueError("fallback comes from a different capability")
        mismatched = [key for key in keys
                      if prior["scope"].get(key) != live["scope"].get(key)]
        if mismatched:
            raise ValueError(
                "fallback scope differs for: " + ", ".join(mismatched))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        live["errors"].append({"fallback_error": str(exc)})
        return live

    freshness = dict(prior["freshness"])
    freshness.update({
        "basis": "validated prior provider result; live query failed",
        "stale_as_of": now(),
    })
    errors = list(prior["errors"]) + [
        {"live_error": item.get("error", str(item))} for item in live["errors"]]
    return provider_result(
        PROVIDER, live["capability"], "stale",
        scope=dict(prior["scope"]), freshness=freshness,
        records=prior["records"], errors=errors,
        status_explanation="live Content Server query failed; exact prior result used")


def query(operation, *, server=DEFAULT_SERVER, library_id, username=None,
          password_file=None, executable="calibredb", query_text=None,
          limit=DEFAULT_LIMIT, sort_by="last_modified", ascending=False,
          timeout=20, fallback=None, runner=subprocess.run, now=utc_now):
    try:
        server = validate_server(server)
        library_id = validate_library_id(library_id)
        username, password_file = validate_auth(username, password_file)
        if not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
        if operation == "search" and (not isinstance(query_text, str)
                                       or not query_text.strip()):
            raise ValueError("search query must be non-empty")
    except ValueError as exc:
        raw_server = server if isinstance(server, str) else str(server)
        authentication = (
            "username/password" if username and password_file
            else "none" if not username and not password_file
            else "incomplete configuration")
        scope = scope_for(
            operation, raw_server, library_id, query=query_text, limit=limit,
            sort_by=sort_by, ascending=ascending,
            authentication=authentication)
        return error_result(operation, "unavailable", scope, now(), str(exc))

    live = live_result(
        operation, server, library_id, username, password_file,
        executable=executable,
        query=query_text if operation == "search" else None,
        limit=limit, sort_by=sort_by, ascending=ascending, timeout=timeout,
        runner=runner, now=now)
    return stale_fallback(fallback, live, now=now)


def arguments(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--server", default=os.environ.get("CALIBRE_CONTENT_SERVER", DEFAULT_SERVER),
        help="loopback Content Server base URL (default: %(default)s)")
    parser.add_argument(
        "--library-id", default=os.environ.get("CALIBRE_CONTENT_LIBRARY_ID"),
        required=not bool(os.environ.get("CALIBRE_CONTENT_LIBRARY_ID")),
        help="exact Content Server library id")
    parser.add_argument(
        "--username", default=os.environ.get("CALIBRE_CONTENT_USERNAME"),
        help="optional Content Server username (requires --password-file)")
    parser.add_argument(
        "--password-file", default=os.environ.get("CALIBRE_CONTENT_PASSWORD_FILE"),
        help="optional password file (requires --username)")
    parser.add_argument("--executable", default="calibredb")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument(
        "--fallback", help="exact prior JSON result to use with stale status")
    parser.add_argument("--indent", type=int, default=2)

    sub = parser.add_subparsers(dest="operation", required=True)
    sub.add_parser("status", help="prove that the configured library is readable")
    listing = sub.add_parser("list", help="list a bounded set of books")
    searching = sub.add_parser("search", help="run a Calibre metadata search")
    searching.add_argument("query")
    for command in (listing, searching):
        command.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
        command.add_argument("--sort-by", choices=sorted(SORT_FIELDS),
                             default="last_modified")
        command.add_argument("--ascending", action="store_true")
    return parser.parse_args(argv)


def emit_json_utf8(value, *, indent=2, stream=None):
    """Write one JSON result as explicit UTF-8, independent of Windows ACP."""
    stream = sys.stdout if stream is None else stream
    payload = (json.dumps(value, ensure_ascii=False, indent=indent) + "\n").encode(
        "utf-8", errors="strict")
    binary = getattr(stream, "buffer", None)
    if binary is not None:
        binary.write(payload)
    else:  # StringIO and other test/embedding streams have no binary layer.
        stream.write(payload.decode("utf-8"))


def main(argv=None):
    args = arguments(argv)
    result = query(
        args.operation, server=args.server, library_id=args.library_id,
        username=args.username, password_file=args.password_file,
        executable=args.executable,
        query_text=getattr(args, "query", None),
        limit=getattr(args, "limit", 1),
        sort_by=getattr(args, "sort_by", "last_modified"),
        ascending=getattr(args, "ascending", False),
        timeout=args.timeout, fallback=args.fallback)
    emit_json_utf8(result, indent=args.indent)
    return 0 if result["status"] in {"complete", "partial"} else 2


if __name__ == "__main__":
    sys.exit(main())
