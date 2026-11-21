"""Context-free, unique note-link resolution; never guess a source folder.

Interest's link_targets.dart runs the same shared conformance cases. This is
a structural identity map, not a substitute for Obsidian's contextual resolver.
"""
from pathlib import PurePosixPath

from note_chunks import frontmatter_entries, iter_notes, _values
from problem_half import parse_note


def link_key(target):
    base = target.split("|", 1)[0].split("#", 1)[0].replace("\\", "/").strip()
    return (base[:-3] if base.lower().endswith(".md") else base).lower()


def resolve_link_targets(aliases_by_note):
    names, aliases = {}, {}
    for relative, declared in aliases_by_note.items():
        canonical = relative.replace("\\", "/")
        if canonical.lower().endswith(".md"):
            canonical = canonical[:-3]
        for key in {link_key(canonical), link_key(PurePosixPath(canonical).name)}:
            names.setdefault(key, set()).add(canonical)
        for alias in declared:
            aliases.setdefault(link_key(alias), set()).add(canonical)
    resolved = {k: next(iter(v)) for k, v in names.items() if len(v) == 1}
    resolved.update({k: next(iter(v)) for k, v in aliases.items()
                     if k not in names and len(v) == 1 and k})
    return resolved


def vault_link_targets(vault):
    """Read the observed Markdown surface, failing if a note cannot be read.

    Includes memory: this is a vault context map, unlike Interest's authored
    card surface. Hidden/system exclusions are those of iter_notes.
    """
    facts = {}
    for path in iter_notes(vault):
        fm = parse_note(path.read_text(encoding="utf-8-sig"))["frontmatter"] or ""
        declared = [v for key, inline, items, other in frontmatter_entries(fm)
                    if key in {"alias", "aliases"} and not other
                    for v in _values(inline, items)]
        facts[path.relative_to(vault).as_posix()] = declared
    return resolve_link_targets(facts)
