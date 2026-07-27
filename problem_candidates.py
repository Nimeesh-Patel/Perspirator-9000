#!/usr/bin/env python3
"""Rank memory knowledge that may deserve a `***` problem note.

Structural only: this script counts, compares, and reports. Which signals run,
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

STOPWORDS = set("""
a an and are as at be because been before being between both but by can could did do does
doing done each even for from further had has have having how if in into is it its more most
no nor not of off on once only or other our out over own same should so some such than that
the their them then there these they this those through to too under until up very was were
what when where which while who whom why will with within without would yet you your
problem problems note notes vault agent current remains remain rather still must may might
""".split())

PROBLEMISH = re.compile(r"problem|conflict|criticism|question", re.I)

# One structural parser for the whole toolkit; see note_chunks.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from note_chunks import (all_chunks, bullets, read_note as read,  # noqa: E402
                         section, vault_links)


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
    template = ""
    block = re.search(r"```text\n(.*?)```", section(text, "## Draft form"), re.S)
    if block:
        template = block.group(1)
    return signals, exempt, template


def tokens(sentence):
    return {w for w in re.findall(r"[a-z][a-z-]{3,}", sentence.lower())
            if w not in STOPWORDS}


def stated_problems(vault, exempt):
    """(note, statement) for every explicitly stated problem in memory/.

    A filter over chunks, not a parser: any memory chunk sitting under a
    heading that names a problem, conflict, criticism, or question. Because a
    chunk keeps multi-line list items whole, statements are no longer cut at
    their first newline.
    """
    found = []
    for chunk in all_chunks(vault, corpus="memory"):
        if chunk["stem"].lower() in exempt:
            continue
        if not PROBLEMISH.search(" ".join(chunk["heading"])):
            continue
        text = " ".join(chunk["text"].split())
        if len(text) < 40 or "relates_to" in text or "source [[" in text:
            continue
        found.append((chunk["stem"], text))
    return found


def signal_recurrence(memory, exempt, threshold):
    items = stated_problems(memory, exempt)
    hits = []
    for (n1, a), (n2, b) in itertools.combinations(items, 2):
        if n1 == n2:
            continue
        ta, tb = tokens(a), tokens(b)
        if len(ta) < 4 or len(tb) < 4:
            continue
        overlap = len(ta & tb) / len(ta | tb)
        if overlap >= threshold:
            hits.append({"signal": "recurrence", "score": round(overlap, 3),
                         "where": [n1, n2], "text": a, "also": b})
    hits.sort(key=lambda h: -h["score"])
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


def render(template, problem, conjecture, source):
    return (template.replace("{{PROBLEM}}", problem)
            .replace("{{CONJECTURE}}", conjecture)
            .replace("{{SOURCE}}", source))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", default=str(Path.home() / "nimeesh vault"))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    vault = Path(args.vault).expanduser().resolve(strict=False)
    memory = vault / "memory"
    if not memory.is_dir():
        raise SystemExit(f"STOP: no memory directory at {memory}")

    signals, exempt, template = load_config(vault)
    hits = []
    if "recurrence" in signals:
        hits += signal_recurrence(vault, exempt, signals["recurrence"][0])
    if "hub-stub" in signals:
        a, b = signals["hub-stub"][:2]
        hits += signal_hub_stub(vault, int(a), int(b))
    if "never-written" in signals:
        hits += signal_never_written(vault, int(signals["never-written"][0]))

    if args.json:
        print(json.dumps({"signals": list(signals), "exempt": sorted(exempt),
                          "template": template, "candidates": hits[:args.limit]},
                         indent=2, ensure_ascii=False))
        return 0

    print(f"Candidates from {memory}")
    print(f"  signals: {', '.join(signals) or 'none enabled'}")
    print(f"  exempt:  {', '.join(sorted(exempt)) or 'none'}\n")
    if not hits:
        print("  no candidates above the configured thresholds")
    for hit in hits[:args.limit]:
        print(f"[{hit['signal']} {hit['score']}] {' + '.join(hit['where'])}")
        print(f"    {hit['text'][:150]}")
        if hit.get("also"):
            print(f"    ~ {str(hit['also'])[:150]}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
