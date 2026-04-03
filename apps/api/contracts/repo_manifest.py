from __future__ import annotations

import argparse
import json
from typing import Any

from apps.planner.planner_defaults import PLANNER_PROMPT_DEFAULTS


def get_repo_contract_manifest() -> dict[str, Any]:
    return {
        "planner_prompt_defaults": dict(PLANNER_PROMPT_DEFAULTS),
    }


def _emit_manifest_section(section: str | None) -> dict[str, Any]:
    manifest = get_repo_contract_manifest()
    if not section:
        return manifest
    normalized = str(section or "").strip().lower()
    if normalized not in manifest:
        raise KeyError(f"unknown manifest section: {section}")
    return manifest[normalized]


def main() -> None:
    parser = argparse.ArgumentParser(description="Emit the NTIS repo contract manifest as JSON.")
    parser.add_argument("--section", default=None, help="Optional top-level section name to emit.")
    args = parser.parse_args()
    payload = _emit_manifest_section(args.section)
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
