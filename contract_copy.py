#!/usr/bin/env python3
"""Check generated contract copies against one canonical, semantic JSON source."""

import json
from pathlib import Path


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def validate_contract_copies(source_dir, home=None):
    source_dir = Path(source_dir)
    config_path = source_dir / "contract_copies.json"
    if not config_path.is_file():
        return [f"contract copy declaration missing: {config_path}"]
    config = load_json(config_path)
    home = Path(home or Path.home())
    problems = []
    for declaration in config.get("contracts", []):
        canonical_path = source_dir / declaration["canonical"]
        try:
            canonical = load_json(canonical_path)
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{declaration.get('name')}: canonical unreadable: {exc}")
            continue
        for raw in declaration.get("copies", []):
            path = Path(raw.replace("{home}", str(home)))
            try:
                copy = load_json(path)
            except (OSError, json.JSONDecodeError) as exc:
                problems.append(f"{declaration.get('name')}: copy unreadable {path}: {exc}")
                continue
            if copy != canonical:
                problems.append(f"{declaration.get('name')}: copy drifted: {path}")
    return problems
