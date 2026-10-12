#!/usr/bin/env python3
"""Read/search one explicitly named local document without indexing or modifying it.

Examples:
    python document_query.py --path C:/papers/paper.pdf contents
    python document_query.py --path C:/papers/paper.pdf read --section 3
    python document_query.py --path C:/papers/paper.pdf passages 'error correction'

Supported: EPUB/KEPUB, TXT, HTML/HTM, PDF text layers, DOCX paragraphs and
legacy DOC through installed Windows Microsoft Word. Scanned pages can use
--ocr with Windows local OCR. OCR results require checking against images.
DRM, unavailable native readers and unsupported formats are reported.
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from contracts import provider_result, source_record
from document_text import (DEFAULT_CHARS, DEFAULT_CONTEXT, DocumentError,
                           parse_document, select_text, validate_selection, validate_ocr)


MAX_BYTES = 128 * 1024 * 1024
TEXT_OPERATIONS = {"contents", "read", "passages"}


def text_result(provider, operation, parsed, selected, scope, observed_at,
                identity, source_uri, reader_locator, sha256):
    scope.update({key: parsed[key] for key in ("coverage", "normalization", "extraction_complete")})
    scope.update({"returned": len(selected["records"]), "total": selected["total"],
                  "next_offset": selected["next_offset"], "offset_unit": selected["offset_unit"],
                  "truncated": selected["truncated"], "representation_sha256": sha256,
                  "query_semantics": "case-insensitive literal phrase; flexible whitespace; non-overlapping matches within each section" if operation == "passages" else None})
    records = []
    for index, item in enumerate(selected["records"]):
        anchor = f"section={item['section']}&start={item.get('start', 0)}"
        if "end" in item:
            anchor += f"&end={item['end']}"
        if "occurrence" in item:
            anchor += f"&occurrence={item['occurrence']}"
        locator = source_uri + "#" + anchor
        records.append(source_record(
            f"{source_uri}:{operation}:{anchor}", item["text"], locator,
            provider=provider, provider_identity=identity, reader_locator=reader_locator,
            completeness="complete" if parsed["extraction_complete"] else "partial",
            completeness_explanation=("OCR recognition requires verification against source image" if item.get("text_origin") == "ocr" else
                                      "document extraction incomplete; inspect provider errors" if not parsed["extraction_complete"] else None),
            provenance={"provider": provider, "representation": "bounded read-only normalized text extraction",
                        "observed_at": observed_at, "representation_sha256": sha256,
                        "source": source_uri, "normalization": parsed["normalization"],
                        "text_origin": item.get("text_origin", "native document text")},
            **{key: value for key, value in item.items() if key != "text"}))
    status = "partial" if parsed["errors"] or selected["truncated"] else "complete"
    return provider_result(
        provider, f"{operation} in one document", status, scope=scope,
        freshness={"observed_at": observed_at, "basis": "current bounded source bytes parsed without writes"},
        records=records, errors=parsed["errors"],
        status_explanation=("extraction incomplete; inspect errors" if parsed["errors"] else
                            "bounded output; continue with next_offset" if selected["truncated"] else None))


def add_text_arguments(parser, operation):
    parser.add_argument("--ocr", action="store_true", help="Windows OCR for PDF pages with no text layer; derived text remains partial evidence")
    parser.add_argument("--max-ocr-pages", type=int, default=20)
    parser.add_argument("--ocr-language", default="en-US")
    if operation == "passages":
        parser.add_argument("query", nargs="?")
        parser.add_argument("--query-file", help="UTF-8 query file; '-' reads stdin")
        parser.add_argument("--context", type=int, default=DEFAULT_CONTEXT)
    parser.add_argument("--section", type=int, required=operation == "read")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=20)
    if operation == "read":
        parser.add_argument("--chars", type=int, default=DEFAULT_CHARS)


def input_query(args, parser):
    query, path = getattr(args, "query", None), getattr(args, "query_file", None)
    if path is not None:
        if query is not None:
            parser.error("provide query text or --query-file, not both")
        try:
            if path == "-":
                stream = getattr(sys.stdin, "buffer", None)
                query = stream.read(32769).decode("utf-8-sig") if stream is not None else sys.stdin.read(32769)
            else:
                with Path(path).open("rb") as stream:
                    query = stream.read(32769).decode("utf-8-sig")
            if len(query.encode("utf-8")) > 32768:
                parser.error("query file exceeds 32768 UTF-8 bytes")
        except (OSError, UnicodeError) as exc:
            parser.error(f"cannot read UTF-8 query file: {exc}")
    return query


def query(path, operation, *, book_format=None, query_text=None, section=None,
          offset=0, chars=DEFAULT_CHARS, context=DEFAULT_CONTEXT, limit=20,
          max_bytes=MAX_BYTES, max_pdf_pages=500, ocr=False,
          max_ocr_pages=20, ocr_language="en-US"):
    observed_at = datetime.now(timezone.utc).isoformat()
    scope = {"operation": operation, "path": str(path), "format": book_format,
             "query": query_text, "section": section, "offset": offset,
             "chars": chars, "context": context, "limit": limit,
             "max_bytes": max_bytes, "max_pdf_pages": max_pdf_pages,
             "ocr": bool(ocr), "max_ocr_pages": max_ocr_pages, "ocr_language": ocr_language,
             "writes_used": False, "transport": "explicit local file"}
    try:
        if operation not in TEXT_OPERATIONS:
            raise DocumentError("unsupported document operation", "unavailable")
        validate_selection(operation, query_text=query_text, section=section,
                           offset=offset, chars=chars, context=context, limit=limit)
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_BYTES:
            raise DocumentError("max bytes must be between 1 and 134217728", "unavailable")
        if isinstance(max_pdf_pages, bool) or not isinstance(max_pdf_pages, int) or not 1 <= max_pdf_pages <= 5000:
            raise DocumentError("max PDF pages must be between 1 and 5000", "unavailable")
        source = Path(path).expanduser().resolve(strict=True)
        if not source.is_file():
            raise DocumentError("source is not a regular file", "unavailable")
        with source.open("rb") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise DocumentError("source exceeds byte bound", "unavailable")
        book_format = (book_format or source.suffix.lstrip(".")).upper()
        scope.update({"path": str(source), "format": book_format})
        validate_ocr(book_format, ocr, max_ocr_pages, ocr_language)
        parsed = parse_document(data, book_format, max_pdf_pages=max_pdf_pages, operation=operation, section=section,
                                ocr=ocr, max_ocr_pages=max_ocr_pages, ocr_language=ocr_language)
        selected = select_text(parsed, operation, query_text=query_text, section=section,
                               offset=offset, chars=chars, context=context, limit=limit)
        return text_result("local-document", operation, parsed, selected, scope, observed_at,
                           {"path": str(source), "format": book_format}, source.as_uri(),
                           source.as_uri(), hashlib.sha256(data).hexdigest())
    except (DocumentError, OSError, ValueError, TypeError) as exc:
        status = exc.status if isinstance(exc, DocumentError) else "unavailable"
        return provider_result("local-document", f"{operation} in one document", status,
                               scope=scope, freshness={"observed_at": observed_at, "basis": "attempted explicit file read"},
                               records=[], errors=[{"error": str(exc)}], status_explanation=str(exc))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", required=True)
    parser.add_argument("--format", dest="book_format")
    parser.add_argument("--max-bytes", type=int, default=MAX_BYTES)
    parser.add_argument("--max-pdf-pages", type=int, default=500)
    sub = parser.add_subparsers(dest="operation", required=True)
    for operation in sorted(TEXT_OPERATIONS):
        add_text_arguments(sub.add_parser(operation), operation)
    args = parser.parse_args(argv)
    result = query(args.path, args.operation, book_format=args.book_format,
                   query_text=input_query(args, parser), section=args.section,
                   offset=args.offset, chars=getattr(args, "chars", DEFAULT_CHARS),
                   context=getattr(args, "context", DEFAULT_CONTEXT), limit=args.limit,
                   max_bytes=args.max_bytes, max_pdf_pages=args.max_pdf_pages,
                   ocr=args.ocr, max_ocr_pages=args.max_ocr_pages, ocr_language=args.ocr_language)
    payload = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(payload)
    else:
        sys.stdout.write(payload.decode("utf-8"))
    return 0 if result["status"] in {"complete", "partial"} else 2


if __name__ == "__main__":
    sys.exit(main())
