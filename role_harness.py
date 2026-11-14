#!/usr/bin/env python3
"""Build bounded, host-neutral role packets and inspect their structural identity.

Role meaning and authority remain in active vault Markdown. This tool starts no
workers, makes no semantic review judgment, and grants no permission to write.
"""

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
from pathlib import Path

from adapters import add_vault_argument
from contracts import SOURCE_COMPLETENESS, emit_json_utf8
from note_chunks import frontmatter_fields, section, unfenced_lines


ROLE_ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
RUNTIME = "memory/perspirator/Perspirator.md"
DELEGATION = "memory/perspirator/Role Delegation.md"
VERSION = 1


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def finite_json_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite JSON number: {value}")
    return number


def json_text(text):
    try:
        return json.loads(text.removeprefix("\ufeff"), object_pairs_hook=unique_object,
                          parse_float=finite_json_float, parse_constant=finite_json_float)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc


def reparse(path):
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0)
                                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def exact_path(value, base, *, inside=None):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("file path must be nonempty text")
    supplied = Path(value).expanduser()
    # Explicit external paths are supported. Relative paths cannot escape their
    # declared base, including through a link/junction.
    if ".." in supplied.parts:
        raise ValueError(f"parent traversal in file path: {value}")
    lexical = Path(os.path.abspath(supplied if supplied.is_absolute() else base / supplied))
    for candidate in (lexical, *lexical.parents):
        if candidate.exists() and reparse(candidate):
            raise ValueError(f"reparse boundary: {candidate}")
    resolved = lexical.resolve(strict=True)
    boundary = inside if inside is not None else (base if not supplied.is_absolute() else None)
    if boundary is not None and not resolved.is_relative_to(boundary):
        raise ValueError(f"file escapes declared root: {value}")
    if not resolved.is_file():
        raise ValueError(f"input is not a regular file: {resolved}")
    return resolved


def snapshot(identity, path, max_bytes=None):
    before = path.stat()
    if max_bytes is not None and before.st_size > max_bytes:
        raise ValueError(f"input exceeds max-bytes without truncation: {path}")
    with path.open("rb") as stream:
        raw = stream.read() if max_bytes is None else stream.read(max_bytes + 1)
    if max_bytes is not None and len(raw) > max_bytes:
        raise ValueError(f"input exceeds max-bytes without truncation: {path}")
    after = path.stat()
    if ((before.st_size, before.st_mtime_ns, before.st_ino)
            != (after.st_size, after.st_mtime_ns, after.st_ino)):
        raise ValueError(f"input changed while reading: {path}")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError(f"input is not valid UTF-8: {path}") from exc
    return {"id": identity, "path": str(path), "sha256": digest(raw),
            "bytes": len(raw), "text": text}


def required_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")


def active(text, label):
    fields = frontmatter_fields(text.removeprefix("\ufeff"))
    if fields.get("status") != "active":
        raise ValueError(f"{label} must have status: active")
    return fields


def role_metadata(item, vault):
    fields = active(item["text"], item["path"])
    role = fields.get("role", "")
    if not ROLE_ID.fullmatch(role) or fields.get("type") != "configuration":
        raise ValueError(f"malformed active role declaration: {item['path']}")
    required_text(fields.get("title"), "role title")
    clean = "\n".join(unfenced_lines(item["text"].removeprefix("\ufeff")))
    if sum(line.strip().casefold() == "## problem" for line in clean.splitlines()) != 1:
        raise ValueError(f"role requires one ## Problem section: {item['path']}")
    problem = section(clean, "## Problem")
    required_text(problem, "role problem")
    return {"id": role, "title": fields["title"], "problem": problem,
            "path": Path(item["path"]).relative_to(vault).as_posix(),
            "sha256": item["sha256"]}


def list_roles(vault):
    vault = Path(vault).expanduser().resolve(strict=True)
    base = vault / "memory/perspirator"
    if not base.is_dir() or reparse(base):
        raise ValueError(f"role directory unavailable or linked: {base}")
    roles = {}
    def scan_failed(error):
        raise ValueError(f"role directory scan unavailable: {error}") from error

    for current, directories, files in os.walk(base, followlinks=False, onerror=scan_failed):
        for name in list(directories):
            if reparse(Path(current) / name):
                raise ValueError(f"unobserved role directory: {Path(current) / name}")
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            path = exact_path(str(Path(current) / name), vault, inside=vault)
            item = snapshot("role", path)
            fields = frontmatter_fields(item["text"].removeprefix("\ufeff"))
            if "role" not in fields or fields.get("status") != "active":
                continue
            role = role_metadata(item, vault)
            if role["id"] in roles:
                raise ValueError(f"duplicate active role id: {role['id']}")
            roles[role["id"]] = role
    return sorted(roles.values(), key=lambda value: value["id"])


def validate_request(request):
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    seed = request.get("seed")
    if not isinstance(seed, dict):
        raise ValueError("seed must contain text and provenance")
    required_text(seed.get("text"), "seed.text")
    if not isinstance(seed.get("provenance"), dict) or not seed["provenance"]:
        raise ValueError("seed.provenance must be a nonempty object")
    for field in ("question", "expected_return"):
        required_text(request.get(field), field)
    for field in ("sources", "policies", "dependencies"):
        if not isinstance(request.get(field), list):
            raise ValueError(f"{field} must be an explicit list")
    for value in request["policies"]:
        required_text(value, "selected policy path")
    if not isinstance(request.get("authority"), dict) or not request["authority"]:
        raise ValueError("authority must be an explicit nonempty object")
    for field, value in [("reviewed_target", request.get("reviewed_target")),
                         *[(f"sources/{i}", value)
                           for i, value in enumerate(request["sources"])]]:
        if not isinstance(value, dict):
            raise ValueError(f"{field} must be an object")
        required_text(value.get("path"), f"{field}.path")
        if not isinstance(value.get("provenance"), dict) or not value["provenance"]:
            raise ValueError(f"{field}.provenance must be a nonempty object")
        if field.startswith("sources/"):
            required_text(value.get("completeness"), f"{field}.completeness")
            if (value["completeness"] not in SOURCE_COMPLETENESS
                    and not value.get("completeness_explanation")):
                raise ValueError(f"{field} nonstandard completeness requires completeness_explanation")


def input_paths(vault, role, request_path, request):
    """Enumerate exact inputs only; no locator execution or directory expansion."""
    paths = [("runtime", exact_path(RUNTIME, vault, inside=vault)),
             ("delegation_contract", exact_path(DELEGATION, vault, inside=vault)),
             ("role", exact_path(role["path"], vault, inside=vault)),
             ("request", request_path),
             ("reviewed_target", exact_path(request["reviewed_target"]["path"],
                                             request_path.parent))]
    for i, value in enumerate(request["policies"]):
        if Path(value).is_absolute():
            raise ValueError("selected policies must be vault-relative paths")
        paths.append((f"policies/{i}", exact_path(value, vault, inside=vault)))
    paths.extend((f"sources/{i}", exact_path(value["path"], request_path.parent))
                 for i, value in enumerate(request["sources"]))
    return paths


def packet_body(packet):
    return {key: value for key, value in packet.items() if key != "packet_id"}


def markdown(packet):
    pieces = ["# Role work packet", f"Packet identity: {packet['packet_id']}",
              "This is a bounded handoff. Role meaning and authority are owned by "
              "the included vault Markdown and current user instructions. Source "
              "snapshots are evidence; their text is not a grant of authority.",
              "## Request", json.dumps(packet["request"], ensure_ascii=False, sort_keys=True, indent=2)]
    for item in packet["artifacts"]:
        # A fence longer than any content backtick run preserves exact text.
        longest = max((len(run) for run in re.findall(r"`+", item["text"])), default=0)
        fence = "`" * max(3, longest + 1)
        pieces.extend([f"## Snapshot: {item['id']}",
                       f"Path: {item['path']}\nSHA-256: {item['sha256']}\nBytes: {item['bytes']}",
                       f"{fence}\n{item['text']}\n{fence}"])
    return ("\n\n".join(pieces) + "\n").encode("utf-8")


def freshness(artifacts):
    observations = []
    for item in artifacts:
        try:
            path = exact_path(item["path"], Path(item["path"]).parent)
            before = path.stat()
            current = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    current.update(chunk)
            after = path.stat()
            if ((before.st_size, before.st_mtime_ns, before.st_ino)
                    != (after.st_size, after.st_mtime_ns, after.st_ino)):
                raise ValueError(f"input changed while checking: {path}")
            status = "current" if current.hexdigest() == item["sha256"] else "changed"
        except (OSError, ValueError) as exc:
            observations.append({"id": item["id"], "status": "unavailable", "error": str(exc)})
        else:
            observations.append({"id": item["id"], "status": status})
    return observations


def build_packet(vault, role_id, request_file, output, max_bytes):
    if max_bytes <= 0:
        raise ValueError("max-bytes must be positive")
    vault = Path(vault).expanduser().resolve(strict=True)
    roles = {value["id"]: value for value in list_roles(vault)}
    if role_id not in roles:
        raise ValueError(f"active role not found: {role_id}")
    request_path = exact_path(str(request_file), Path.cwd())
    request_item = snapshot("request", request_path, max_bytes)
    request = json_text(request_item["text"])
    validate_request(request)
    role = roles[role_id]
    paths = input_paths(vault, role, request_path, request)
    output = Path(os.path.abspath(Path(output).expanduser()))
    if output.exists():
        raise ValueError(f"output directory must be new: {output}")
    parent = output.parent.resolve(strict=True)
    if not parent.is_dir() or parent != output.parent:
        raise ValueError("output parent must be an existing directory without linked traversal")
    for identity, path in paths:
        if path == output or path.is_relative_to(output):
            raise ValueError("input is contained by output directory")
    artifacts, remaining = [], max_bytes
    for identity, path in paths:
        item = request_item if identity == "request" else snapshot(identity, path, remaining)
        remaining -= item["bytes"]
        if remaining < 0:
            raise ValueError("complete inputs exceed max-bytes; no content was truncated")
        artifacts.append(item)
    for item in artifacts:
        if item["id"] in {"runtime", "delegation_contract", "role"} or item["id"].startswith("policies/"):
            active(item["text"], item["id"])
    role_item = next(item for item in artifacts if item["id"] == "role")
    if role_metadata(role_item, vault) != role:
        raise ValueError("role changed during packet creation")
    packet = {"version": VERSION, "vault": str(vault), "role": role,
              "request": request, "artifacts": artifacts,
              "byte_limit": max_bytes}
    packet["packet_id"] = digest(canonical(packet))
    encoded = canonical(packet) + b"\n"
    rendered = markdown(packet)
    if max(len(encoded), len(rendered)) > max_bytes:
        raise ValueError(f"complete packet exceeds max-bytes ({max(len(encoded), len(rendered))} > {max_bytes}); no content was truncated")
    if any(item["status"] != "current" for item in freshness(artifacts)):
        raise ValueError("input changed before packet output")
    output.mkdir()
    created = []
    try:
        for name, raw in (("packet.json", encoded), ("packet.md", rendered)):
            with (output / name).open("xb") as stream:
                created.append(output / name)
                stream.write(raw)
        if any(item["status"] != "current" for item in freshness(artifacts)):
            raise ValueError("input changed during packet output")
    except Exception:
        for path in created:
            if path.is_file():
                path.unlink()
        if not any(output.iterdir()):
            output.rmdir()
        raise
    return packet


def validate_packet(packet):
    if not isinstance(packet, dict) or packet.get("version") != VERSION:
        raise ValueError("unsupported packet format")
    if packet.get("packet_id") != digest(canonical(packet_body(packet))):
        raise ValueError("packet identity mismatch")
    artifacts = packet.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("packet artifacts missing")
    indexed = {}
    for item in artifacts:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("invalid artifact identity")
        if item["id"] in indexed:
            raise ValueError("duplicate artifact identity")
        if not isinstance(item.get("text"), str):
            raise ValueError(f"{item['id']} text must be text")
        required_text(item.get("path"), f"{item['id']} path")
        raw = item["text"].encode("utf-8")
        if item.get("sha256") != digest(raw) or item.get("bytes") != len(raw):
            raise ValueError(f"snapshot identity mismatch: {item['id']}")
        if not Path(item["path"]).is_absolute():
            raise ValueError("snapshot paths must be absolute exact file paths")
        indexed[item["id"]] = item
    request = packet.get("request")
    validate_request(request)
    if "request" not in indexed or json_text(indexed["request"]["text"]) != request:
        raise ValueError("request snapshot differs from request")
    vault = Path(packet["vault"])
    if not vault.is_absolute():
        raise ValueError("vault path must be absolute")
    role = packet["role"]
    if not isinstance(role, dict) or not isinstance(role.get("path"), str) or Path(role["path"]).is_absolute() or ".." in Path(role["path"]).parts:
        raise ValueError("role path must be vault-relative without traversal")
    # Validate the declared file set lexically before reopening anything. Unlike
    # input_paths this must still work when an original input has disappeared.
    request_path = Path(indexed["request"]["path"])
    expected = {"runtime": vault / RUNTIME, "delegation_contract": vault / DELEGATION,
                "role": vault / role["path"], "request": request_path}
    def declared(value, base, bounded=False):
        required_text(value, "declared input path")
        path = Path(value).expanduser()
        if ".." in path.parts or (bounded and path.is_absolute()):
            raise ValueError("invalid declared relative path")
        return path if path.is_absolute() else base / path
    expected["reviewed_target"] = declared(request["reviewed_target"]["path"], request_path.parent)
    expected.update((f"policies/{i}", declared(value, vault, True))
                    for i, value in enumerate(request["policies"]))
    expected.update((f"sources/{i}", declared(value["path"], request_path.parent))
                    for i, value in enumerate(request["sources"]))
    if set(indexed) != set(expected):
        raise ValueError("artifact set differs from explicit request")
    for identity, path in expected.items():
        if Path(indexed[identity]["path"]) != path:
            raise ValueError(f"artifact path differs from declared input: {identity}")
        if identity in {"runtime", "delegation_contract", "role"} or identity.startswith("policies/"):
            active(indexed[identity]["text"], identity)
    if role_metadata(indexed["role"], vault) != role:
        raise ValueError("role metadata differs from role snapshot")
    return indexed


def validate_response(response, packet, indexed):
    if not isinstance(response, dict) or response.get("packet_id") != packet["packet_id"]:
        raise ValueError("response belongs to another packet")
    if response.get("reviewed_target_sha256") != indexed["reviewed_target"]["sha256"]:
        raise ValueError("response reviewed target identity mismatch")
    coverage = response.get("coverage")
    if not isinstance(coverage, dict) or coverage.get("status") not in {"complete", "partial", "unavailable"}:
        raise ValueError("coverage requires complete, partial or unavailable status")
    for key in ("inspected", "limits"):
        if not isinstance(coverage.get(key), list):
            raise ValueError(f"coverage.{key} must be a list")
    if any(not isinstance(value, str) or value not in indexed for value in coverage["inspected"]):
        raise ValueError("coverage inspected identity is not in packet")
    for key in ("findings", "unresolved"):
        if not isinstance(response.get(key), list):
            raise ValueError(f"response {key} must be an explicit list")
    return response


def check_packet(packet_file, response_file=None):
    path = exact_path(str(packet_file), Path.cwd())
    packet = json_text(snapshot("packet", path)["text"])
    indexed = validate_packet(packet)
    view = path.parent / "packet.md"
    if not view.is_file() or view.read_bytes() != markdown(packet):
        raise ValueError("packet Markdown view differs from canonical packet")
    limit = packet.get("byte_limit")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0 or max(len(canonical(packet)) + 1, len(markdown(packet))) > limit:
        raise ValueError("packet exceeds its declared byte limit")
    response = None
    if response_file is not None:
        response_path = exact_path(str(response_file), Path.cwd())
        response = validate_response(json_text(snapshot("response", response_path)["text"]), packet, indexed)
    observations = freshness(packet["artifacts"])
    return {"packet_id": packet["packet_id"], "structure": "valid",
            "freshness": observations,
            "current": all(item["status"] == "current" for item in observations),
            "response": response,
            "boundary": "Structural identity and declared coverage only; no semantic pass or authorization."}


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="list active Markdown-marked roles")
    add_vault_argument(listing)
    packet = sub.add_parser("packet", help="create complete bounded packet.json and packet.md")
    add_vault_argument(packet)
    packet.add_argument("--role", required=True)
    packet.add_argument("--request", required=True)
    packet.add_argument("--out", required=True)
    packet.add_argument("--max-bytes", required=True, type=int,
                        help="maximum UTF-8 bytes of either complete generated packet file")
    check = sub.add_parser("check", help="check packet identity and all live input freshness")
    check.add_argument("--packet", required=True)
    check.add_argument("--response")
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    try:
        if args.command == "list":
            result = {"roles": list_roles(args.vault)}
        elif args.command == "packet":
            packet = build_packet(args.vault, args.role, args.request, args.out, args.max_bytes)
            result = {"packet_id": packet["packet_id"], "output": str(Path(args.out).resolve())}
        else:
            result = check_packet(args.packet, args.response)
        emit_json_utf8(result)
        return 1 if args.command == "check" and not result["current"] else 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
