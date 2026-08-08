#!/usr/bin/env python3
"""Anchor authored passages of a talk to the moment they were spoken.

A spoken passage has no natural boundary and no page number, so the source
fact it needs is *when*. This adapts a caption track into the same
source-neutral source-record contract as `x_posts.py`, with the locator
carrying a second offset, so a passage stays re-hearable in its own context.

The caption file is fetched outside this tool, which stays stdlib-only:

    python -m yt_dlp --skip-download --write-auto-sub --sub-lang en \\
        --sub-format vtt -o "talk.%(ext)s" "<video url>"

It establishes source facts only. Which passages are worth keeping, which
problems they bear on, and what they refute are judgments made elsewhere.

Examples:
    python video_sources.py talk.en.vtt --passages selected.txt --video <url>
    python video_sources.py talk.en.vtt --passages selected.txt --out sources.json
"""

import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

from contracts import provider_result, source_record, status_for

CUE_RE = re.compile(r"^(\d\d):(\d\d):(\d\d)\.(\d\d\d)\s*-->")
INLINE_RE = re.compile(r"<(\d\d):(\d\d):(\d\d)\.(\d\d\d)>")
TAG_RE = re.compile(r"</?c[^>]*>")
VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})")
WORD_RE = re.compile(r"[a-z0-9']+")


def seconds(hours, minutes, secs, millis):
    return int(hours) * 3600 + int(minutes) * 60 + int(secs) + int(millis) / 1000


def video_id(value):
    """Accept a bare id or any YouTube URL form."""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value or ""):
        return value
    found = VIDEO_ID_RE.search(value or "")
    if not found:
        raise ValueError(f"no YouTube video id in: {value!r}")
    return found.group(1)


def normalise(text):
    return WORD_RE.findall(text.lower().replace("’", "'"))


def timeline(vtt_text):
    """One (seconds, word) entry per spoken word, rolling repeats removed.

    YouTube auto-captions restate the previous line as untimed text and mark
    only the newly spoken words with inline timestamps. Keeping the timed
    words alone therefore both deduplicates the roll-up and gives word-level
    rather than cue-level resolution.
    """
    entries = []
    cue_start = None
    for raw in vtt_text.splitlines():
        cue = CUE_RE.match(raw.strip())
        if cue:
            cue_start = seconds(*cue.groups())
            continue
        if cue_start is None or not raw.strip() or "-->" in raw:
            continue
        if not INLINE_RE.search(raw):
            continue
        pieces = INLINE_RE.split(raw)
        head = TAG_RE.sub("", pieces[0])
        for word in normalise(head):
            entries.append((cue_start, word))
        for index in range(1, len(pieces), 5):
            stamp = seconds(*pieces[index:index + 4])
            body = TAG_RE.sub("", pieces[index + 4]) if index + 4 < len(pieces) else ""
            for word in normalise(body):
                entries.append((stamp, word))
    return entries


def locate(words, probe, minimum=0.75):
    """Best start index for a probe sequence, or None when nothing matches.

    Returns explicit non-placement rather than the least-bad guess: a
    confidently wrong timestamp sends a reader to the wrong argument, which
    is worse than a passage carrying no offset at all.
    """
    if not probe or len(words) < len(probe):
        return None, 0.0
    span = len(probe)
    first = probe[0]
    starts = [i for i, (_, word) in enumerate(words) if word == first]
    if not starts:
        starts = range(len(words) - span + 1)
    best_at, best_score = None, 0.0
    for start in starts:
        window = [word for _, word in words[start:start + span]]
        if window == probe:
            return start, 1.0
        score = SequenceMatcher(None, window, probe).ratio()
        if score > best_score:
            best_at, best_score = start, score
    return (best_at, best_score) if best_score >= minimum else (None, best_score)


def passages_from(text):
    """Blank-line separated blocks, in the order the reader chose them."""
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text)]
    return [block for block in blocks if block]


def anchor(vtt_text, passages, url, probe_words=8, minimum=0.75):
    words = timeline(vtt_text)
    ident = video_id(url)
    located, unplaced = [], []
    for index, passage in enumerate(passages, 1):
        probe = normalise(passage)[:probe_words]
        start, score = locate(words, probe, minimum)
        if start is None:
            unplaced.append({"passage": passage[:120], "best_match": round(score, 2)})
            continue
        offset = int(words[start][0])
        located.append(source_record(
            f"{ident}-{offset}", passage,
            f"https://youtu.be/{ident}?t={offset}", provider="video-captions",
            provenance={"provider": "video-captions", "video": ident},
            seconds=offset, exact=score == 1.0, order=index))
    return provider_result(
        "video-captions", "anchor authored passages",
        status_for(located, [], incomplete=bool(unplaced)),
        scope={"video": ident, "passages": len(passages)},
        freshness={"basis": "supplied caption file"},
        records=located, errors=[], words=len(words), unplaced=unplaced)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vtt", help="caption file fetched by yt-dlp")
    parser.add_argument("--passages", required=True,
                        help="blank-line separated passages to anchor")
    parser.add_argument("--video", help="video url or id; defaults to the vtt filename")
    parser.add_argument("--probe-words", type=int, default=8)
    parser.add_argument("--min-match", type=float, default=0.75)
    parser.add_argument("--out", help="write JSON sources here")
    args = parser.parse_args()

    vtt_text = Path(args.vtt).read_text(encoding="utf-8", errors="replace")
    passages = passages_from(Path(args.passages).read_text(encoding="utf-8"))
    try:
        report = anchor(vtt_text, passages, args.video or args.vtt,
                        args.probe_words, args.min_match)
    except ValueError as exc:
        raise SystemExit(f"STOP: {exc}")

    if args.out:
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=1),
            encoding="utf-8")
    exact = sum(1 for item in report["records"] if item["exact"])
    print(f"{len(report['records'])} anchored ({exact} exact), "
          f"{len(report['unplaced'])} unplaced, "
          f"from {report['words']} timed words", file=sys.stderr)
    for item in report["unplaced"]:
        print(f"  UNPLACED (best {item['best_match']}): {item['passage']}",
              file=sys.stderr)
    if not args.out:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
