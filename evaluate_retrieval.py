#!/usr/bin/env python3
"""Measure whether retrieval recovers relations the vault author already recorded.

The standing question is whether this index actually finds related knowledge,
and it has never been answered: `Neighbour Retrieval.md` says "whether it is
semantic is what the evaluations test", and the only evaluation ever run was
43 hand-judged pairs whose labels now sit misfiled in an unrelated note.

Hand-labelling does not scale with the vault, so this uses ground truth the
vault already contains. A `[[wikilink]]` between two notes is a relation the
author asserted, so a ranker that cannot surface a note's own linked notes is
not finding related knowledge — whatever its scores look like.

What this measures and does not:

- **Recall is meaningful.** A linked pair is related by the author's judgment,
  so failing to retrieve it is a real miss.
- **Precision is not measurable this way.** An unlinked result may be a genuine
  relation not yet noticed, which is the tool's whole purpose. Unlinked hits
  are therefore reported, never counted as errors.
- **Links are biased toward relations already noticed.** High recall means the
  ranker agrees with what is already known; it says nothing about the
  unnoticed relations that matter most. This is a floor, not a ceiling.

Examples:
    python evaluate_retrieval.py --vault "<vault>"
    python evaluate_retrieval.py --vault "<vault>" --k 25 --sample 120
"""

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adapters import add_vault_argument  # noqa: E402
from note_chunks import extract_links, iter_notes, read_note  # noqa: E402
from neighbour import (Embedder, collapse_by_note, content_words,  # noqa: E402
                       inverse_document_frequency, lexical_coverage,
                       load_config, load_index, merge_rankers)
from problem_half import parse_note  # noqa: E402


def authored_relations(vault, exempt):
    """note stem -> stems it links to, for notes that state a problem."""
    stems = {}
    for path in iter_notes(vault, exempt):
        text = read_note(path)
        if text is None:
            continue
        parsed = parse_note(text)
        if not parsed["has_separator"] or not parsed["problem"]:
            continue
        stems[path.stem.lower()] = {
            "path": path.relative_to(vault).as_posix(),
            "query": " ".join((path.stem + " " + parsed["problem"]).split()),
            "links": {t.split("/")[-1].split("#")[0].strip().lower()
                      for t in extract_links(text)},
        }
    known = set(stems)
    for record in stems.values():
        record["links"] &= known
        record["links"].discard(record["path"].lower())
    return {stem: rec for stem, rec in stems.items() if rec["links"]}


def evaluate(vault, k, sample, seed):
    config = load_config(vault)
    loaded = load_index(vault, refresh_index=False)
    meta, vectors = loaded["meta"], loaded["vectors"]
    records = authored_relations(vault, config["exempt"])
    keys = sorted(records)
    if sample and sample < len(keys):
        random.Random(seed).shuffle(keys)
        keys = keys[:sample]

    idf = inverse_document_frequency([unit["text"] for unit in meta])
    words = [content_words(unit["text"]) for unit in meta]
    embedder = Embedder(config["model"], config["formation"]["max-tokens"])

    modes = ("embedding", "lexical", "both")
    found = {mode: 0 for mode in modes}
    unlinked = {mode: 0 for mode in modes}
    total_links = 0

    stem_of = [Path(unit["note"]).stem.lower() for unit in meta]
    for position, key in enumerate(keys):
        record = records[key]
        query_vector = embedder([record["query"]])[0]
        cosine = vectors @ query_vector
        eligible = [i for i, unit in enumerate(meta)
                    if unit["corpus"] == "vault" and stem_of[i] != key]
        query_words = content_words(record["query"])
        lexical = {}
        for index in eligible:
            base = lexical_coverage(query_words, words[index], idf)
            lexical[index] = base + 1e-6 * float(cosine[index]) if base else 0.0
        total_links += len(record["links"])
        for mode in modes:
            order, _ = merge_rankers(eligible, meta, cosine, lexical, k, mode)
            hits = {stem_of[i] for i in order}
            found[mode] += len(record["links"] & hits)
            unlinked[mode] += len(hits - record["links"])
        if (position + 1) % 25 == 0:
            print(f"  ...{position + 1}/{len(keys)}", file=sys.stderr)

    return {"notes": len(keys), "links": total_links, "k": k,
            "found": found, "unlinked": unlinked}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    add_vault_argument(parser)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--sample", type=int, default=100)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    report = evaluate(Path(args.vault).expanduser().resolve(),
                      args.k, args.sample, args.seed)
    print(f"\n{report['notes']} problem notes, {report['links']} authored links, "
          f"top-{report['k']}\n")
    print(f"{'ranker':<12}{'links recovered':>18}{'recall':>9}"
          f"{'unlinked shown':>16}")
    for mode in ("embedding", "lexical", "both"):
        hit = report["found"][mode]
        recall = hit / report["links"] if report["links"] else 0.0
        print(f"{mode:<12}{hit:>18}{recall:>9.1%}{report['unlinked'][mode]:>16}")
    print("\nUnlinked results are not errors: an unlinked neighbour may be a "
          "relation\nnot yet noticed, which is what this index exists to "
          "surface.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
