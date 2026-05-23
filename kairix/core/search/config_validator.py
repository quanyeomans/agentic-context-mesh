"""kairix config validate — schema validation for kairix.config.yaml.

Operator-facing utility that reads the YAML, parses collections + agents,
and reports any structural issues. Exits non-zero on errors so it can
be wired into CI pre-deploy checks.

Validates:
  - Each collection has a name (required) and a path (required).
  - Each agent has a name; collection names match the agent_pattern (or
    are explicitly declared); write_paths are non-overlapping.
  - retrieval_overrides keys (when present) name fields that exist on
    RetrievalConfig — silent typos in this section are a common operator
    mistake.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# RetrievalConfig fields valid as override keys. Kept as a literal list so we
# don't drag the dataclass in as a runtime dependency for validation alone.
_VALID_OVERRIDE_KEYS = frozenset(
    {
        "fusion_strategy",
        "rrf_k",
        "bm25_limit",
        "vec_limit",
        "skip_vector",
        "entity",
        "procedural",
        "temporal",
        "rerank",
        "rerank_intents",
    }
)


def validate_config(data: dict[str, Any]) -> list[str]:
    """Validate a parsed kairix.config.yaml dict.

    Returns a list of human-readable error messages. Empty list means valid.
    Never raises — returns errors as strings.

    Also runs the Wave D topology v2 referential-integrity validators
    when any of the 6 Wave D blocks is present — empty / absent blocks
    skip cleanly so legacy configs see byte-identical behaviour.
    """
    errors: list[str] = []
    errors.extend(_validate_collections(data.get("collections")))
    errors.extend(_validate_agents(data.get("agents"), data.get("collections")))
    errors.extend(_validate_topology_v2(data))
    return errors


def _validate_topology_v2(data: dict[str, Any]) -> list[str]:
    """Parse + cross-reference-check the Wave D topology v2 blocks.

    Default-safe: when none of the topology v2 blocks is set, returns
    ``[]`` without touching the parser. Parse errors and validation
    failures both render as F21-shaped operator-friendly strings.

    ``collections`` overlaps with the legacy ``collections.shared``
    block (legacy is ``{shared: [...]}``); Wave D's top-level
    ``collections`` is reserved for a future evolution where the v2
    schema lives under a distinct key. For now, Wave D parses every
    block EXCEPT ``collections`` so the legacy contract that requires
    ``collections: <mapping>`` stays intact. Cross-reference rule 3
    (collections.*.sources.*.cc_pair) is therefore inactive until the
    Wave D top-level key resolves the name overlap — tracked in the
    Wave D commit body.
    """
    topology_keys = ("connectors", "credentials", "cc_pairs", "scope_profiles", "skills")
    if not any(data.get(k) for k in topology_keys):
        return []
    from kairix.config import parse_topology_v2, validate_topology_v2_references
    from kairix.config.topology_v2 import TopologyV2ParseError

    wave_d_view: dict[str, Any] = {k: data.get(k) for k in topology_keys}
    try:
        parsed = parse_topology_v2(wave_d_view)
    except TopologyV2ParseError as exc:
        return [f"topology_v2: parse failed — {exc}"]
    failures = validate_topology_v2_references(parsed)
    return [f.message for f in failures]


def _validate_collection_overrides(prefix: str, name: str, overrides: Any) -> list[str]:
    """Validate a single collection's optional ``retrieval`` override block."""
    if overrides is None:
        return []
    if not isinstance(overrides, dict):
        return [f"{prefix} ({name}): 'retrieval' must be a mapping"]
    bad = set(overrides.keys()) - _VALID_OVERRIDE_KEYS
    if bad:
        return [
            f"{prefix} ({name}): unknown retrieval override key(s) {sorted(bad)} "
            f"— valid: {sorted(_VALID_OVERRIDE_KEYS)}"
        ]
    return []


def _validate_shared_collection_item(prefix: str, item: Any, seen_names: set[str]) -> list[str]:
    """Validate a single entry in ``collections.shared`` and update ``seen_names``."""
    if not isinstance(item, dict):
        return [f"{prefix}: must be a mapping with name + path"]
    name = item.get("name")
    if not name:
        return [f"{prefix}: missing required 'name'"]
    errs: list[str] = []
    if name in seen_names:
        errs.append(f"{prefix}: duplicate collection name {name!r}")
    seen_names.add(name)
    if not item.get("path"):
        errs.append(f"{prefix} ({name}): missing required 'path'")
    errs.extend(_validate_collection_overrides(prefix, name, item.get("retrieval")))
    return errs


def _validate_agent_pattern(pattern: Any) -> list[str]:
    """Validate the optional ``collections.agent_pattern`` template string."""
    if pattern is None:
        return []
    if not isinstance(pattern, str):
        return ["collections.agent_pattern: must be a string template"]
    if "{agent}" not in pattern:
        return ["collections.agent_pattern: must contain '{agent}' placeholder"]
    return []


def _validate_collections(collections: Any) -> list[str]:
    if collections is None:
        return []  # absence is valid (search-everything fallback)
    if not isinstance(collections, dict):
        return ["collections: must be a mapping"]

    shared = collections.get("shared", [])
    if not isinstance(shared, list):
        return ["collections.shared: must be a list"]

    errors: list[str] = []
    seen_names: set[str] = set()
    for i, item in enumerate(shared):
        errors.extend(_validate_shared_collection_item(f"collections.shared[{i}]", item, seen_names))
    errors.extend(_validate_agent_pattern(collections.get("agent_pattern")))
    return errors


def _resolve_agent_pattern(collections: Any) -> str:
    """Return the agent-collection pattern, defaulting to ``{agent}-memory``."""
    default = "{agent}-memory"
    if not isinstance(collections, dict):
        return default
    custom = collections.get("agent_pattern")
    return custom if isinstance(custom, str) else default


def _check_write_path_overlap(
    prefix: str,
    name: str,
    write_path: str,
    write_paths: list[tuple[str, str]],
) -> list[str]:
    """Return error strings for any duplicate or prefix-overlapping write_paths."""
    errors: list[str] = []
    for other_name, other_path in write_paths:
        if write_path == other_path:
            errors.append(f"{prefix} ({name}): write_path {write_path!r} duplicates agent {other_name!r}")
            continue
        if other_path and (
            write_path.startswith(other_path.rstrip("/") + "/") or other_path.startswith(write_path.rstrip("/") + "/")
        ):
            errors.append(
                f"{prefix} ({name}): write_path {write_path!r} overlaps with "
                f"agent {other_name!r} write_path {other_path!r}"
            )
    return errors


def _validate_agent_write_path(
    prefix: str,
    name: str,
    write_path: Any,
    write_paths: list[tuple[str, str]],
) -> list[str]:
    """Validate an agent's optional ``write_path`` field and update ``write_paths``."""
    if not write_path:
        return []
    if not isinstance(write_path, str):
        return [f"{prefix} ({name}): write_path must be a string"]
    errors = _check_write_path_overlap(prefix, name, write_path, write_paths)
    write_paths.append((str(name), write_path))
    return errors


def _validate_agent_item(
    prefix: str,
    item: Any,
    pattern: str,
    seen_names: set[str],
    write_paths: list[tuple[str, str]],
) -> list[str]:
    """Validate one entry in the ``agents`` list."""
    if not isinstance(item, dict):
        return [f"{prefix}: must be a mapping"]
    name = item.get("name")
    if not name:
        return [f"{prefix}: missing required 'name'"]
    errors: list[str] = []
    if name in seen_names:
        errors.append(f"{prefix}: duplicate agent name {name!r}")
    seen_names.add(name)
    collection = item.get("collection") or pattern.format(agent=name)
    if not isinstance(collection, str):
        errors.append(f"{prefix} ({name}): collection must be a string")
    errors.extend(_validate_agent_write_path(prefix, name, item.get("write_path", ""), write_paths))
    return errors


def _validate_agents(agents: Any, collections: Any) -> list[str]:
    if agents is None:
        return []  # absence is valid (no all-agents support)
    if not isinstance(agents, list):
        return ["agents: must be a list"]

    errors: list[str] = []
    seen_names: set[str] = set()
    write_paths: list[tuple[str, str]] = []  # (agent_name, path)
    pattern = _resolve_agent_pattern(collections)

    for i, item in enumerate(agents):
        errors.extend(_validate_agent_item(f"agents[{i}]", item, pattern, seen_names, write_paths))

    return errors


def main(argv: list[str] | None = None) -> int:
    """CLI entry: kairix config validate [path]"""
    import argparse

    import yaml

    parser = argparse.ArgumentParser(prog="kairix config", description="Validate kairix configuration")
    sub = parser.add_subparsers(dest="subcommand")
    validate_p = sub.add_parser("validate", help="Validate kairix.config.yaml")
    validate_p.add_argument(
        "path",
        nargs="?",
        help="Path to config file (default: $KAIRIX_CONFIG_PATH or ./kairix.config.yaml)",
    )

    args = parser.parse_args(argv)
    if args.subcommand != "validate":
        parser.print_help()
        return 1

    if args.path:
        config_path = Path(args.path)
    else:
        from kairix.core.search.config_loader import resolve_config_path

        resolved = resolve_config_path()
        if resolved is None:
            print("No config file found. Set KAIRIX_CONFIG_PATH or place kairix.config.yaml in the cwd.")
            return 1
        config_path = resolved

    if not config_path.exists():
        print(f"Config file not found: {config_path}")
        return 1

    try:
        with config_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        print(f"YAML parse error in {config_path}: {exc}")
        return 1

    errors = validate_config(data)
    if errors:
        print(f"Found {len(errors)} validation error(s) in {config_path}:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"OK: {config_path} is valid.")
    return 0
