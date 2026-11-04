#!/usr/bin/env python3
"""Nominate a bounded vault frontier; select full originals with role_harness.

Names and rankings are discovery evidence, not a current-situation account or
a decision about relevance. This tool neither runs a worker nor grants writes.
"""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import sys
from urllib.parse import quote

from adapters import add_vault_argument
from contracts import emit_json_utf8, provider_result
from note_chunks import frontmatter_fields, iter_notes
from problem_half import parse_note
from role_harness import canonical, exact_path, freshness, reparse, snapshot


VERSION = 1
PREVIEW_CHARS = 220


def relative_name(value):
    """A supplied name cannot become traversal or an external-file request."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("named note must be nonempty text")
    value = value.strip().replace("\\", "/")
    path = PurePosixPath(value)
    if path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts:
        raise ValueError(f"named note must stay inside the vault: {value}")
    if value in (".", ".."):
        raise ValueError("named note must identify a note")
    return path.as_posix()


def note_observation(vault, value):
    """Read one exact original using the existing role snapshot mechanism."""
    value = relative_name(value)
    if not value.lower().endswith(".md"):
        raise ValueError(f"candidate is not an exact Markdown path: {value}")
    path = exact_path(value, vault, inside=vault)
    item = snapshot(value, path)
    fields = frontmatter_fields(item["text"].removeprefix("\ufeff"))
    parsed = parse_note(item["text"].removeprefix("\ufeff"))
    is_problem = parsed["has_separator"]
    preview_text = parsed["problem"] if is_problem else parsed["body"]
    # A marked excerpt supports nomination only; original sources are retained
    # as exact paths for the existing complete role packet, never this preview.
    preview_text = preview_text or ""
    preview = preview_text[:PREVIEW_CHARS]
    source = {
        "note": path.relative_to(vault).as_posix(), "path": str(path),
        "sha256": item["sha256"], "bytes": item["bytes"],
        "locator": ("obsidian://open?vault=" + quote(vault.name, safe="")
                    + "&file=" + quote(path.relative_to(vault).as_posix(), safe="")),
        "provenance": {"provider": "filesystem", "identity": "raw-byte-sha256"},
        "observation_status": "current",
        "declared": fields,
        "metadata_representation": (
            "note_chunks.frontmatter_fields top-level value strings; "
            "compound YAML, aliases, authorship and adoption are not interpreted"),
        "structure": "problem-note" if is_problem else "no-separator",
        "preview": {"text": preview, "characters": len(preview),
                    "total_characters": len(preview_text),
                    "truncated": len(preview_text) > PREVIEW_CHARS,
                    "limit_characters": PREVIEW_CHARS,
                    "side": "problem" if is_problem else "body",
                    "representation": "normalized-newlines-without-frontmatter",
                    "conjecture_omitted": is_problem,
                    "source_reading": "not-a-complete-source-reading"},
    }
    return source, item


def scalar_title(fields):
    """Use the shared reader's scalar surface; do not claim YAML alias support."""
    value = fields.get("title", "")
    return value if value and not value.startswith(("[", "{", "|", ">", "&", "*", "!")) else None


def nominate(vault, *, texts=(), named=(), k=10, graph="configured", corpus=None,
             rank="both", refresh_index=True, max_bytes):
    """Return bounded nominations, preserving independent supplied facets.

    Named-only use never loads a retrieval index or embedding model. All text
    facets share neighbour.match_batch. No source is selected by this function.
    """
    if max_bytes <= 0:
        raise ValueError("max-bytes must be positive")
    if k <= 0:
        raise ValueError("k must be positive")
    if rank not in ("both", "embedding", "lexical"):
        raise ValueError("unknown ranker selection")
    if graph not in ("configured", "obsidian", "filesystem", "none"):
        raise ValueError("unknown graph selection")
    if corpus not in (None, "all", "vault", "memory"):
        raise ValueError("unknown corpus selection")
    texts, named = list(texts), [relative_name(value) for value in named]
    if not texts and not named:
        raise ValueError("supply at least one --text or --named nomination")
    if any(not isinstance(text, str) or not text.strip() for text in texts):
        raise ValueError("every text facet must be nonempty")
    supplied_vault = Path(os.path.abspath(Path(vault).expanduser()))
    if not supplied_vault.is_dir():
        raise ValueError(f"vault directory unavailable: {supplied_vault}")
    for candidate in (supplied_vault, *supplied_vault.parents):
        if reparse(candidate):
            raise ValueError(f"reparse boundary: {candidate}")
    vault = supplied_vault.resolve(strict=True)
    observed_at = datetime.now(timezone.utc).isoformat()
    observations, errors = {}, []

    def observe(value):
        if value in observations:
            return observations[value]
        try:
            source, item = note_observation(vault, value)
        except (OSError, ValueError) as exc:
            error = {"note": value, "status": "unavailable", "error": str(exc)}
            errors.append(error)
            observations[value] = error
            return error
        observations[value] = source
        return source

    # Scan only for exact name/title requests. Direct paths avoid this scan and
    # can deliberately name an otherwise ambient-exempt artifact.
    name_requests = [value for value in named if "/" not in value and not value.lower().endswith(".md")]
    names = {}
    scanned = 0
    if name_requests:
        for path in iter_notes(vault):
            rel = path.relative_to(vault).as_posix()
            source = observe(rel)
            scanned += 1
            if source.get("observation_status") == "current":
                fields = source["declared"]
                for key, value in (("stem", path.stem), ("title", scalar_title(fields))):
                    if value in name_requests:
                        names.setdefault(value, {}).setdefault(rel, []).append(key)

    named_channel, candidates = [], {}

    def nominate_path(value, reason):
        source = observe(value)
        if source.get("observation_status") != "current":
            return
        entry = candidates.setdefault(source["note"], {**source, "reasons": []})
        entry["reasons"].append(reason)

    for value in named:
        is_path = "/" in value or value.lower().endswith(".md")
        matches = {value: ["path"]} if is_path else names.get(value, {})
        found = []
        for rel, matched in matches.items():
            source = observe(rel)
            if source.get("observation_status") != "current":
                continue
            found.append({"note": source["note"], "matched_by": matched})
            nominate_path(rel, {"channel": "named", "requested": value,
                               "matched_by": matched,
                               "ambient_exemptions": "not-applied-to-explicit-nomination"})
        status = "ambiguous" if len(found) > 1 else ("matched" if found else "unresolved")
        if is_path and not found and observations.get(value, {}).get("status") == "unavailable":
            status = "unavailable"
        named_channel.append({"requested": value, "status": status, "candidates": found})

    ranked, index_header, refresh = [], None, None
    if texts:
        # Lazy import keeps the named route independent even of loading the
        # optional neighbour module and its index configuration.
        try:
            from neighbour import match_batch
            batch = match_batch(str(vault), [{"text": text, "note": None, "file": None}
                                             for text in texts], k=k, graph=graph,
                                corpus=corpus, rank=rank, refresh_index=refresh_index)
            if len(batch["queries"]) != len(texts):
                raise ValueError("ranked batch returned a different facet count")
        except (ImportError, OSError, ValueError, SystemExit) as exc:
            failure = {"status": "unavailable", "error": str(exc),
                       "error_type": type(exc).__name__}
            errors.append({"channel": "ranked", **failure})
            ranked = [{"query_index": number, "text": text, **failure,
                       "graph": {"provider": "unobserved", "status": "unavailable"}}
                      for number, text in enumerate(texts, 1)]
        else:
            index_header, refresh = batch["header"], batch["refresh"]
            for number, (text, result) in enumerate(zip(texts, batch["queries"]), 1):
                accepted, rejected = [], []
                for hit in result["results"]:
                    source = observe(hit["note"])
                    if source.get("observation_status") != "current":
                        rejected.append({"note": hit["note"], "status": "unavailable"})
                        continue
                    accepted.append(hit)
                    nominate_path(hit["note"], {"channel": "ranked", "query_index": number,
                                               "text": text, "evidence": dict(hit)})
                safe_result = {**result, "results": accepted,
                               "query_index": number, "text": text}
                if rejected:
                    rejected_paths = {item["note"] for item in rejected}
                    graph_result = result["graph"]
                    safe_result["graph"] = {
                        **graph_result, "status": "partial",
                        "notes": [item for item in graph_result.get("notes", [])
                                  if item.get("path") not in rejected_paths],
                    }
                    safe_result["rejected_sources"] = rejected
                ranked.append(safe_result)

    # Check precisely the revision retained in each output record. A later
    # observation through another spelling must not certify an older preview.
    current = freshness([{"id": entry["note"], "path": entry["path"],
                          "sha256": entry["sha256"]}
                         for entry in candidates.values()])
    for entry, check in zip(candidates.values(), current):
        entry["observation_status"] = check["status"]
        if check["status"] != "current":
            errors.append({"note": entry["note"], **check})
    limits = [
        "Nominations do not establish relevance, current priorities, or exhaustive coverage.",
        "Previews are bounded nomination evidence; give selected original paths to role_harness packet.",
        "Filesystem alias resolution is unsupported; Obsidian owns native alias/link resolution.",
        "Graph links and backlinks remain bounded provider evidence, not automatically selected sources.",
    ]
    if name_requests:
        limits.append("Name scan uses note_chunks default structural exclusions; explicit paths can name excluded notes.")
    if texts and not refresh_index:
        limits.append("Ambient retrieval used the existing index without refreshing it.")
    graph_partial = any(query["graph"].get("status") not in ("ok", "disabled")
                        for query in ranked)
    missing_nominations = bool(index_header and
                               index_header["freshness"].get("missing_paths"))
    result = provider_result(
        "vault-context-query", "nominate",
        "partial" if errors or graph_partial or missing_nominations else "complete",
        scope={"vault": str(vault), "named": named, "text_facets": texts,
               "ambient": {"enabled": bool(texts), "k_per_facet": k,
                           "rank": rank, "corpus": corpus or "all", "graph": graph},
               "name_scan": {"notes_observed": scanned,
                             "resolution": "exact-vault-relative-path-or-case-sensitive-stem-scalar-title"},
               "unobserved": limits},
        freshness={"observed_at": observed_at, "index": index_header,
                   "selected_source_checks": current},
        records=list(candidates.values()), errors=errors,
        version=VERSION, channels={"named": named_channel, "ranked": ranked},
        index_refresh=refresh, byte_limit=max_bytes)
    encoded = canonical(result) + b"\n"
    if len(encoded) > max_bytes:
        raise ValueError(f"complete nomination manifest exceeds max-bytes ({len(encoded)} > {max_bytes}); no content was truncated")
    return result


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("nominate", help="nominate exact names and independent ranked facets")
    add_vault_argument(command)
    command.add_argument("--text", action="append", default=[])
    command.add_argument("--named", action="append", default=[])
    command.add_argument("--k", type=int, default=10)
    command.add_argument("--graph", choices=("configured", "obsidian", "filesystem", "none"), default="configured")
    command.add_argument("--corpus", choices=("memory", "vault", "all"))
    command.add_argument("--rank", choices=("both", "embedding", "lexical"), default="both")
    command.add_argument("--no-refresh", action="store_true")
    command.add_argument("--max-bytes", type=int, required=True)
    command.add_argument("--out", help="new manifest file; existing files are never replaced")
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    try:
        result = nominate(args.vault, texts=args.text, named=args.named, k=args.k,
                          graph=args.graph, corpus=args.corpus, rank=args.rank,
                          refresh_index=not args.no_refresh, max_bytes=args.max_bytes)
        if args.out:
            output = Path(os.path.abspath(Path(args.out).expanduser()))
            for candidate in (output, *output.parents):
                if candidate.exists() and reparse(candidate):
                    raise ValueError(f"reparse output boundary: {candidate}")
            output.parent.resolve(strict=True)
            with output.open("xb") as stream:
                stream.write(canonical(result) + b"\n")
        else:
            # Emit the identical compact UTF-8 representation whose byte limit
            # was checked, rather than a larger pretty-printed substitute.
            encoded = canonical(result) + b"\n"
            binary = getattr(sys.stdout, "buffer", None)
            if binary is not None:
                binary.write(encoded)
            else:
                sys.stdout.write(encoded.decode("utf-8"))
        return 0
    except (OSError, ValueError) as exc:
        emit_json_utf8({"status": "unavailable", "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
