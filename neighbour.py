#!/usr/bin/env python3
"""
neighbour.py — distributional neighbours of a piece of text, from the vault.

    neighbour.py index --vault <root> [--out <path>.npz]
    neighbour.py match --vault <root> [--index <path>.npz]
                       (--text "..." | --file <note> | --stdin)
                       [--corpus memory|vault] [--side problem|answer|none]
                       [--folder <prefix>] [--k N] [--json]

This answers ONE question: which chunks are distributionally near this source?

It does not claim that two passages state the same problem, that one belongs
inside the other, that a result is a criticism, an elaboration, or a rival
conjecture, or that any note is the right destination. Those are judgments the
agent makes from the current problem situation.

The mechanism — model, what is indexed, exemptions, index location, provenance
fields — is read from memory/perspirator/Neighbour Retrieval.md. If that note is
missing or not active this stops rather than improvising. `k`, corpus, side, and
folder are query arguments, not settings: recurrence asks for memory-to-memory
neighbours, placement asks for vault destinations, and the substrate does not
know which is intended.

Embedding dependencies (numpy, torch, transformers) are confined to this file.
The rest of the toolkit stays standard-library only.

Never writes to the vault except the index file named in the config note, which
is disposable and regenerable from the Markdown.
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from note_chunks import (bullets, chunks_for_note, extract_links,  # noqa: E402
                         frontmatter_fields, iter_notes, read_note, section,
                         vault_links)

CONFIG_NOTE = ("memory", "perspirator", "Neighbour Retrieval.md")


def load_config(vault):
    """Model, indexed corpora/sides, exemptions, and index path, from the vault."""
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
    for item in bullets(section(text, "## What is indexed")):
        key, _, values = item.partition(":")
        indexed[key.strip().lower()] = [v.strip() for v in values.split(",") if v.strip()]

    exempt = tuple(bullets(section(text, "## Exempt")))
    location = (bullets(section(text, "## Index location")) or [None])[0]
    if not location:
        raise SystemExit(f"STOP: {path} names no index location")
    # Path normalises separators; no manual slash juggling.
    location = Path(location.replace("<vault>", str(Path(vault))))
    config = {"model": model, "corpora": indexed.get("corpus", []),
              "sides": indexed.get("side", []), "exempt": exempt,
              "index": location, "note": path}
    # Anything that changes WHAT is indexed invalidates the whole index, not
    # just some chunks. Model changes are reported separately and never
    # silently re-embedded.
    config["shape"] = json.dumps({"corpora": sorted(config["corpora"]),
                                  "sides": sorted(config["sides"]),
                                  "exempt": sorted(exempt)}, sort_keys=True)
    return config


def chunk_id(chunk):
    """Stable identity: content plus where it sits."""
    raw = f"{chunk['note']}|{chunk['start']}|{chunk['text']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class Embedder:
    """Loaded lazily so `index --help` and config errors need no model."""

    def __init__(self, model_name):
        import torch
        from transformers import AutoTokenizer, AutoModel
        self.torch = torch
        self.tok = load_pretrained(AutoTokenizer, model_name, "tokenizer")
        self.mod = load_pretrained(AutoModel, model_name, "model").eval()

    def __call__(self, texts, batch=32):
        import numpy as np
        out = []
        with self.torch.no_grad():
            for i in range(0, len(texts), batch):
                enc = self.tok(texts[i:i + batch], padding=True, truncation=True,
                               max_length=256, return_tensors="pt")
                hidden = self.mod(**enc).last_hidden_state
                mask = enc["attention_mask"].unsqueeze(-1).float()
                pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
                out.append(self.torch.nn.functional.normalize(pooled, dim=1).numpy())
        return np.vstack(out) if out else np.zeros((0, self.mod.config.hidden_size),
                                                   dtype="float32")


def load_pretrained(factory, model_name, component):
    """Use a complete local cache without a Hub request; download on cache miss."""
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
    corpora, sides = config["corpora"], config["sides"]
    return ((not corpora or chunk["corpus"] in corpora)
            and (not sides or chunk["side"] in sides))


def note_stamps(vault, config):
    """rel-path -> [mtime, size] for every indexable note. Cheap: stat only."""
    stamps = {}
    for path in iter_notes(vault, config["exempt"] or None):
        stat = path.stat()
        stamps[path.relative_to(vault).as_posix()] = [int(stat.st_mtime), stat.st_size]
    return stamps


def refresh(vault, config, out, rebuild=False, embedder=None):
    """Bring the index level with the vault. Returns a short report.

    Freshness is not scheduled and not the user's problem. A note is re-read
    only when its mtime or size changed, a chunk is re-embedded only when its
    text changed, and deleted notes drop out. Everything else is reused.
    """
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
                f"comparable; re-run `index --rebuild`.")
        if header.get("shape") != config["shape"]:
            raise SystemExit(
                f"STOP: {out} was built for a different set of chunks (corpus/side/"
                f"exempt changed in {config['note'].name}); re-run `index --rebuild`.")
        old_meta = json.loads(str(old["meta"]))
        old_stamps = header.get("notes", {})
        for cid, vec in zip(json.loads(str(old["ids"])), old["vectors"]):
            known_vectors[cid] = vec

    changed = {rel for rel, stamp in stamps.items() if old_stamps.get(rel) != stamp}
    removed = set(old_stamps) - set(stamps)

    chunks = [m for m in old_meta if m["note"] not in changed and m["note"] not in removed]
    for rel in sorted(changed):
        for chunk in chunks_for_note(vault / rel, vault):
            if keep_chunk(chunk, config):
                chunks.append(chunk)

    ids = [chunk_id(c) for c in chunks]
    missing = [i for i, cid in enumerate(ids) if cid not in known_vectors]
    started = time.time()
    if missing:
        embedder = embedder or Embedder(config["model"])
        vectors = embedder([chunks[i]["text"] for i in missing])
        for slot, i in enumerate(missing):
            known_vectors[ids[i]] = vectors[slot]
    elapsed = time.time() - started

    matrix = (np.vstack([known_vectors[cid] for cid in ids]) if ids
              else np.zeros((0, 384), "float32"))
    meta = [{k: c[k] for k in ("note", "stem", "heading", "start", "end",
                               "side", "corpus", "links", "text")} for c in chunks]
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out, vectors=matrix.astype("float32"),
             ids=json.dumps(ids), meta=json.dumps(meta, ensure_ascii=False),
             header=json.dumps({"model": config["model"], "shape": config["shape"],
                                "dim": int(matrix.shape[1]) if len(matrix) else 0,
                                "chunks": len(ids), "notes": stamps,
                                "built": time.strftime("%Y-%m-%d %H:%M")}))
    return {"chunks": len(ids), "embedded": len(missing), "reused": len(ids) - len(missing),
            "notes_changed": len(changed), "notes_removed": len(removed),
            "seconds": round(elapsed, 1), "path": out}


def cmd_index(args):
    vault = Path(args.vault).expanduser().resolve()
    config = load_config(vault)
    out = Path(args.out).expanduser() if args.out else config["index"]
    report = refresh(vault, config, out, rebuild=args.rebuild)
    size = out.stat().st_size / 1e6
    print(f"indexed {report['chunks']} chunks "
          f"({report['embedded']} embedded, {report['reused']} reused)")
    print(f"  {report['notes_changed']} notes changed, {report['notes_removed']} removed")
    print(f"  model {config['model']}")
    print(f"  {out}  {size:.1f} MB  embed {report['seconds']}s")
    return 0


def cmd_match(args):
    import numpy as np
    vault = Path(args.vault).expanduser().resolve()
    config = load_config(vault)
    index = Path(args.index).expanduser() if args.index else config["index"]
    if not index.is_file():
        raise SystemExit(f"STOP: no index at {index}. Run `neighbour.py index` first.")

    stale = None
    if not args.no_refresh:
        stale = refresh(vault, config, index)
        if stale["notes_changed"] or stale["notes_removed"]:
            print(f"refreshed: {stale['notes_changed']} notes changed, "
                  f"{stale['notes_removed']} removed, {stale['embedded']} chunks "
                  f"re-embedded ({stale['seconds']}s)", file=sys.stderr)

    data = np.load(index, allow_pickle=False)
    header = json.loads(str(data["header"]))
    if header.get("model") != config["model"]:
        raise SystemExit(f"STOP: index model {header.get('model')!r} != config "
                         f"{config['model']!r}; rebuild the index.")
    meta = json.loads(str(data["meta"]))
    vectors = data["vectors"]

    source_note = None
    if args.file:
        source = Path(args.file).expanduser()
        text = read_note(source)
        if text is None:
            raise SystemExit(f"STOP: cannot read {source}")
        try:
            source_note = source.resolve().relative_to(vault).as_posix()
        except ValueError:
            source_note = None
    elif args.stdin:
        text = sys.stdin.read()
    else:
        text = args.text
    if not (text or "").strip():
        raise SystemExit("STOP: empty query text")

    query = Embedder(config["model"])([text])[0]
    scores = vectors @ query

    keep = []
    for i, m in enumerate(meta):
        if args.corpus not in (None, "all") and m["corpus"] != args.corpus:
            continue
        if args.side not in (None, "all") and m["side"] != args.side:
            continue
        if args.folder and not m["note"].startswith(args.folder):
            continue
        if source_note and m["note"] == source_note:
            continue
        keep.append(i)
    keep.sort(key=lambda i: -scores[i])
    keep = keep[:args.k]

    refs, _, _ = vault_links(vault, excludes=config["exempt"] or None)
    src_stem = Path(source_note).stem if source_note else None
    src_referrers = {p.stem for p in refs.get(src_stem, set())} if src_stem else set()
    src_links = set()
    if source_note:
        src_links = {t.split("/")[-1]
                     for t in extract_links(read_note(vault / source_note) or "")}

    results = []
    for rank, i in enumerate(keep, 1):
        m = meta[i]
        dest_referrers = {p.stem for p in refs.get(m["stem"], set())}
        results.append({
            "rank": rank,
            "score": round(float(scores[i]), 2),
            "note": m["note"], "heading": m["heading"], "side": m["side"],
            "corpus": m["corpus"],
            "already_links": bool(src_stem) and (m["stem"] in src_links
                                                 or src_stem in set(m["links"])),
            "shares_referrers": sorted(src_referrers & dest_referrers)[:5],
            "snippet": " ".join(m["text"].split())[:220],
        })

    if args.json:
        print(json.dumps({"query_note": source_note, "header": header,
                          "results": results}, ensure_ascii=False, indent=1))
        return 0
    print(f"{len(results)} neighbours  (index {header['chunks']} chunks, "
          f"model {header['model']})")
    for r in results:
        where = " > ".join(r["heading"]) or "(no heading)"
        print(f"\n[{r['rank']:>2}] {r['score']:.2f}  {r['note']} :: {where}")
        print(f"     {r['corpus']}/{r['side']}"
              + ("  already-links" if r["already_links"] else "")
              + (f"  shares-referrers: {', '.join(r['shares_referrers'])}"
                 if r["shares_referrers"] else ""))
        print(f"     {r['snippet'][:170]}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_index = sub.add_parser("index", help="embed vault chunks into a disposable index")
    p_index.add_argument("--vault", default=str(Path.home() / "nimeesh vault"))
    p_index.add_argument("--out")
    p_index.add_argument("--rebuild", action="store_true")
    p_index.set_defaults(func=cmd_index)

    p_match = sub.add_parser("match", help="rank chunks near a piece of text")
    p_match.add_argument("--vault", default=str(Path.home() / "nimeesh vault"))
    p_match.add_argument("--index")
    group = p_match.add_mutually_exclusive_group(required=True)
    group.add_argument("--text")
    group.add_argument("--file")
    group.add_argument("--stdin", action="store_true")
    p_match.add_argument("--corpus", choices=("memory", "vault", "all"),
                         help="restrict corpus; 'all' is the explicit no-filter value")
    p_match.add_argument("--side", choices=("problem", "answer", "none", "all"),
                         help="restrict note side; 'all' is the explicit no-filter value")
    p_match.add_argument("--folder")
    p_match.add_argument("--k", type=int, default=10)
    p_match.add_argument("--no-refresh", action="store_true",
                         help="query the index as-is, without checking the vault")
    p_match.add_argument("--json", action="store_true")
    p_match.set_defaults(func=cmd_match)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
