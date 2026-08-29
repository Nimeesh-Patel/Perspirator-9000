#!/usr/bin/env python3
"""Rank memory knowledge that may deserve a `***` problem note.

Retrieval and structure only: this script ranks, counts, and reports. Which signals run,
their thresholds, which notes are exempt, and the shape of a draft all live in
the vault at memory/perspirator/Candidate Selection.md, so the selection logic
is editable without touching code.
"""

import argparse
import itertools
import json
import re
import sys
from pathlib import Path

CONFIG_NOTE = ("memory", "perspirator", "Candidate Selection.md")

PROBLEMISH = re.compile(r"problem|conflict|criticism|question", re.I)

# One structural parser for the whole toolkit; see note_chunks.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from adapters import add_vault_argument  # noqa: E402
from note_chunks import (all_chunks, bullets, read_note as read,  # noqa: E402
                         section, vault_links)
from neighbour import (content_words, inverse_document_frequency,  # noqa: E402
                       lexical_coverage, load_index)


def load_config(vault):
    """Signals, thresholds, exemptions, and the draft template, from the vault."""
    path = vault.joinpath(*CONFIG_NOTE)
    text = read(path)
    if text is None:
        raise SystemExit(f"STOP: cannot read {path}\n"
                         "The selection logic lives there; this script will not improvise it.")
    signals = {}
    for item in bullets(section(text, "## Signals")):
        name, _, rest = item.partition(":")
        numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", rest)]
        if numbers:
            signals[name.strip().lower()] = numbers
    exempt = {b.strip().lower() for b in bullets(section(text, "## Exempt"))}
    excluded = {b.strip().replace("\\", "/").strip("/").casefold()
                for b in bullets(section(text, "## Excluded folders"))}
    template = ""
    block = re.search(r"```text\n(.*?)```", section(text, "## Draft form"), re.S)
    if block:
        template = block.group(1)
    return signals, exempt, excluded, template



def stated_problems(vault, exempt, excluded=()):
    """(note, statement) for every explicitly stated problem in memory/.

    A filter over chunks, not a parser: any memory chunk sitting under a
    heading that names a problem, conflict, criticism, or question. Because a
    chunk keeps multi-line list items whole, statements are no longer cut at
    their first newline.
    """
    found = []
    for chunk in all_chunks(vault, corpus="memory"):
        note = chunk["note"].replace("\\", "/").casefold()
        if any(note == folder or note.startswith(folder + "/")
               for folder in excluded):
            continue
        if chunk["stem"].lower() in exempt:
            continue
        if not PROBLEMISH.search(" ".join(chunk["heading"])):
            continue
        text = " ".join(chunk["text"].split())
        if len(text) < 40 or "relates_to" in text or "source [[" in text:
            continue
        found.append((chunk["stem"], text))
    return found


def signal_recurrence(vault, exempt, embedding_threshold, lexical_threshold,
                      refresh_index=True, excluded=()):
    """Surface recurring-problem candidates through the shared substrate.

    Embedding and lexical proximity are independent retrieval conjectures. A
    pair survives when either crosses its configured threshold; neither score
    establishes that the statements really express the same problem.
    """
    items = stated_problems(vault, exempt, excluded)
    idf = inverse_document_frequency([text for _, text in items])
    words = {text: content_words(text) for _, text in items}
    loaded = load_index(vault, refresh_index=refresh_index)
    normalized = lambda text: " ".join(text.split())
    slots = {}
    for index, meta in enumerate(loaded["meta"]):
        slots.setdefault((meta["stem"], normalized(meta["text"])), index)

    hits = []
    vectors = loaded["vectors"]
    for (n1, left), (n2, right) in itertools.combinations(items, 2):
        if n1 == n2:
            continue
        left_slot = slots.get((n1, normalized(left)))
        right_slot = slots.get((n2, normalized(right)))
        embedding = None
        if left_slot is not None and right_slot is not None:
            embedding = float(vectors[left_slot] @ vectors[right_slot])
        # Recurrence is symmetric: both statements must substantially cover
        # the other's discriminative vocabulary. Reuse the one lexical scorer
        # instead of retaining a second Jaccard implementation.
        lexical = min(lexical_coverage(words[left], words[right], idf),
                      lexical_coverage(words[right], words[left], idf))
        matched_by = []
        if embedding is not None and embedding >= embedding_threshold:
            matched_by.append("embedding")
        if lexical >= lexical_threshold:
            matched_by.append("lexical")
        if not matched_by:
            continue
        hits.append({
            "signal": "recurrence",
            "embedding_score": round(embedding, 3) if embedding is not None else None,
            "lexical_score": round(lexical, 3),
            "matched_by": matched_by,
            "where": [n1, n2],
            "text": left,
            "also": right,
            "_rank": (len(matched_by),
                      max((embedding or -1) / embedding_threshold,
                          lexical / lexical_threshold)),
        })
    hits.sort(key=lambda hit: hit["_rank"], reverse=True)
    for hit in hits:
        del hit["_rank"]
    return hits


def signal_hub_stub(vault, max_bytes, min_referrers):
    refs, _, notes = vault_links(vault)
    by_stem = {p.stem.lower(): p for p in notes}
    hits = []
    for target, referrers in refs.items():
        path = by_stem.get(target.lower())
        if path is None or len(referrers) < min_referrers:
            continue
        text = read(path)
        if text is None or len(text) >= max_bytes:
            continue
        # A note that already has the separator is already a problem note; being
        # short is then normal, not a gap. Only undeveloped notes are candidates.
        body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)
        if re.search(r"^\*\*\*\s*$", body, re.M):
            continue
        hits.append({"signal": "hub-stub", "score": len(referrers),
                     "where": [path.stem], "text": " ".join(body.split())[:220],
                     "also": f"{len(text)} bytes, {len(referrers)} referrers"})
    hits.sort(key=lambda h: -h["score"])
    return hits


def signal_never_written(vault, min_referrers):
    refs, existing, _ = vault_links(vault)
    # basic-memory writes permalink-style links; they resolve to a real note
    # through its slug, so they are not unwritten concepts.
    def slug(name):
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    slugs = {slug(stem) for stem in existing}
    hits = []
    for target, referrers in refs.items():
        if target.lower() in existing or slug(target) in slugs:
            continue
        if len(referrers) < min_referrers:
            continue
        if re.search(r"\.(png|jpg|jpeg|gif|svg|pdf)$", target, re.I) or "/" in target:
            continue
        hits.append({"signal": "never-written", "score": len(referrers),
                     "where": sorted(p.stem for p in referrers)[:4],
                     "text": target, "also": f"{len(referrers)} referrers, no file"})
    hits.sort(key=lambda h: -h["score"])
    return hits



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_vault_argument(parser)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-refresh", action="store_true",
                        help="use the neighbour index as-is")
    args = parser.parse_args()

    vault = Path(args.vault).expanduser().resolve(strict=False)
    memory = vault / "memory"
    if not memory.is_dir():
        raise SystemExit(f"STOP: no memory directory at {memory}")

    signals, exempt, excluded, template = load_config(vault)
    hits = []
    if "recurrence" in signals:
        thresholds = signals["recurrence"]
        if len(thresholds) < 2 or any(value <= 0 for value in thresholds[:2]):
            raise SystemExit("STOP: recurrence needs positive embedding and lexical "
                             "thresholds in Candidate Selection.md")
        hits += signal_recurrence(vault, exempt, thresholds[0], thresholds[1],
                                  refresh_index=not args.no_refresh,
                                  excluded=excluded)
    if "hub-stub" in signals:
        a, b = signals["hub-stub"][:2]
        hits += signal_hub_stub(vault, int(a), int(b))
    if "never-written" in signals:
        hits += signal_never_written(vault, int(signals["never-written"][0]))

    if args.json:
        print(json.dumps({"signals": list(signals), "exempt": sorted(exempt),
                          "excluded_folders": sorted(excluded),
                          "template": template, "candidates": hits[:args.limit]},
                         indent=2, ensure_ascii=False))
        return 0

    print(f"Candidates from {memory}")
    print(f"  signals: {', '.join(signals) or 'none enabled'}")
    print(f"  exempt:  {', '.join(sorted(exempt)) or 'none'}")
    print(f"  excluded folders: {', '.join(sorted(excluded)) or 'none'}\n")
    if not hits:
        print("  no candidates above the configured thresholds")
    for hit in hits[:args.limit]:
        if hit["signal"] == "recurrence":
            label = (f"recurrence {'+'.join(hit['matched_by'])} "
                     f"embedding={hit['embedding_score']} lexical={hit['lexical_score']}")
        else:
            label = f"{hit['signal']} {hit['score']}"
        print(f"[{label}] {' + '.join(hit['where'])}")
        print(f"    {hit['text'][:150]}")
        if hit.get("also"):
            print(f"    ~ {str(hit['also'])[:150]}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
