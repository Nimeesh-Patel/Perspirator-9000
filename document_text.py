#!/usr/bin/env python3
"""Bounded, read-only document text extraction and selection.

Text offsets refer to the declared normalized extraction, never original file
bytes or ebook page numbers. OCR requires an explicit PDF request. No source
conversion, external resources or persistent cache.
"""

import io
import posixpath
import re
import zipfile
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET


FORMATS = {"EPUB", "KEPUB", "TXT", "HTML", "HTM", "PDF", "DOCX", "DOC"}
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_TEXT_CHARS = 16 * 1024 * 1024
MAX_SECTIONS = 5000
DEFAULT_CHARS = 6000
DEFAULT_CONTEXT = 600
NORMALIZATION = "decoded text; NBSP replaced; horizontal whitespace collapsed; paragraph/page boundaries retained"


class DocumentError(ValueError):
    def __init__(self, message, status="indeterminate"):
        super().__init__(message)
        self.status = status


def normalize(text):
    text = text.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def decode_text(data, markup=False):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="strict")
    # Respect explicit XML/HTML declarations; do not guess legacy codepages.
    header = data[:1024]
    declared = re.search(br'(?:encoding|charset)\s*=\s*["\']?([\w-]+)', header, re.I) if markup else None
    encoding = declared.group(1).decode("ascii") if declared else "utf-8-sig"
    return data.decode(encoding, errors="strict")


class Prose(HTMLParser):
    BLOCKS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title_parts = [], []
        self.hidden = 0
        self.in_title = False
        self.heading_parts = []
        self.in_heading = False
        self.heading_seen = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag == "title":
            self.in_title = True
        if re.fullmatch(r"h[1-6]", tag) and not self.heading_seen:
            self.in_heading = True
        if tag in self.BLOCKS and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "title":
            self.in_title = False
        if re.fullmatch(r"h[1-6]", tag) and self.in_heading:
            self.in_heading = False
            self.heading_seen = bool(self.heading_parts)
        if tag in self.BLOCKS and not self.hidden:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_heading and not self.hidden:
            self.heading_parts.append(data)
        if self.in_title:
            self.title_parts.append(data)
        elif not self.hidden:
            self.parts.append(data)


def html_section(data, member):
    parser = Prose()
    parser.feed(decode_text(data, markup=True))
    parser.close()
    text = normalize("".join(parser.parts))
    title = normalize("".join(parser.heading_parts)) or normalize("".join(parser.title_parts))
    return {"member": member, "title": title or member, "text": text}


def archive_member(base, href):
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc or parsed.query:
        raise DocumentError(f"external or queried archive reference refused: {href}")
    path = unquote(parsed.path)
    if "\\" in path or path.startswith("/"):
        raise DocumentError(f"absolute archive reference refused: {href}")
    member = posixpath.normpath(posixpath.join(base, path))
    if member == ".." or member.startswith("../"):
        raise DocumentError(f"archive reference escaped package: {href}")
    return member


def checked_zip(data):
    archive = zipfile.ZipFile(io.BytesIO(data))
    infos = archive.infolist()
    if sum(info.file_size for info in infos) > MAX_EXPANDED_BYTES:
        archive.close()
        raise DocumentError("archive exceeds expanded-byte bound", "unavailable")
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        archive.close()
        raise DocumentError("duplicate archive members make source identity ambiguous")
    return archive


def epub_sections(data):
    sections, errors = [], []
    with checked_zip(data) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfiles = container.findall(".//{*}rootfile")
        if not rootfiles:
            raise DocumentError("EPUB container has no package rootfile")
        package = archive_member("", rootfiles[0].get("full-path", ""))
        root = ET.fromstring(archive.read(package))
        manifest = root.find("{*}manifest")
        spine = root.find("{*}spine")
        if manifest is None or spine is None:
            raise DocumentError("EPUB package has no manifest/spine")
        items = {item.get("id"): item for item in manifest}
        refs = list(spine)
        if not refs:
            raise DocumentError("EPUB reading spine is empty")
        for index, ref in enumerate(refs[:MAX_SECTIONS], 1):
            try:
                item = items.get(ref.get("idref"))
                if item is None:
                    raise DocumentError("spine reference missing from manifest")
                if item.get("media-type") not in {"application/xhtml+xml", "text/html"}:
                    raise DocumentError("spine item has unsupported text media type")
                member = archive_member(posixpath.dirname(package), item.get("href", ""))
                section = html_section(archive.read(member), member)
                section.update({"section": index, "spine_idref": ref.get("idref"), "linear": ref.get("linear", "yes")})
                sections.append(section)
            except (DocumentError, KeyError, UnicodeError, LookupError) as exc:
                errors.append({"section": index, "error": str(exc)})
        if len(refs) > MAX_SECTIONS:
            errors.append({"error": "EPUB spine exceeds section bound"})
    return sections, errors, "EPUB reading spine; non-spine resources excluded"


def docx_sections(data):
    sections = []
    with checked_zip(data) as archive:
        for member, label in (("word/document.xml", "Document body"),
                              ("word/footnotes.xml", "Footnotes"),
                              ("word/endnotes.xml", "Endnotes")):
            if member not in archive.namelist():
                if member == "word/document.xml":
                    raise DocumentError("DOCX has no document body")
                continue
            root = ET.fromstring(archive.read(member))
            paragraphs = []
            for para in root.findall(".//{*}p"):
                pieces = []
                for element in para.iter():
                    name = element.tag.rsplit("}", 1)[-1]
                    if name == "t":
                        pieces.append(element.text or "")
                    elif name in {"tab", "br", "cr"}:
                        pieces.append("\n" if name != "tab" else "\t")
                paragraphs.append("".join(pieces))
            sections.append({"section": len(sections) + 1, "member": member,
                             "title": label, "text": normalize("\n\n".join(paragraphs))})
    return sections, [], "DOCX stored paragraph text in body, tables, footnotes and endnotes; headers, comments, deleted revision text and embedded binaries excluded; no Word layout"


def validate_ocr(book_format, ocr, max_ocr_pages, ocr_language):
    if ocr and book_format != "PDF":
        raise DocumentError("OCR is available for PDF only", "unavailable")
    if isinstance(max_ocr_pages, bool) or not isinstance(max_ocr_pages, int) or not 1 <= max_ocr_pages <= 100:
        raise DocumentError("max OCR pages must be between 1 and 100", "unavailable")
    if not isinstance(ocr_language, str) or not re.fullmatch(r"[A-Za-z0-9-]{2,35}", ocr_language):
        raise DocumentError("OCR language must be a Windows language tag", "unavailable")


def pdf_sections(data, max_pages, *, operation=None, section=None,
                 ocr=False, max_ocr_pages=20, ocr_language="en-US"):
    try:
        import pdfplumber
    except ImportError as exc:
        raise DocumentError("PDF text requires pdfplumber; install requirements-pdf.txt", "unavailable") from exc
    sections, errors, empty, recognized = [], [], [], []
    ocr_attempts = 0
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        count = len(pdf.pages)
        if section is not None and section > count:
            raise DocumentError("selected physical page exceeds PDF page count", "unavailable")
        indexes = [section - 1] if section is not None else range(min(count, max_pages))
        for page_index in indexes:
            index = page_index + 1
            if operation == "contents":
                sections.append({"section": index, "page_number": index,
                                 "title": f"Physical page {index}", "text": "", "characters": None})
                continue
            page = None
            try:
                page = pdf.pages[page_index]
                text = normalize(page.extract_text() or "")
                ocr_metadata = None
                if not text and ocr:
                    if ocr_attempts >= max_ocr_pages:
                        errors.append({"page_number": index, "error": "OCR page bound reached; page text unobserved"})
                    else:
                        ocr_attempts += 1
                        from pdf_ocr import OCRError, recognize_pdf_page
                        try:
                            evidence = recognize_pdf_page(data, page_index, language=ocr_language)
                            text = normalize(evidence["text"])
                            ocr_metadata = {key: value for key, value in evidence.items() if key != "text"}
                            if text:
                                recognized.append(index)
                        except OCRError as exc:
                            errors.append({"page_number": index, "error": str(exc)})
                if not text:
                    empty.append(index)
                record = {"section": index, "page_number": index,
                          "title": f"Physical page {index}", "text": text,
                          "text_origin": "ocr" if ocr_metadata else "PDF text layer"}
                if ocr_metadata:
                    record["ocr"] = ocr_metadata
                sections.append(record)
            except Exception as exc:
                errors.append({"page_number": index, "error": f"text extraction failed: {type(exc).__name__}: {exc}"})
            finally:
                # A long document need not retain every parsed character object.
                if page is not None:
                    page.close()
        if section is None and count > max_pages:
            errors.append({"error": "PDF page bound reached", "total_pages": count, "parsed_pages": max_pages})
    if empty:
        errors.append({"error": "pages have no extractable text; blank/scanned distinction unresolved" + (" after requested OCR" if ocr else "; OCR not requested"), "pages": empty})
    if recognized:
        errors.append({"error": "OCR-derived text requires verification against original scanned pages", "pages": recognized})
    coverage = ("PDF physical page enumeration only; text layer not inspected" if operation == "contents" else
                "PDF text layer on selected physical page; other pages unobserved" if section is not None else
                "PDF text layer in physical page order; layout reading order may differ")
    if ocr and operation != "contents":
        coverage += "; optional Windows OCR on pages without text only; embedded images on text-bearing pages not OCRed"
    elif operation != "contents":
        coverage += "; no OCR requested"
    return sections, errors, coverage


def parse_document(data, book_format, *, max_pdf_pages=500, operation=None, section=None,
                   ocr=False, max_ocr_pages=20, ocr_language="en-US"):
    book_format = book_format.upper()
    validate_ocr(book_format, ocr, max_ocr_pages, ocr_language)
    if book_format not in FORMATS:
        raise DocumentError(f"unsupported text format: {book_format}; supported: {', '.join(sorted(FORMATS))}", "unavailable")
    try:
        if book_format in {"EPUB", "KEPUB"}:
            sections, errors, coverage = epub_sections(data)
        elif book_format == "DOCX":
            sections, errors, coverage = docx_sections(data)
        elif book_format == "DOC":
            from legacy_doc import read_doc
            try:
                result = read_doc(data)
            except Exception as exc:
                raise DocumentError(str(exc), "unavailable") from exc
            sections = [dict(row, section=index, text=normalize(row["text"].replace("\r", "\n").replace("\x07", "\t")),
                             text_origin="Microsoft Word native text")
                        for index, row in enumerate(result["sections"], 1)]
            errors, coverage = [], "Word body, footnotes and endnotes from a temporary read-only DOC copy; headers, comments and embedded binaries excluded"
        elif book_format == "PDF":
            sections, errors, coverage = pdf_sections(data, max_pdf_pages, operation=operation, section=section,
                                                     ocr=ocr, max_ocr_pages=max_ocr_pages, ocr_language=ocr_language)
        else:
            section = (html_section(data, "document") if book_format in {"HTML", "HTM"}
                       else {"title": "Text document", "text": normalize(decode_text(data))})
            section["section"] = 1
            sections, errors, coverage = [section], [], "one text document"
        remaining, bounded = MAX_TEXT_CHARS, []
        for section in sections:
            if remaining <= 0:
                errors.append({"error": "normalized-text bound reached; remaining sections unobserved"})
                break
            text = section["text"]
            if len(text) > remaining:
                section = dict(section, text=text[:remaining])
                errors.append({"section": section["section"], "error": "normalized-text bound reached within section"})
            remaining -= len(section["text"])
            bounded.append(section)
        return {"sections": bounded, "errors": errors, "coverage": coverage,
                "normalization": NORMALIZATION, "extraction_complete": not errors}
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(f"{book_format} extraction failed: {type(exc).__name__}: {exc}") from exc


def validate_selection(operation, *, query_text=None, section=None,
                       offset=0, chars=DEFAULT_CHARS, context=DEFAULT_CONTEXT, limit=20):
    for label, value, minimum, maximum in (("offset", offset, 0, 1000000000),
                                           ("chars", chars, 1, 32000),
                                           ("context", context, 0, 8000),
                                           ("limit", limit, 1, 500)):
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise DocumentError(f"{label} must be between {minimum} and {maximum}", "unavailable")
    if section is not None and (isinstance(section, bool) or not isinstance(section, int) or section < 1):
        raise DocumentError("section must be a positive integer", "unavailable")
    if operation == "read" and section is None:
        raise DocumentError("read requires --section from contents", "unavailable")
    if operation == "passages" and (not isinstance(query_text, str) or not query_text.strip() or len(query_text) > 2000):
        raise DocumentError("passages requires a non-empty literal query of at most 2000 characters", "unavailable")


def select_text(parsed, operation, *, query_text=None, section=None, offset=0,
                chars=DEFAULT_CHARS, context=DEFAULT_CONTEXT, limit=20):
    validate_selection(operation, query_text=query_text, section=section,
                       offset=offset, chars=chars, context=context, limit=limit)
    sections = [item for item in parsed["sections"] if section is None or item["section"] == section]
    if section is not None and not sections:
        raise DocumentError("selected section was not extracted; inspect contents and extraction errors", "unavailable")
    records, total, next_offset = [], 0, None
    if operation == "contents":
        total = len(sections)
        for item in sections[offset:offset + limit]:
            descriptor = {key: value for key, value in item.items() if key != "text"}
            descriptor.update({"text": item["title"], "characters": item.get("characters", len(item["text"]))})
            records.append(descriptor)
        if offset + len(records) < total:
            next_offset = offset + len(records)
    elif operation == "read":
        item = sections[0]
        total = len(item["text"])
        if offset > total:
            raise DocumentError("offset exceeds extracted section length", "unavailable")
        stop = min(total, offset + chars)
        if stop > offset:
            records.append(dict(item, text=item["text"][offset:stop], start=offset, end=stop))
        if stop < total:
            next_offset = stop
    elif operation == "passages":
        # Whitespace-flexible, case-insensitive literal phrases, not FTS syntax.
        pattern = re.compile(r"\s+".join(re.escape(word) for word in query_text.split()), re.I)
        for item in sections:
            for match in pattern.finditer(item["text"]):
                if offset <= total < offset + limit:
                    start, end = max(0, match.start() - context), min(len(item["text"]), match.end() + context)
                    records.append(dict(item, text=item["text"][start:end], start=start, end=end,
                                        match_start=match.start(), match_end=match.end(), occurrence=total))
                total += 1
        if offset + len(records) < total:
            next_offset = offset + len(records)
    else:
        raise DocumentError(f"unsupported text operation: {operation}", "unavailable")
    return {"records": records, "total": total, "next_offset": next_offset,
            "truncated": next_offset is not None,
            "offset_unit": "normalized characters in selected section" if operation == "read" else "occurrences" if operation == "passages" else "sections"}
