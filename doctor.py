#!/usr/bin/env python3
"""Validate the canonical runtime, structural toolkit, and agent adapters."""

import argparse
import sys
import tempfile
from pathlib import Path

from adapters import (ADAPTERS, FIRST_CLASS, SHARED_FILES, TARGETS,
                      adapter_directories, add_adapter_directory_arguments,
                      add_vault_argument, absolute, generated_files,
                      parse_target, render, selected_keys, slash)

RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append(bool(ok))
    mark = "ok  " if ok else "FAIL"
    suffix = f" - {detail}" if detail else ""
    print(f"  [{mark}] {label}{suffix}")
    return bool(ok)


sys.path.insert(0, str(Path(__file__).resolve().parent))
from note_chunks import frontmatter_fields  # noqa: E402,F401
from policy_index import active_policy_surface  # noqa: E402
from artifact_lifecycle import lifecycle_problems, load_lifecycle  # noqa: E402
from contract_copy import validate_contract_copies  # noqa: E402
from installation import manifest_problems  # noqa: E402


def normalized_adapter(text, vault, tools_dir):
    return (text.replace("\\", "/")
            .replace(slash(vault), "{{VAULT_PATH}}")
            .replace(slash(tools_dir), "{{TOOLS_DIR}}"))


def check_active_note(label, path):
    """The note exists and its frontmatter says status: active."""
    if not check(f"{label} exists", path.is_file(), str(path)):
        check(f"{label} status is active", False, "note missing")
        return
    fields = frontmatter_fields(path.read_text(encoding="utf-8-sig", errors="replace"))
    check(f"{label} status is active", fields.get("status") == "active",
          f"status: {fields.get('status')!r}")


def validate_vault(vault):
    base = vault / "memory" / "perspirator"
    print("\nCanonical runtime")
    check_active_note("runtime", base / "Perspirator.md")
    check_active_note("bootstrap note", base / "Bootstrap.md")
    check_active_note("policy loader note",
                      vault / "memory" / "policies" / "Policy Loader.md")
    check_active_note("candidate selection note", base / "Candidate Selection.md")
    check_active_note("neighbour retrieval note", base / "Neighbour Retrieval.md")
    try:
        policies = active_policy_surface(vault)
        check("active policy surface is well formed", True,
              f"{len(policies)} policies")
    except ValueError as exc:
        check("active policy surface is well formed", False, str(exc))

    for name in ("proposals", "runs"):
        check(f"runtime directory exists: {name}", (base / name).is_dir())

    try:
        lifecycle = load_lifecycle(vault)
        problems = lifecycle_problems(vault, lifecycle)
        check("artifact lifecycle declaration is well formed", True,
              f"{len(lifecycle)} roles")
        check("declared artifact lifecycles have no mechanical violation",
              not problems, "; ".join(problems[:4]))
    except ValueError as exc:
        check("artifact lifecycle declaration is well formed", False, str(exc))

    runs = base / "runs"
    writable = False
    error = "runs directory missing"
    if runs.is_dir():
        try:
            with tempfile.NamedTemporaryFile(
                    dir=runs, prefix=".doctor-", suffix=".tmp", delete=True):
                writable = True
            error = ""
        except OSError as exc:
            error = str(exc)
    check("runs directory is writable", writable, error)


def validate_memory_freshness(vault):
    """Checks that replace a manual revision pass.

    Each one covers a place where an artifact used to restate another and drift
    when nobody remembered to update it.
    """
    memory = vault / "memory"
    print("\nMemory freshness")
    if not memory.is_dir():
        check("memory directory exists", False, str(memory))
        return

    notes = sorted(p for p in memory.rglob("*.md") if p.is_file())
    stale_updated, wrong_permalink = [], []

    for path in notes:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        fields = frontmatter_fields(text)
        rel = path.relative_to(vault).as_posix()

        if "updated" in fields:
            stale_updated.append(rel)

        permalink = fields.get("permalink")
        if permalink:
            # basic-memory slugifies folder names, so compare in that form.
            folder = path.parent.relative_to(vault).as_posix().lower().replace(" ", "-")
            if not permalink.lower().startswith(folder + "/"):
                wrong_permalink.append(f"{rel} -> {permalink}")


    check("no note carries a hand-maintained 'updated:' field",
          not stale_updated, ", ".join(stale_updated[:4]) or "")
    check("every permalink matches the note's folder",
          not wrong_permalink, ", ".join(wrong_permalink[:3]) or "")

    # Reported, not failed: a current knowledge note nothing points at has
    # already been forgotten. Lifecycle-declared run reports are directed
    # execution evidence, not ambient current memory, so mixing them into this
    # census drowns the Recall signal in deliberately unlinked history.
    recall_notes, directed_evidence = orphan_census_partition(vault, notes)
    targets = {p: 0 for p in recall_notes}
    corpus = "\n".join(p.read_text(encoding="utf-8-sig", errors="replace")
                        for p in recall_notes)
    for path in recall_notes:
        stem = path.stem
        if f"[[{stem}]]" in corpus or f"[[{stem}|" in corpus or f"/{stem}]]" in corpus:
            targets[path] = 1
    orphans = [p.relative_to(vault).as_posix() for p, hit in targets.items() if not hit]
    print(f"  [note] {len(orphans)} of {len(recall_notes)} current memory notes have no incoming "
           f"wikilink (see the Recall policy)")
    for rel in orphans[:8]:
        print(f"         {rel}")
    if len(orphans) > 8:
        print(f"         ... and {len(orphans) - 8} more")
    if directed_evidence:
        print(f"  [note] {len(directed_evidence)} lifecycle-declared run reports "
              "were excluded from the ambient orphan census")


def orphan_census_partition(vault, notes):
    """Separate current memory from lifecycle-declared directed run evidence.

    The lifecycle declaration owns the role boundary. If it is unreadable, the
    doctor reports that failure elsewhere and this census fails open by keeping
    every note visible rather than silently excluding an unknown path.
    """
    try:
        lifecycle = load_lifecycle(vault)
    except ValueError:
        return list(notes), []

    run_prefixes = [Path(entry["path"]) for entry in lifecycle
                    if entry.get("role") == "run"]
    current, directed = [], []
    for path in notes:
        relative = path.relative_to(vault)
        is_run = any(relative == prefix or prefix in relative.parents
                     for prefix in run_prefixes)
        (directed if is_run else current).append(path)
    return current, directed


NOT_SHIPPED = {"install.py"}


def validate_shared_files(source_dir):
    print("\nShared structural toolkit")
    for name in SHARED_FILES:
        check(f"shared source exists: {name}", (source_dir / name).is_file(),
              str(source_dir / name))

    # The manifest is hand-maintained, so the invariant it exists to protect —
    # the shipped toolkit is the repository toolkit — has to be checked against
    # the directory rather than against itself. Otherwise a new script is
    # simply never installed and every listed check still passes.
    present = {path.name for path in source_dir.glob("*.py")
               if not path.name.startswith("test_")} - NOT_SHIPPED
    listed_scripts = {name for name in SHARED_FILES if "/" not in name
                      and name.endswith(".py")}
    unlisted = sorted(present - listed_scripts)
    check("every source script is listed for install", not unlisted,
          "unlisted, so never installed: " + ", ".join(unlisted) if unlisted else "")
    copy_problems = validate_contract_copies(source_dir)
    check("generated contract fixtures match their canonical source",
          not copy_problems, "; ".join(copy_problems))


def validate_adapter(key, adapter, tools_dir, vault, template, source_dir):
    print(f"\n{ADAPTERS[key]['label']} adapter")
    if not check("adapter exists", adapter.is_file(), str(adapter)):
        return None

    text = adapter.read_text(encoding="utf-8-sig", errors="replace")
    normalized_text = text.replace("\\", "/").lower()

    bootstrap = f"{slash(vault)}/memory/perspirator/Bootstrap.md"
    check("names the bootstrap note", bootstrap.lower() in normalized_text, bootstrap)
    unresolved = any(marker in text for marker in ("{{VAULT_PATH}}", "{{TOOLS_DIR}}"))
    check("contains no unresolved path placeholders", not unresolved)
    check("points at its structural toolkit",
          slash(tools_dir).lower() in normalized_text, slash(tools_dir))

    for relative in SHARED_FILES:
        installed = tools_dir / relative
        check(f"installed shared file exists: {relative}", installed.is_file(),
              str(installed))
        source = source_dir / relative
        if installed.is_file() and source.is_file():
            check(f"installed shared file matches source: {relative}",
                  installed.read_bytes() == source.read_bytes())

    desired = generated_files(key)
    ownership_problems = manifest_problems(tools_dir, desired)
    check("generated-file ownership is current", not ownership_problems,
          "; ".join(ownership_problems))

    if template is not None:
        check("render matches canonical bootstrap source exactly",
              text == render(template, vault, tools_dir))
    return text


def target_type(value):
    try:
        return parse_target(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    add_vault_argument(parser)
    parser.add_argument("--target", type=target_type, default="Auto",
                        help=f"Auto or one of {', '.join(TARGETS)}")
    add_adapter_directory_arguments(parser)
    parser.add_argument("--source", help="canonical SKILL.md template")
    return parser.parse_args()


def main():
    args = arguments()
    vault = absolute(args.vault)
    here = Path(__file__).resolve().parent

    directories = adapter_directories(args)

    candidate = absolute(args.source) if args.source else here / "SKILL.md"
    template = None
    if candidate.is_file():
        candidate_text = candidate.read_text(encoding="utf-8-sig", errors="replace")
        if "{{VAULT_PATH}}" in candidate_text and "{{TOOLS_DIR}}" in candidate_text:
            template = candidate_text
    source_dir = candidate.parent if template is not None else here

    if args.target == "Auto":
        selected = [key for key in FIRST_CLASS
                    if (directories[key] / ADAPTERS[key]["filename"]).is_file()]
    else:
        selected = selected_keys(args.target)

    print(f"Perspirator doctor - target: {args.target}")
    print(f"Vault: {vault}")
    if not selected:
        check("an installed adapter was discovered", False,
              "use --target or install an adapter")
    if "Custom" in selected and "Custom" not in directories:
        check("custom directory supplied", False, "use --custom-dir")
        selected = []

    validate_vault(vault)
    validate_memory_freshness(vault)
    validate_shared_files(source_dir)

    rendered = {}
    for key in selected:
        tools_dir = directories[key]
        adapter = tools_dir / ADAPTERS[key]["filename"]
        text = validate_adapter(key, adapter, tools_dir, vault, template, source_dir)
        if text is not None:
            rendered[key] = (text, tools_dir)

    if len(rendered) > 1:
        print("\nCross-adapter consistency")
        keys = sorted(rendered)
        first_key = keys[0]
        first_text, first_tools = rendered[first_key]
        baseline = normalized_adapter(first_text, vault, first_tools)
        for key in keys[1:]:
            text, tools_dir = rendered[key]
            check(f"{first_key} and {key} share one bootstrap semantics",
                  normalized_adapter(text, vault, tools_dir) == baseline)
        for relative in SHARED_FILES:
            paths = [tools / relative for _, tools in rendered.values()]
            same = (all(path.is_file() for path in paths) and
                    len({path.read_bytes() for path in paths}) == 1)
            check(f"all adapters share file bytes: {relative}", same)

    print()
    if RESULTS and all(RESULTS):
        print(f"all {len(RESULTS)} checks passed")
        return 0
    print(f"{RESULTS.count(False)} of {len(RESULTS)} checks FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
