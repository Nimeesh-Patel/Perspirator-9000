#!/usr/bin/env python3
"""Read-only Calibre library and reading evidence through loopback Calibre.

The Calibre GUI owns the live library. This adapter never opens ``metadata.db``
and never invokes a mutating ``calibredb`` command. Metadata and indexed
full-text queries use documented ``calibredb`` read surfaces. Desktop-viewer
annotations are recovered from the bounded EPUB representation served by the
Content Server, without modifying or unpacking the library book on disk.

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
    python calibre_query.py --library-id Calibre_Library \
      full-text --book-id 82 Wheeler
    python calibre_query.py --library-id Calibre_Library \
      annotations --book-id 82 --format EPUB

Discover the exact Content Server library id once instead of guessing it:
    calibredb list --with-library http://127.0.0.1:8081/#- --for-machine

Redirect a successful JSON result to a file and pass it later with
``--fallback`` if stale evidence is preferable to no evidence. The fallback is
validated against the exact operation and observation scope.
"""

import argparse
import base64
import binascii
import html
import hashlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import (
    HTTPBasicAuthHandler, HTTPDigestAuthHandler, HTTPPasswordMgrWithDefaultRealm,
    HTTPRedirectHandler, ProxyHandler, Request, build_opener,
)

from contracts import provider_result, validate_provider_result


PROVIDER = "calibre"
DEFAULT_SERVER = "http://127.0.0.1:8081"
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
DEFAULT_MAX_BOOK_BYTES = 128 * 1024 * 1024
MAX_MAX_BOOK_BYTES = 512 * 1024 * 1024
MAX_ANNOTATION_BYTES = 16 * 1024 * 1024
FTS_INDEXING_THRESHOLD = 100
FTS_MATCH_START = "[[CALIBRE_MATCH]]"
FTS_MATCH_END = "[[/CALIBRE_MATCH]]"
ANNOTATION_MEMBER = "META-INF/calibre_bookmarks.txt"
ANNOTATION_MAGIC = b"encoding=json+base64:\n"
LEGACY_ANNOTATION_SEPARATOR = "*|!|?|*"
LEGACY_ANNOTATION_ESCAPE = "esc-text-%&*#%(){}ads19-end-esc"
LEGACY_CURRENT_PAGE = "calibre_current_page_bookmark"
LEGACY_TIMESTAMP = "1970-01-01T00:00:00+00:00"
SUPPORTED_ANNOTATION_FORMATS = {"EPUB"}
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
FORMAT_RE = re.compile(r"^[A-Za-z0-9]+$")
LOOPBACKS = {"127.0.0.1", "::1"}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


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


def validate_book_id(book_id):
    if isinstance(book_id, bool):
        raise ValueError("book id must be a positive integer")
    if isinstance(book_id, int):
        pass
    elif isinstance(book_id, str) and book_id.isascii() and book_id.isdigit():
        book_id = int(book_id)
    else:
        raise ValueError("book id must be a positive integer")
    if book_id < 1:
        raise ValueError("book id must be a positive integer")
    return book_id


def validate_format(book_format):
    if not isinstance(book_format, str):
        raise ValueError("book format must be a simple Calibre format name")
    book_format = book_format.strip().upper()
    if not FORMAT_RE.fullmatch(book_format):
        raise ValueError("book format must be a simple Calibre format name")
    return book_format


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
              sort_by=None, ascending=False, authentication="not established",
              book_id=None, book_format=None, exact=False,
              indexing_threshold=None, max_book_bytes=None, surface=None,
              transport=None, read_only_commands=None):
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
        "book_id": book_id,
        "format": book_format,
        "exact": bool(exact),
        "indexing_threshold": indexing_threshold,
        "max_book_bytes": max_book_bytes,
        "surface": surface,
        "transport": transport or "calibredb via Content Server",
        "authentication": authentication,
        "read_only_commands": read_only_commands or ["list"],
        "local_write_used": False,
    }


def capability_for(operation):
    return {
        "status": "probe library readability",
        "list": "list books",
        "search": "search book metadata",
        "full-text": "search one book through Calibre's full-text index",
        "annotations": "recover one EPUB's embedded viewer annotations",
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


def fts_command_for(executable, server, library_id, username, password_file, *,
                    book_id, query, exact=False, timeout=20,
                    indexing_threshold=FTS_INDEXING_THRESHOLD):
    """Build one documented, read-only, book-bounded FTS command."""
    book_id = validate_book_id(book_id)
    if not isinstance(query, str) or not query.strip():
        raise ValueError("full-text query must be non-empty")
    if indexing_threshold != FTS_INDEXING_THRESHOLD:
        raise ValueError(
            f"indexing threshold must be {FTS_INDEXING_THRESHOLD} so absence "
            "is not inferred from an incompletely indexed library")
    command = [
        executable, "fts_search",
        "--with-library", library_url(server, library_id),
    ]
    if username is not None:
        command.extend([
            "--username", username,
            "--password", f"<f:{password_file.as_posix()}>",
        ])
    command.extend([
        "--timeout", str(timeout),
        "--output-format", "json",
        "--include-snippets",
        "--match-start-marker", FTS_MATCH_START,
        "--match-end-marker", FTS_MATCH_END,
        "--indexing-threshold", str(indexing_threshold),
        "--restrict-to", f"ids:{book_id}",
    ])
    if exact:
        command.append("--do-not-match-on-related-words")
    command.append(query.strip())
    return command


def invoke(command, timeout, runner=subprocess.run):
    """Run one allowlisted JSON-list read and preserve failure uncertainty."""
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
            # Calibre's machine JSON is UTF-8. Another code page is a provider
            # contract failure, not text to guess or replace.
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


def viewer_locator(library_id, book_id, book_format, open_at=None):
    base = f"calibre://view-book/{library_id}/{book_id}/{book_format}"
    if not open_at:
        return base
    return f"{base}?open_at={quote(open_at, safe='')}"


def _clean_fts_snippet(snippet, query):
    if not isinstance(snippet, str):
        raise ValueError("full-text result has a non-text snippet")
    # Calibre's GUI opens an FTS result by searching for its returned context.
    # Keep that context intact: selecting or joining only marked terms can lose
    # a multi-word match or invent a phrase when an FTS expression matched
    # separated terms.
    clean = snippet.replace(FTS_MATCH_START, "").replace(FTS_MATCH_END, "")
    clean = " ".join(clean.replace("\u00a0", " ").split()).strip(" .…")
    return clean or query.strip()


def fts_record(row, server, library_id, restricted_book_id, observed_at,
               query):
    if not isinstance(row, dict):
        raise ValueError("full-text result is not an object")
    book_id = validate_book_id(row.get("book_id"))
    if book_id != restricted_book_id:
        raise ValueError(
            f"full-text result escaped book restriction: {book_id}")
    book_format = validate_format(row.get("format"))
    title = row.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"full-text result for book {book_id} has no title")
    snippet = row.get("text", "")
    clean = _clean_fts_snippet(snippet, query)
    open_at = "search:" + clean
    return {
        "id": f"calibre:{library_id}:{book_id}:{book_format}:fts",
        "provider_identity": {
            "library_id": library_id,
            "book_id": book_id,
            "format": book_format,
        },
        "locator": viewer_locator(
            library_id, book_id, book_format, open_at=open_at),
        "text": snippet,
        "title": title.strip(),
        "authors": row.get("authors"),
        "format": book_format,
        "metadata": dict(row),
        "provenance": {
            "provider": PROVIDER,
            "representation": "calibredb fts_search --output-format json",
            "server": server,
            "network_scope": "loopback",
            "library_id": library_id,
            "observed_at": observed_at,
        },
    }


def _read_password(password_file):
    try:
        raw = Path(password_file).read_bytes()
        return raw.decode("utf-8-sig", errors="strict").rstrip("\r\n")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"password file could not be read as UTF-8: {exc}") from exc


def fetch_bytes(url, server, username, password_file, timeout, max_bytes,
                opener_factory=build_opener):
    """GET one bounded loopback representation without following redirects."""
    # An explicit empty proxy handler prevents urllib from inheriting system or
    # environment proxy settings for this loopback-only evidence surface.
    handlers = [ProxyHandler({}), _NoRedirect()]
    if username is not None:
        try:
            password = _read_password(password_file)
        except ValueError as exc:
            return {"ok": False, "status": "unavailable", "error": str(exc)}
        manager = HTTPPasswordMgrWithDefaultRealm()
        manager.add_password(None, server, username, password)
        handlers.extend([
            HTTPBasicAuthHandler(manager), HTTPDigestAuthHandler(manager)])
    opener = opener_factory(*handlers)
    request = Request(
        url, headers={"Accept": "application/epub+zip, application/octet-stream",
                      "User-Agent": "Perspirator-Calibre/1"})
    try:
        with opener.open(request, timeout=timeout) as response:
            final = urlsplit(response.geturl())
            expected = urlsplit(server)
            if (final.scheme, final.hostname, final.port) != (
                    expected.scheme, expected.hostname, expected.port):
                return {
                    "ok": False, "status": "unavailable",
                    "error": "Content Server response escaped the configured loopback origin",
                }
            declared = response.headers.get("Content-Length")
            if declared is not None:
                try:
                    if int(declared) > max_bytes:
                        return {
                            "ok": False, "status": "unavailable",
                            "error": f"book representation exceeds {max_bytes} bytes",
                        }
                except ValueError:
                    return {
                        "ok": False, "status": "indeterminate",
                        "error": "Content Server returned an invalid Content-Length",
                    }
            payload = response.read(max_bytes + 1)
    except HTTPError as exc:
        return {"ok": False, "status": "unavailable",
                "error": f"Content Server GET failed with HTTP {exc.code}"}
    except (socket.timeout, TimeoutError) as exc:
        return {"ok": False, "status": "indeterminate",
                "error": f"Content Server GET timed out after {timeout}s: {exc}"}
    except HTTPException as exc:
        return {"ok": False, "status": "indeterminate",
                "error": ("Content Server GET ended with an uncertain HTTP "
                          f"response: {type(exc).__name__}: {exc}")}
    except (URLError, OSError) as exc:
        return {"ok": False, "status": "unavailable",
                "error": f"Content Server GET failed: {type(exc).__name__}: {exc}"}
    if len(payload) > max_bytes:
        return {"ok": False, "status": "unavailable",
                "error": f"book representation exceeds {max_bytes} bytes"}
    return {"ok": True, "status": "complete", "bytes": payload}


def _decode_legacy_annotations(raw):
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"legacy embedded annotations are not UTF-8: {exc}") from exc
    records = []
    observed_rows = 0
    skip_reasons = {}

    def skipped(reason):
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

    for line in text.splitlines():
        if not line.strip():
            continue
        observed_rows += 1
        if "^" in line:
            skipped("older-legacy-record-not-migrated")
            continue
        if LEGACY_ANNOTATION_SEPARATOR not in line:
            skipped("unrecognized-line")
            continue
        try:
            title, spine_text, pos = line.strip().split(
                LEGACY_ANNOTATION_SEPARATOR)
            spine_index = int(spine_text)
            if spine_index < 0:
                raise ValueError("negative spine index")
        except (TypeError, ValueError):
            skipped("malformed-separator-record")
            continue
        pos = pos.replace(LEGACY_ANNOTATION_ESCAPE, "^")
        try:
            float(pos)
        except ValueError:
            pass
        else:
            # Calibre does not migrate legacy numeric positions to the modern
            # annotation representation.
            skipped("numeric-position-not-migrated")
            continue
        position = f"epubcfi(/{2 * (spine_index + 1)}/{pos.lstrip('/')})"
        record = {
            "pos": position,
            "pos_type": "epubcfi",
            "timestamp": LEGACY_TIMESTAMP,
        }
        if title and title != LEGACY_CURRENT_PAGE:
            record.update({"type": "bookmark", "title": title})
        else:
            record["type"] = "last-read"
        records.append(record)
    skipped_rows = sum(skip_reasons.values())
    return records, {
        "observed_rows": observed_rows,
        "recovered_rows": len(records),
        "skipped_rows": skipped_rows,
        "skip_reasons": skip_reasons,
    }


def _decode_embedded_annotations(payload):
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            matches = [info for info in archive.infolist()
                       if info.filename == ANNOTATION_MEMBER]
            if len(matches) > 1:
                raise ValueError(
                    f"EPUB contains duplicate {ANNOTATION_MEMBER} members")
            if not matches:
                return [], False, None, {
                    "observed_rows": 0, "recovered_rows": 0,
                    "skipped_rows": 0, "skip_reasons": {},
                }
            info = matches[0]
            if info.file_size > MAX_ANNOTATION_BYTES:
                raise ValueError(
                    f"embedded annotation member exceeds {MAX_ANNOTATION_BYTES} bytes")
            with archive.open(info) as handle:
                raw = handle.read(MAX_ANNOTATION_BYTES + 1)
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise ValueError(f"book representation is not a readable EPUB ZIP: {exc}") from exc
    if len(raw) > MAX_ANNOTATION_BYTES:
        raise ValueError(
            f"embedded annotation member exceeds {MAX_ANNOTATION_BYTES} bytes")
    if raw.startswith(ANNOTATION_MAGIC):
        encoded = b"".join(raw[len(ANNOTATION_MAGIC):].split())

        def reject_nonfinite(value):
            raise ValueError(f"non-finite JSON number is not supported: {value}")

        try:
            decoded = base64.b64decode(encoded, validate=True)
            annotations = json.loads(
                decoded.decode("utf-8-sig", errors="strict"),
                parse_constant=reject_nonfinite)
        except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
            raise ValueError(
                f"embedded annotations are not strict JSON+base64: {exc}") from exc
        if not isinstance(annotations, list):
            raise ValueError("embedded annotation payload is not a JSON list")
        return annotations, True, "json+base64", {
            "observed_rows": len(annotations),
            "recovered_rows": len(annotations),
            "skipped_rows": 0,
            "skip_reasons": {},
        }
    annotations, migration = _decode_legacy_annotations(raw)
    return annotations, True, "legacy", migration


def _viewer_epubcfi_position(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value.startswith("epubcfi(/") or not value.endswith(")"):
        return None
    # Validate the package-document spine selector without pretending to be a
    # complete CFI parser. Calibre-generated selectors are positive even
    # integers; Calibre itself parses the remainder, including escaped `^)`.
    first_segment = value[len("epubcfi(/"):-1].partition("/")[0]
    try:
        spine_selector = int(first_segment)
    except ValueError:
        return None
    if spine_selector < 2 or spine_selector % 2:
        return None
    return value


def _annotation_position(annotation):
    pos = annotation.get("pos")
    if annotation.get("pos_type") == "epubcfi":
        position = _viewer_epubcfi_position(pos)
        if position:
            return position
    if annotation.get("type") == "highlight":
        spine_index = annotation.get("spine_index")
        start_cfi = annotation.get("start_cfi")
        if (isinstance(spine_index, int) and spine_index >= 0
                and isinstance(start_cfi, str) and start_cfi.startswith("/")):
            return _viewer_epubcfi_position(
                f"epubcfi(/{2 * (spine_index + 1)}{start_cfi})")
    return None


def _annotation_identity(annotation, index):
    native = annotation.get("uuid")
    if isinstance(native, str) and native.strip():
        return native.strip()
    if annotation.get("type") == "last-read":
        return "last-read"
    if annotation.get("type") == "bookmark":
        title = annotation.get("title")
        if isinstance(title, str) and title:
            digest = hashlib.sha256(
                title.encode("utf-8", errors="strict")).hexdigest()[:24]
            return f"bookmark-{digest}"
    canonical = json.dumps(
        annotation, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8", errors="strict")
    digest = hashlib.sha256(canonical).hexdigest()[:24]
    return f"record-{index}-{digest}"


def annotation_record(annotation, index, server, library_id, book_id,
                      book_format, observed_at):
    if not isinstance(annotation, dict):
        raise ValueError("embedded annotation record is not an object")
    annotation_type = annotation.get("type")
    if not isinstance(annotation_type, str) or not annotation_type.strip():
        annotation_type = "unknown"
    identity = _annotation_identity(annotation, index)
    position = _annotation_position(annotation)
    withdrawal_state = (
        "withdrawn" if annotation.get("removed") is True else "active")
    parts = []
    for key in ("highlighted_text", "notes", "title", "pos"):
        value = annotation.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return {
        "id": (f"calibre:{library_id}:{book_id}:{book_format}:"
               f"annotation:{identity}"),
        "provider_identity": {
            "library_id": library_id,
            "book_id": book_id,
            "format": book_format,
            "annotation_id": identity,
        },
        "locator": viewer_locator(
            library_id, book_id, book_format,
            open_at=position if withdrawal_state == "active" else None),
        "text": "\n".join(parts),
        "annotation_type": annotation_type,
        "withdrawal_state": withdrawal_state,
        "timestamp": annotation.get("timestamp"),
        "position": position,
        "metadata": dict(annotation),
        "provenance": {
            "provider": PROVIDER,
            "representation": f"embedded EPUB:{ANNOTATION_MEMBER}",
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
    raw_count = len(rows)
    truncated = raw_count > limit
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
        "raw_rows": raw_count,
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


def live_fts_result(server, library_id, username, password_file, *,
                    executable="calibredb", book_id, query, limit=DEFAULT_LIMIT,
                    exact=False, timeout=20, runner=subprocess.run, now=utc_now):
    operation = "full-text"
    observed_at = now()
    scope = scope_for(
        operation, server, library_id, query=query, limit=limit,
        authentication="username/password" if username else "none",
        book_id=book_id, exact=exact,
        indexing_threshold=FTS_INDEXING_THRESHOLD,
        surface="Calibre native full-text index",
        transport="calibredb fts_search via Content Server",
        read_only_commands=["fts_search"])
    scope.update({
        "occurrence_coverage": (
            "Calibre returns a representative match per book format, not "
            "every occurrence or whole-book text"),
        "index_completeness_required": "100% accepted by Calibre",
    })
    try:
        command = fts_command_for(
            executable, server, library_id, username, password_file,
            book_id=book_id, query=query, exact=exact, timeout=timeout)
    except ValueError as exc:
        return error_result(operation, "unavailable", scope, observed_at, str(exc))
    transport = invoke(command, timeout, runner=runner)
    if not transport["ok"]:
        return error_result(
            operation, transport["status"], scope, observed_at,
            transport["error"])

    rows = transport["rows"]
    raw_count = len(rows)
    truncated = raw_count > limit
    rows = rows[:limit]
    records, errors, seen = [], [], set()
    for index, row in enumerate(rows):
        try:
            record = fts_record(
                row, server, library_id, book_id, observed_at, query)
            if record["id"] in seen:
                raise ValueError(
                    f"duplicate full-text identity: {record['id']}")
            seen.add(record["id"])
            records.append(record)
        except ValueError as exc:
            errors.append({"row": index, "error": str(exc)})
    scope.update({"returned": len(records), "raw_rows": raw_count,
                  "truncated": truncated})
    status = (
        "partial" if records and (errors or truncated)
        else "indeterminate" if errors
        else "partial" if truncated
        else "complete")
    explanation = None
    if errors:
        explanation = "one or more native full-text rows could not be represented"
    elif truncated:
        explanation = f"more than {limit} format matches were returned"
    return provider_result(
        PROVIDER, capability_for(operation), status, scope=scope,
        freshness={
            "observed_at": observed_at,
            "basis": "successful live Calibre full-text query",
        }, records=records, errors=errors, status_explanation=explanation)


def live_annotations_result(server, library_id, username, password_file, *,
                            book_id, book_format="EPUB", timeout=20,
                            limit=DEFAULT_LIMIT,
                            max_book_bytes=DEFAULT_MAX_BOOK_BYTES,
                            http_get=fetch_bytes, now=utc_now):
    operation = "annotations"
    observed_at = now()
    surface = f"embedded {book_format} copy:{ANNOTATION_MEMBER}"
    scope = scope_for(
        operation, server, library_id, limit=limit,
        authentication="username/password" if username else "none",
        book_id=book_id, book_format=book_format,
        max_book_bytes=max_book_bytes, surface=surface,
        transport="Content Server GET /get/{format}/{book_id}/{library_id}",
        read_only_commands=["GET book format"])
    scope.update({
        "annotation_namespace": "desktop-viewer records embedded in this EPUB copy",
        "unobserved_namespaces": ["local Calibre annotation database",
                                  "Content Server web-user annotations"],
        "absence_meaning": (
            "no records in the embedded EPUB copy; not proof that all "
            "Calibre annotation namespaces are empty"),
        "interface_stability": (
            "Calibre source-defined Content Server route; not a promised "
            "public API"),
    })
    url = f"{server}/get/{book_format}/{book_id}/{library_id}"
    transport = http_get(
        url, server, username, password_file, timeout, max_book_bytes)
    if not transport.get("ok"):
        return error_result(
            operation, transport.get("status", "indeterminate"), scope,
            observed_at, transport.get("error", "book download failed"))
    payload = transport.get("bytes")
    if not isinstance(payload, bytes):
        return error_result(
            operation, "indeterminate", scope, observed_at,
            "successful book GET returned a non-byte representation")
    scope["downloaded_bytes"] = len(payload)
    try:
        annotations, member_present, storage_format, migration = (
            _decode_embedded_annotations(payload))
    except ValueError as exc:
        return error_result(operation, "indeterminate", scope, observed_at, str(exc))
    scope["annotation_member_present"] = member_present
    scope["annotation_storage_format"] = storage_format
    raw_count = migration["observed_rows"]
    recoverable_count = len(annotations)
    truncated = recoverable_count > limit
    annotations = annotations[:limit]
    scope["raw_rows"] = raw_count
    scope["recoverable_rows"] = recoverable_count
    scope["migration_skipped_rows"] = migration["skipped_rows"]
    scope["migration_skip_reasons"] = migration["skip_reasons"]
    scope["truncated"] = truncated
    records, errors, seen = [], [], set()
    if migration["skipped_rows"]:
        errors.append({
            "error": (
                "one or more legacy annotation rows could not be migrated "
                "to Calibre's modern annotation representation"),
            "skipped_rows": migration["skipped_rows"],
            "skip_reasons": migration["skip_reasons"],
        })
    for index, annotation in enumerate(annotations):
        try:
            record = annotation_record(
                annotation, index, server, library_id, book_id,
                book_format, observed_at)
            if record["id"] in seen:
                raise ValueError(
                    f"duplicate embedded annotation identity: {record['id']}")
            seen.add(record["id"])
            records.append(record)
        except ValueError as exc:
            errors.append({"row": index, "error": str(exc)})
    scope["returned"] = len(records)
    status = (
        "partial" if records and (errors or truncated)
        else "indeterminate" if errors
        else "partial" if truncated
        else "complete")
    explanation = None
    if errors:
        explanation = "one or more embedded annotation records could not be represented"
    elif truncated:
        explanation = f"more than {limit} embedded annotation records were present"
    return provider_result(
        PROVIDER, capability_for(operation), status, scope=scope,
        freshness={
            "observed_at": observed_at,
            "basis": "successful bounded GET of the current served EPUB copy",
        }, records=records, errors=errors, status_explanation=explanation)


def stale_fallback(path, live, now=utc_now):
    """Use only an exact prior result, preserving why the live call failed."""
    if not path or live["status"] in {"complete", "partial"}:
        return live
    try:
        prior = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        validate_provider_result(prior)
        keys = (
            "operation", "server", "library_id", "query", "limit",
            "sort_by", "ascending", "authentication", "book_id", "format",
            "exact", "indexing_threshold", "max_book_bytes", "surface",
        )
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
          book_id=None, book_format="EPUB", exact=False,
          max_book_bytes=DEFAULT_MAX_BOOK_BYTES, timeout=20, fallback=None,
          runner=subprocess.run, http_get=fetch_bytes, now=utc_now):
    raw_server = server if isinstance(server, str) else str(server)
    raw_library_id = library_id
    raw_format = book_format
    raw_book_id = book_id
    try:
        if operation not in {"status", "list", "search", "full-text",
                              "annotations"}:
            raise ValueError(f"unsupported Calibre operation: {operation}")
        server = validate_server(server)
        library_id = validate_library_id(library_id)
        username, password_file = validate_auth(username, password_file)
        if not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
        if operation in {"search", "full-text"}:
            if not isinstance(query_text, str) or not query_text.strip():
                label = "full-text" if operation == "full-text" else "search"
                raise ValueError(f"{label} query must be non-empty")
            query_text = query_text.strip()
        if operation in {"full-text", "annotations"}:
            book_id = validate_book_id(book_id)
        if operation == "annotations":
            book_format = validate_format(book_format)
            if book_format not in SUPPORTED_ANNOTATION_FORMATS:
                raise ValueError(
                    "embedded annotation recovery currently supports EPUB only")
            if (isinstance(max_book_bytes, bool)
                    or not isinstance(max_book_bytes, int)
                    or not 1 <= max_book_bytes <= MAX_MAX_BOOK_BYTES):
                raise ValueError(
                    f"max book bytes must be between 1 and {MAX_MAX_BOOK_BYTES}")
    except ValueError as exc:
        authentication = (
            "username/password" if username and password_file
            else "none" if not username and not password_file
            else "incomplete configuration")
        scope = scope_for(
            operation, raw_server, raw_library_id, query=query_text, limit=limit,
            sort_by=sort_by, ascending=ascending,
            authentication=authentication, book_id=raw_book_id,
            book_format=raw_format, exact=exact,
            indexing_threshold=(FTS_INDEXING_THRESHOLD
                                if operation == "full-text" else None),
            max_book_bytes=(max_book_bytes
                            if operation == "annotations" else None))
        if operation not in {"status", "list", "search", "full-text",
                              "annotations"}:
            return provider_result(
                PROVIDER, "unknown Calibre operation", "unavailable",
                scope=scope,
                freshness={"observed_at": now(),
                           "basis": "input validation failed"},
                records=[], errors=[{"error": str(exc)}],
                status_explanation=str(exc))
        return error_result(operation, "unavailable", scope, now(), str(exc))

    if operation in {"status", "list", "search"}:
        live = live_result(
            operation, server, library_id, username, password_file,
            executable=executable,
            query=query_text if operation == "search" else None,
            limit=limit, sort_by=sort_by, ascending=ascending, timeout=timeout,
            runner=runner, now=now)
    elif operation == "full-text":
        live = live_fts_result(
            server, library_id, username, password_file,
            executable=executable, book_id=book_id, query=query_text,
            limit=limit, exact=exact, timeout=timeout, runner=runner, now=now)
    else:
        live = live_annotations_result(
            server, library_id, username, password_file,
            book_id=book_id, book_format=book_format, timeout=timeout,
            limit=limit, max_book_bytes=max_book_bytes,
            http_get=http_get, now=now)
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
    full_text = sub.add_parser(
        "full-text",
        help=("search one book through Calibre's native full-text index; "
              "returns representative format snippets, not every occurrence"))
    full_text.add_argument("query")
    full_text.add_argument("--book-id", type=int, required=True)
    full_text.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    full_text.add_argument(
        "--exact", action="store_true",
        help="do not match related word forms")
    annotations = sub.add_parser(
        "annotations",
        help="recover desktop-viewer records embedded in one served EPUB copy")
    annotations.add_argument("--book-id", type=int, required=True)
    annotations.add_argument("--limit", type=int, default=MAX_LIMIT)
    annotations.add_argument(
        "--format", dest="book_format", default="EPUB",
        help="book format (currently EPUB only)")
    annotations.add_argument(
        "--max-book-bytes", type=int, default=DEFAULT_MAX_BOOK_BYTES,
        help="maximum in-memory book download (default: %(default)s)")
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
        book_id=getattr(args, "book_id", None),
        book_format=getattr(args, "book_format", "EPUB"),
        exact=getattr(args, "exact", False),
        max_book_bytes=getattr(
            args, "max_book_bytes", DEFAULT_MAX_BOOK_BYTES),
        timeout=args.timeout, fallback=args.fallback)
    emit_json_utf8(result, indent=args.indent)
    return 0 if result["status"] in {"complete", "partial"} else 2


if __name__ == "__main__":
    sys.exit(main())
