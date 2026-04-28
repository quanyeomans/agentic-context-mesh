#!/usr/bin/env python3
"""
validate-entity-stubs.py — Validate entity stub .md files against quality standards.

Checks each .md file in the entity stubs directory against:
  - frontmatter_complete: required keys present (type, name, entity-id, created)
  - type_valid: type is one of person/organisation/concept/project/decision
  - body_length: body has >= 20 non-empty lines
  - no_boilerplate: no auto-generated placeholder text
  - has_relationships: body contains a ## Relationships or ## Related section
  - name_not_generic: entity name is not a stop word

Designed as a CI gate: use --strict to exit 1 on any failures.

Usage:
    python scripts/validate-entity-stubs.py <entity-dir> [--strict]

Exit codes:
    0 — all files pass (or failures exist but --strict not set)
    1 — one or more failures and --strict is set
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import TypedDict

from kairix.entities.stop_entities import is_stop_entity

VALID_TYPES: frozenset[str] = frozenset({"person", "organisation", "concept", "project", "decision"})

REQUIRED_FRONTMATTER_KEYS: list[str] = ["type", "name", "entity-id", "created"]

BOILERPLATE_STRINGS: list[str] = [
    "Auto-generated entity",
    "Needs manual review",
]

BOILERPLATE_PATTERN: re.Pattern[str] = re.compile(r"appears in \d+ (vault )?documents")


class CheckResult(TypedDict):
    passed: bool
    reasons: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate entity stub .md files against quality standards.",
    )
    parser.add_argument(
        "entity_dir",
        metavar="entity-dir",
        help="Path to the entity stubs directory (e.g. agent-knowledge/entities/)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Exit 1 if any files fail validation",
    )
    return parser.parse_args()


def extract_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """
    Parse the leading YAML frontmatter block from a markdown file.

    Returns:
        (frontmatter_dict, body_text)
        frontmatter_dict is empty if no valid frontmatter block found.
        body_text is everything after the closing ---.
    """
    if not content.startswith("---"):
        return {}, content

    end_idx = content.find("\n---", 3)
    if end_idx == -1:
        return {}, content

    fm_block = content[3:end_idx].strip()
    body = content[end_idx + 4 :].strip()

    frontmatter: dict[str, str] = {}
    for line in fm_block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            frontmatter[key.strip()] = value.strip()

    return frontmatter, body


def check_frontmatter_complete(frontmatter: dict[str, str]) -> list[str]:
    """Return failure reasons if required frontmatter keys are missing."""
    missing = [k for k in REQUIRED_FRONTMATTER_KEYS if k not in frontmatter]
    if missing:
        return [f"frontmatter_complete: missing keys: {', '.join(missing)}"]
    return []


def check_type_valid(frontmatter: dict[str, str]) -> list[str]:
    """Return failure reasons if the type field is not in VALID_TYPES."""
    entity_type = frontmatter.get("type", "").strip()
    if entity_type not in VALID_TYPES:
        return [f"type_valid: '{entity_type}' is not one of {sorted(VALID_TYPES)}"]
    return []


def check_body_length(body: str) -> list[str]:
    """Return failure reasons if body has fewer than 20 non-empty lines."""
    non_empty = [line for line in body.splitlines() if line.strip()]
    if len(non_empty) < 20:
        return [f"body_length: only {len(non_empty)} non-empty lines (need >= 20)"]
    return []


def check_no_boilerplate(content: str) -> list[str]:
    """Return failure reasons if the file contains boilerplate placeholder text."""
    for phrase in BOILERPLATE_STRINGS:
        if phrase in content:
            return [f"no_boilerplate: contains '{phrase}'"]
    if BOILERPLATE_PATTERN.search(content):
        return ["no_boilerplate: matches 'appears in N (vault )?documents' pattern"]
    return []


def check_has_relationships(body: str) -> list[str]:
    """Return failure reasons if the body lacks a Relationships or Related section."""
    if "## Relationships" not in body and "## Related" not in body:
        return ["has_relationships: no '## Relationships' or '## Related' section found"]
    return []


def check_name_not_generic(frontmatter: dict[str, str]) -> list[str]:
    """Return failure reasons if the entity name is a stop word."""
    name = frontmatter.get("name", "").strip()
    if not name:
        return ["name_not_generic: name field is empty"]
    if is_stop_entity(name):
        return [f"name_not_generic: '{name}' is a stop entity"]
    return []


def validate_file(path: Path) -> CheckResult:
    """Run all checks against a single entity stub file."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return CheckResult(passed=False, reasons=[f"read_error: {exc}"])

    frontmatter, body = extract_frontmatter(content)
    reasons: list[str] = []

    reasons.extend(check_frontmatter_complete(frontmatter))
    reasons.extend(check_type_valid(frontmatter))
    reasons.extend(check_body_length(body))
    reasons.extend(check_no_boilerplate(content))
    reasons.extend(check_has_relationships(body))
    reasons.extend(check_name_not_generic(frontmatter))

    return CheckResult(passed=len(reasons) == 0, reasons=reasons)


def main() -> None:
    args = parse_args()
    entity_dir = Path(args.entity_dir)

    if not entity_dir.exists():
        print(f"ERROR: entity-dir does not exist: {entity_dir}", file=sys.stderr)
        sys.exit(1)

    if not entity_dir.is_dir():
        print(f"ERROR: entity-dir is not a directory: {entity_dir}", file=sys.stderr)
        sys.exit(1)

    md_files = sorted(entity_dir.rglob("*.md"))

    if not md_files:
        print(f"No .md files found in {entity_dir}")
        sys.exit(0)

    passed_count = 0
    failed_count = 0

    for md_path in md_files:
        relative = md_path.relative_to(entity_dir)
        result = validate_file(md_path)

        if result["passed"]:
            print(f"  PASS  {relative}")
            passed_count += 1
        else:
            print(f"  FAIL  {relative}")
            for reason in result["reasons"]:
                print(f"    {reason}")
            failed_count += 1

    print(f"\nResult: {passed_count} passed, {failed_count} failed")

    if args.strict and failed_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
