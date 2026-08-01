#!/usr/bin/env python3
"""Distributional neighbours with inspectable units and graph context.

Embeddings propose nearby retrieval units. They do not decide semantic identity,
placement, criticism, or redundancy. Unit formation and graph providers are
selected by the active vault configuration, while this file owns the mechanism.
"""

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from note_chunks import (bullets, chunks_for_note, extract_links,  # noqa: E402
                         frontmatter_fields, iter_notes, read_note, section,
                         vault_links)
from obsidian_cli import ObsidianCLI  # noqa: E402

CONFIG_NOTE = ("memory", "perspirator", "Neighbour Retrieval.md")

LEXICAL_STOPWORDS = set("""
a an and are as at be because been before being between both but by can could did do does
doing done each even for from further had has have having how if in into is it its more most
no nor not of off on once only or other our out over own same should so some such than that
the their them then there these they this those through to too under until up very was were
what when where which while who whom why will with within without would yet you your
problem problems note notes vault agent current remains remain rather still must may might
""".split())

FORMATION = {
    "problem": "identity-and-contextual-conjecture",
    "non-problem": "authored-blocks",
    "oversize": "authored-boundaries-then-token-windows",
    "embedding-oversize": "mean-pooled-token-windows",
}


def lexical_overlap(left, right):
    """Cheap independent signal; never a semantic-identity test."""
    def tokens(text):
        return {word for word in re.findall(r"[a-z][a-z-]{3,}", text.lower())
                if word not in LEXICAL_STOPWORDS}
    a, b = tokens(left), tokens(right)
    if len(a) < 4 or len(b) < 4:
        return 0.0
    return len(a & b) / len(a | b)


def keyed_bullets(text, heading):
    out = {}
    for item in bullets(section(text, heading)):
        key, sep, value = item.partition(":")
        if sep:
            out[key.strip().lower()] = value.strip()
    return out


def required_int(values, key, note, minimum=1):
    try:
        value = int(values[key])
    except (KeyError, ValueError):
        raise SystemExit(f"STOP: {note} needs integer '{key}'") from None
    if value < minimum:
        raise SystemExit(f"STOP: {note} needs '{key}' >= {minimum}")
    return value


def load_config(vault):
    """Load and validate the Markdown-owned retrieval selections."""
    path = Path(vault).joinpath(*CONFIG_NOTE)
    text = read_note(path)
    if text is None:
        raise SystemExit(f"STOP: cannot read {path}\n"
                         "The retrieval mechanism is defined there; this will not improvise it.")
    if frontmatter_fields(text).get("status") != "active":
        raise SystemExit(f"STOP: {path} is not status: active")

    model = (bullets(section(text, "## Model")) or [None])[0]
    if not model:
        raise SystemExit(f"STOP: {path} names no model under '## Model'")

    indexed = {}
    for key, value in keyed_bullets(text, "## What is indexed").items():
        indexed[key] = [part.strip() for part in value.split(",") if part.strip()]

    formation = keyed_bullets(text, "## Unit formation")
    for key, expected in FORMATION.items():
        if formation.get(key) != expected:
            raise SystemExit(f"STOP: {path} must select '{key}: {expected}' under "
                             "'## Unit formation'")
    max_tokens = required_int(formation, "max-tokens", path, minimum=16)

    graph = keyed_bullets(text, "## Graph expansion")
    if graph.get("provider") not in ("obsidian", "filesystem"):
        raise SystemExit(f"STOP: {path} graph provider must be obsidian or filesystem")
    if graph.get("fallback") not in ("filesystem", "none"):
        raise SystemExit(f"STOP: {path} graph fallback must be filesystem or none")
    graph_config = {
        "provider": graph["provider"], "fallback": graph["fallback"],
        "limit": required_int(graph, "limit", path),
        "timeout": required_int(graph, "timeout-seconds", path),
    }

    exempt = tuple(bullets(section(text, "## Exempt")))
    location = (bullets(section(text, "## Index location")) or [None])[0]
    if not location:
        raise SystemExit(f"STOP: {path} names no index location")
    location = Path(location.replace("<vault>", str(Path(vault))))
    config = {
        "model": model,
        "corpora": indexed.get("corpus", []),
        "sides": indexed.get("side", []),
        "units": indexed.get("unit", []),
        "formation": {**FORMATION, "max-tokens": max_tokens},
        "graph": graph_config,
        "exempt": exempt, "index": location, "note": path,
    }
    config["shape"] = json.dumps({
        "corpora": sorted(config["corpora"]),
        "sides": sorted(config["sides"]),
        "units": sorted(config["units"]),
        "formation": config["formation"],
        "exempt": sorted(exempt),
    }, sort_keys=True)
    return config


def chunk_id(chunk):
    """Stable identity includes formation and embedded context."""
    raw = "|".join((chunk["note"], str(chunk["start"]), chunk["unit"],
                    chunk["strategy"], chunk["embedding_text"]))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class Embedder:
    """Loaded lazily so help/config failures need no model."""
    def __init__(self, model_name, max_tokens=256):
        import torch
        from transformers import AutoTokenizer, AutoModel
        self.torch = torch
        self.max_tokens = max_tokens
        self.tok = load_pretrained(AutoTokenizer, model_name, "tokenizer")
        self.mod = load_pretrained(AutoModel, model_name, "model").eval()

    def token_length(self, text):
        encoded = self.tok(text, add_special_tokens=True, truncation=False,
                           verbose=False)
        ids = encoded["input_ids"]
        return len(ids[0] if ids and isinstance(ids[0], list) else ids)

    def __call__(self, texts, batch=32):
        """Embed every token window, then pool windows back to logical units."""
        import numpy as np
        out = []
        with self.torch.no_grad():
            for i in range(0, len(texts), batch):
                current = texts[i:i + batch]
                enc = self.tok(
                    current, padding=True, truncation=True,
                    max_length=self.max_tokens, return_overflowing_tokens=True,
                    return_tensors="pt", verbose=False)
                mapping = enc.pop("overflow_to_sample_mapping")
                hidden = self.mod(**enc).last_hidden_state
                mask = enc["attention_mask"].unsqueeze(-1).float()
                token_sums = (hidden * mask).sum(1)
                token_counts = mask.sum(1)
                pooled = []
                for source_index in range(len(current)):
                    selected = mapping == source_index
                    total = token_sums[selected].sum(0)
                    count = token_counts[selected].sum(0).clamp(min=1e-9)
                    pooled.append(total / count)
                stacked = self.torch.stack(pooled)
                out.append(self.torch.nn.functional.normalize(stacked, dim=1).numpy())
        return np.vstack(out) if out else np.zeros((0, self.mod.config.hidden_size),
                                                   dtype="float32")

def load_pretrained(factory, model_name, component):
    """Use a complete local cache without a Hub request; download on miss."""
    try:
        return factory.from_pretrained(model_name, local_files_only=True)
    except (OSError, ValueError) as local_error:
        try:
            return factory.from_pretrained(model_name)
        except Exception as download_error:
            local_detail = " ".join(str(local_error).split())[:300]
            download_detail = " ".join(str(download_error).split())[:300]
            raise SystemExit(
                f"STOP: embedding {component} {model_name!r} is unavailable from "
                "the local Hugging Face cache and could not be downloaded.\n"
                f"  local: {local_detail}\n"
                f"  download: {type(download_error).__name__}: {download_detail}"
            ) from None


def keep_chunk(chunk, config):
    return ((not config["corpora"] or chunk["corpus"] in config["corpora"])
            and (not config["sides"] or chunk["side"] in config["sides"])
            and (not config["units"] or chunk["unit"] in config["units"]))


def note_stamps(vault, config):
    stamps = {}
    for path in iter_notes(vault, config["exempt"] or None):
        stat = path.stat()
        stamps[path.relative_to(vault).as_posix()] = [int(stat.st_mtime), stat.st_size]
    return stamps


def formation_args(config, embedder):
    form = config["formation"]
    return {
        "max_tokens": form["max-tokens"],
        "token_length": embedder.token_length,
        "problem_strategy": form["problem"],
        "nonproblem_strategy": form["non-problem"],
        "oversize_strategy": form["oversize"],
    }


def refresh(vault, config, out, rebuild=False, embedder=None):
    """Incrementally level the disposable index with the vault."""
    import numpy as np

    stamps = note_stamps(vault, config)
    known_vectors, old_meta, old_stamps = {}, [], {}
    if out.is_file() and not rebuild:
        old = np.load(out, allow_pickle=False)
        header = json.loads(str(old["header"]))
        if header.get("model") != config["model"]:
            raise SystemExit(
                f"STOP: {out} was built with {header.get('model')!r}, config says "
                f"{config['model']!r}. Vectors from different models are not "
                "comparable; re-run `index --rebuild`.")
        if header.get("shape") != config["shape"]:
            raise SystemExit(
                f"STOP: {out} was built for a different retrieval-unit shape; "
                "re-run `index --rebuild`.")
        old_meta = json.loads(str(old["meta"]))
        old_stamps = header.get("notes", {})
        for cid, vector in zip(json.loads(str(old["ids"])), old["vectors"]):
            known_vectors[cid] = vector

    changed = {rel for rel, stamp in stamps.items() if old_stamps.get(rel) != stamp}
    removed = set(old_stamps) - set(stamps)
    chunks = [m for m in old_meta if m["note"] not in changed and m["note"] not in removed]

    if changed:
        embedder = embedder or Embedder(config["model"], config["formation"]["max-tokens"])
        options = formation_args(config, embedder)
        for rel in sorted(changed):
            for chunk in chunks_for_note(vault / rel, vault, **options):
                if keep_chunk(chunk, config):
                    chunks.append(chunk)

    ids = [chunk_id(chunk) for chunk in chunks]
    missing = [i for i, cid in enumerate(ids) if cid not in known_vectors]
    started = time.time()
    if missing:
        embedder = embedder or Embedder(config["model"], config["formation"]["max-tokens"])
        vectors = embedder([chunks[i]["embedding_text"] for i in missing])
        for slot, index in enumerate(missing):
            known_vectors[ids[index]] = vectors[slot]
    elapsed = time.time() - started

    matrix = (np.vstack([known_vectors[cid] for cid in ids]) if ids
              else np.zeros((0, 384), "float32"))
    fields = ("note", "stem", "heading", "start", "end", "side", "unit",
              "strategy", "corpus", "links", "text", "embedding_text")
    meta = [{key: chunk[key] for key in fields} for chunk in chunks]
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out, vectors=matrix.astype("float32"), ids=json.dumps(ids),
             meta=json.dumps(meta, ensure_ascii=False),
             header=json.dumps({
                 "model": config["model"], "shape": config["shape"],
                 "dim": int(matrix.shape[1]) if len(matrix) else 0,
                 "chunks": len(ids), "units": len(ids), "notes": stamps,
                 "built": time.strftime("%Y-%m-%d %H:%M"),
             }))
    return {"chunks": len(ids), "units": len(ids), "embedded": len(missing),
            "reused": len(ids) - len(missing), "notes_changed": len(changed),
            "notes_removed": len(removed), "seconds": round(elapsed, 1), "path": out}


def load_index(vault, index=None, refresh_index=True):
    import numpy as np
    vault = Path(vault).expanduser().resolve()
    config = load_config(vault)
    index = Path(index).expanduser() if index else config["index"]
    if not index.is_file():
        raise SystemExit(f"STOP: no index at {index}. Run `neighbour.py index` first.")
    stale = refresh(vault, config, index) if refresh_index else None
    data = np.load(index, allow_pickle=False)
    header = json.loads(str(data["header"]))
    if header.get("model") != config["model"]:
        raise SystemExit(f"STOP: index model {header.get('model')!r} != config "
                         f"{config['model']!r}; rebuild the index.")
    if header.get("shape") != config["shape"]:
        raise SystemExit("STOP: index retrieval-unit shape differs from config; rebuild it.")
    return {"vault": vault, "config": config, "index": index, "stale": stale,
            "header": header, "meta": json.loads(str(data["meta"])),
            "vectors": data["vectors"]}


def public_header(header):
    """Bounded reader-facing index provenance; freshness stamps stay internal."""
    try:
        shape = json.loads(header.get("shape", "{}"))
    except json.JSONDecodeError:
        shape = {"status": "unreadable"}
    summary = {key: header.get(key)
               for key in ("model", "dim", "units", "built")}
    summary["shape"] = shape
    return summary


def collapse_by_note(indices, meta, scores, limit):
    """One best unit per note; surrounding graph context is expanded later."""
    seen, out = set(), []
    for index in sorted(indices, key=lambda i: -scores[i]):
        note = meta[index]["note"]
        if note in seen:
            continue
        seen.add(note)
        out.append(index)
        if len(out) >= limit:
            break
    return out


def top_note_pairs(indices, meta, vectors, limit):
    '''Rank filtered unit pairs, retaining only the best unit pair per note pair.'''
    best = {}
    for offset, left in enumerate(indices):
        right_indices = indices[offset + 1:]
        if not right_indices:
            continue
        scores = vectors[right_indices] @ vectors[left]
        for right, score in zip(right_indices, scores):
            left_note, right_note = meta[left]['note'], meta[right]['note']
            if left_note == right_note:
                continue
            key = tuple(sorted((left_note, right_note)))
            previous = best.get(key)
            if previous is None or float(score) > previous[0]:
                best[key] = (float(score), left, right)
    ranked = sorted(best.values(), key=lambda item: -item[0])
    return ranked[:limit]


def note_link_stems(vault, note):
    '''Return link targets from the complete note, not only one retrieval unit.'''
    text = read_note(Path(vault) / note) or ''
    return {target.split('/')[-1] for target in extract_links(text)}


def filesystem_context(vault, note, refs, limit):
    path = vault / note
    text = read_note(path) or ""
    stem = path.stem
    return {
        "provider": "filesystem", "status": "ok", "path": note,
        "backlinks": sorted(p.relative_to(vault).as_posix()
                            for p in refs.get(stem, set()))[:limit],
        "links": extract_links(text)[:limit],
        "properties": frontmatter_fields(text),
    }


def expand_graph(vault, results, config, mode, refs, cli=None):
    """Expand selected note paths after ranking; never alter vector scores."""
    if mode == "none":
        return {"provider": "none", "status": "disabled", "notes": []}
    selected = config["graph"]["provider"] if mode == "configured" else mode
    limit = config["graph"]["limit"]
    notes = [result["note"] for result in results[:limit]]
    if selected == "filesystem":
        return {"provider": "filesystem", "status": "ok",
                "notes": [filesystem_context(vault, note, refs, limit) for note in notes]}

    cli = cli or ObsidianCLI(
        vault, timeout=config["graph"]["timeout"], limit=limit)
    contexts = [cli.note_context(note) for note in notes]
    failures = [context for context in contexts if context["status"] != "ok"]
    if not failures:
        return {"provider": "obsidian", "status": "ok", "notes": contexts,
                "capabilities": ["links", "backlinks", "properties", "search",
                                 "search:context", "base:query", "orphans",
                                 "deadends", "unresolved"]}
    if config["graph"]["fallback"] == "filesystem":
        expanded = []
        for note, context in zip(notes, contexts):
            if context["status"] == "ok":
                expanded.append(context)
                continue
            fallback = filesystem_context(vault, note, refs, limit)
            fallback["status"] = "fallback"
            fallback["issue"] = context.get("error")
            expanded.append(fallback)
        some_obsidian = len(failures) < len(contexts)
        return {
            "provider": "mixed" if some_obsidian else "filesystem",
            "status": "partial-fallback" if some_obsidian else "fallback",
            "issues": [{"path": item.get("path"),
                        "status": item.get("status"),
                        "error": item.get("error")} for item in failures],
            "notes": expanded,
        }
    failure = failures[0]
    return {"provider": "obsidian", "status": failure["status"],
            "issue": failure.get("error"), "notes": contexts}


def cmd_index(args):
    vault = Path(args.vault).expanduser().resolve()
    config = load_config(vault)
    out = Path(args.out).expanduser() if args.out else config["index"]
    report = refresh(vault, config, out, rebuild=args.rebuild)
    size = out.stat().st_size / 1e6
    print(f"indexed {report['units']} retrieval units "
          f"({report['embedded']} embedded, {report['reused']} reused)")
    print(f"  {report['notes_changed']} notes changed, {report['notes_removed']} removed")
    print(f"  model {config['model']}  max {config['formation']['max-tokens']} tokens")
    print(f"  {out}  {size:.1f} MB  embed {report['seconds']}s")
    return 0


def match_query(vault, config, meta, vectors, query, query_vector,
                args, refs, cli=None):
    """Rank and expand one validated query using shared loaded resources."""
    source_note = query["note"]
    scores = vectors @ query_vector
    eligible = []
    for index, unit in enumerate(meta):
        if args.corpus not in (None, "all") and unit["corpus"] != args.corpus:
            continue
        if args.side not in (None, "all") and unit["side"] != args.side:
            continue
        if args.unit not in (None, "all") and unit["unit"] != args.unit:
            continue
        if args.folder and not unit["note"].startswith(args.folder):
            continue
        if source_note and unit["note"] == source_note:
            continue
        eligible.append(index)
    kept = collapse_by_note(eligible, meta, scores, args.k)

    src_stem = Path(source_note).stem if source_note else None
    src_referrers = {p.stem for p in refs.get(src_stem, set())} if src_stem else set()
    src_links = note_link_stems(vault, source_note) if source_note else set()
    results = []
    for rank, index in enumerate(kept, 1):
        unit = meta[index]
        dest_referrers = {p.stem for p in refs.get(unit["stem"], set())}
        results.append({
            "rank": rank, "score": round(float(scores[index]), 2),
            "note": unit["note"], "heading": unit["heading"],
            "side": unit["side"], "unit": unit["unit"],
            "strategy": unit["strategy"], "corpus": unit["corpus"],
            "already_links": bool(src_stem) and (
                unit["stem"] in src_links
                or src_stem in note_link_stems(vault, unit["note"])),
            "shares_referrers": sorted(src_referrers & dest_referrers)[:5],
            "snippet": " ".join(unit["text"].split())[:220],
        })
    graph = expand_graph(vault, results, config, args.graph, refs, cli=cli)
    return {"query_note": source_note, "results": results, "graph": graph}


def print_match(result, header):
    """Render one match result while keeping JSON and text assembly separate."""
    graph, results = result["graph"], result["results"]
    print(f"{len(results)} neighbours  (index {header['chunks']} units, "
          f"model {header['model']}; graph {graph['provider']}/{graph['status']})")
    for item in results:
        where = " > ".join(item["heading"]) or "(no heading)"
        print(f"\n[{item['rank']:>2}] {item['score']:.2f}  "
              f"{item['note']} :: {where}")
        print(f"     {item['corpus']}/{item['side']}/{item['unit']} "
              f"[{item['strategy']}]"
              + ("  already-links" if item["already_links"] else "")
              + (f"  shares-referrers: {', '.join(item['shares_referrers'])}"
                 if item["shares_referrers"] else ""))
        print(f"     {item['snippet'][:170]}")


def cmd_match(args):
    loaded = load_index(args.vault, index=args.index,
                        refresh_index=not args.no_refresh)
    vault, config = loaded["vault"], loaded["config"]
    stale = loaded["stale"]
    if stale and (stale["notes_changed"] or stale["notes_removed"]):
        print(f"refreshed: {stale['notes_changed']} notes changed, "
              f"{stale['notes_removed']} removed, {stale['embedded']} units "
              f"re-embedded ({stale['seconds']}s)", file=sys.stderr)
    header, meta, vectors = loaded["header"], loaded["meta"], loaded["vectors"]

    queries = []
    if args.file:
        for value in args.file:
            source = Path(value).expanduser()
            text = read_note(source)
            if text is None:
                raise SystemExit(f"STOP: cannot read {source}")
            try:
                source_note = source.resolve().relative_to(vault).as_posix()
            except ValueError:
                source_note = None
            queries.append({"text": text, "note": source_note,
                            "file": str(source)})
    elif args.stdin:
        queries.append({"text": sys.stdin.read(), "note": None, "file": None})
    else:
        for value in args.text:
            queries.append({"text": value, "note": None, "file": None})
    empty = next((query for query in queries
                  if not (query["text"] or "").strip()), None)
    if empty:
        suffix = f" in {empty['file']}" if empty["file"] else ""
        raise SystemExit(f"STOP: empty query text{suffix}")

    embedder = Embedder(config["model"], config["formation"]["max-tokens"])
    query_vectors = embedder([query["text"] for query in queries])
    refs, _, _ = vault_links(vault, excludes=config["exempt"] or None)
    selected = (config["graph"]["provider"]
                if args.graph == "configured" else args.graph)
    cli = (ObsidianCLI(vault, timeout=config["graph"]["timeout"],
                       limit=config["graph"]["limit"])
           if selected == "obsidian" else None)
    matches = [match_query(vault, config, meta, vectors, query, vector,
                           args, refs, cli=cli)
               for query, vector in zip(queries, query_vectors)]

    if args.json:
        if len(matches) == 1:
            payload = {"query_note": matches[0]["query_note"],
                       "header": public_header(header),
                       "results": matches[0]["results"],
                       "graph": matches[0]["graph"]}
        else:
            payload = {"header": public_header(header), "queries": matches}
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0
    for offset, result in enumerate(matches):
        if len(matches) > 1:
            if offset:
                print()
            label = queries[offset]["file"] or f"text query {offset + 1}"
            print(f"===== {label} =====")
        print_match(result, header)
    return 0


def cmd_pairs(args):
    '''Nominate mutually near notes without making a same-problem judgment.'''
    loaded = load_index(args.vault, index=args.index,
                        refresh_index=not args.no_refresh)
    stale = loaded['stale']
    if stale and (stale['notes_changed'] or stale['notes_removed']):
        print('refreshed: {} notes changed, {} removed, {} units '
              're-embedded ({}s)'.format(
                  stale['notes_changed'], stale['notes_removed'],
                  stale['embedded'], stale['seconds']), file=sys.stderr)
    vault = loaded['vault']
    meta, vectors = loaded['meta'], loaded['vectors']
    eligible = []
    for index, unit in enumerate(meta):
        if args.corpus != 'all' and unit['corpus'] != args.corpus:
            continue
        if args.side != 'all' and unit['side'] != args.side:
            continue
        if args.unit != 'all' and unit['unit'] != args.unit:
            continue
        if args.folder and not unit['note'].startswith(args.folder):
            continue
        eligible.append(index)
    if args.k < 1:
        raise SystemExit('STOP: pairs --k must be at least 1')
    if len(eligible) > args.max_units:
        raise SystemExit(
            'STOP: pairs selected {} units, above --max-units {}. '
            'Narrow corpus/side/unit/folder or raise the explicit bound.'.format(
                len(eligible), args.max_units))

    refs, _, _ = vault_links(vault, excludes=loaded['config']['exempt'] or None)
    results = []
    ranked = top_note_pairs(eligible, meta, vectors, args.k)
    for rank, (score, left_index, right_index) in enumerate(ranked, 1):
        left, right = meta[left_index], meta[right_index]
        left_refs = {path.stem for path in refs.get(left['stem'], set())}
        right_refs = {path.stem for path in refs.get(right['stem'], set())}
        left_links = note_link_stems(vault, left['note'])
        right_links = note_link_stems(vault, right['note'])
        results.append({
            'rank': rank,
            'score': round(score, 3),
            'left': {
                'note': left['note'], 'heading': left['heading'],
                'side': left['side'], 'unit': left['unit'],
                'strategy': left['strategy'],
                'snippet': ' '.join(left['text'].split())[:220],
            },
            'right': {
                'note': right['note'], 'heading': right['heading'],
                'side': right['side'], 'unit': right['unit'],
                'strategy': right['strategy'],
                'snippet': ' '.join(right['text'].split())[:220],
            },
            'already_links': (right['stem'] in left_links
                              or left['stem'] in right_links),
            'shares_referrers': sorted(left_refs & right_refs)[:5],
            'left_backlinks': len(refs.get(left['stem'], set())),
            'right_backlinks': len(refs.get(right['stem'], set())),
        })

    payload = {
        'header': public_header(loaded['header']),
        'selection': {
            'corpus': args.corpus, 'side': args.side, 'unit': args.unit,
            'folder': args.folder, 'eligible_units': len(eligible),
            'max_units': args.max_units,
        },
        'relation_evidence': {'provider': 'filesystem', 'status': 'ok'},
        'results': results,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0
    print('{} candidate pairs from {} units '
          '(corpus {}, side {}, unit {})'.format(
              len(results), len(eligible), args.corpus, args.side, args.unit))
    for result in results:
        suffix = ''
        if result['already_links']:
            suffix += '  already-links'
        if result['shares_referrers']:
            suffix += '  shares-referrers: {}'.format(
                ', '.join(result['shares_referrers']))
        suffix += '  backlinks: {}/{}'.format(
            result['left_backlinks'], result['right_backlinks'])
        print('\n[{:>2}] {:.3f}  {}  <->  {}{}'.format(
            result['rank'], result['score'], result['left']['note'],
            result['right']['note'], suffix))
        print('     L: {}'.format(result['left']['snippet'][:150]))
        print('     R: {}'.format(result['right']['snippet'][:150]))
    return 0


def main():
    # Vault text is Unicode; do not let a legacy Windows console code page
    # turn a completed retrieval into an output-time failure.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_index = sub.add_parser("index", help="embed retrieval units into a disposable index")
    p_index.add_argument("--vault", default=str(Path.home() / "nimeesh vault"))
    p_index.add_argument("--out")
    p_index.add_argument("--rebuild", action="store_true")
    p_index.set_defaults(func=cmd_index)

    p_match = sub.add_parser("match", help="rank units, collapse notes, expand context")
    p_match.add_argument("--vault", default=str(Path.home() / "nimeesh vault"))
    p_match.add_argument("--index")
    group = p_match.add_mutually_exclusive_group(required=True)
    group.add_argument("--text", action="append", metavar="TEXT",
                       help="query text; repeat to reuse one index/model session")
    group.add_argument("--file", action="append", metavar="PATH",
                       help="query a note; repeat to reuse one index/model session")
    group.add_argument("--stdin", action="store_true")
    p_match.add_argument("--corpus", choices=("memory", "vault", "all"))
    p_match.add_argument("--side", choices=("problem", "answer", "none", "all"))
    p_match.add_argument("--unit", choices=("problem_identity", "conjecture", "block", "all"))
    p_match.add_argument("--folder")
    p_match.add_argument("--k", type=int, default=10)
    p_match.add_argument("--graph", choices=("configured", "obsidian", "filesystem", "none"),
                         default="configured")
    p_match.add_argument("--no-refresh", action="store_true")
    p_match.add_argument("--json", action="store_true")
    p_match.set_defaults(func=cmd_match)
    p_pairs = sub.add_parser(
        'pairs', help='rank filtered note pairs; make no identity judgment')
    p_pairs.add_argument('--vault', default=str(Path.home() / 'nimeesh vault'))
    p_pairs.add_argument('--index')
    p_pairs.add_argument('--corpus', choices=('memory', 'vault', 'all'),
                         default='vault')
    p_pairs.add_argument('--side', choices=('problem', 'answer', 'none', 'all'),
                         default='problem')
    p_pairs.add_argument(
        '--unit', choices=('problem_identity', 'conjecture', 'block', 'all'),
        default='problem_identity')
    p_pairs.add_argument('--folder')
    p_pairs.add_argument('--k', type=int, default=50)
    p_pairs.add_argument('--max-units', type=int, default=1000)
    p_pairs.add_argument('--no-refresh', action='store_true')
    p_pairs.add_argument('--json', action='store_true')
    p_pairs.set_defaults(func=cmd_pairs)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
