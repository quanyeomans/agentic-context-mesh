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

# F17: 'collections' top-level YAML key, referenced ≥3 times across the
# validator. Hoisted so a future rename has a single edit site.
_COLLECTIONS_KEY = "collections"

# Synthetic collections fed by projectors (no filesystem path). ADR-036.
_SYNTHETIC_COLLECTION_NAMES: frozenset[str] = frozenset({"entity-summaries"})

# F17: the reference-library collection name appears in several validator
# branches (path auto-correction hint + the relative-path harmoniser accept).
# Hoisted so the literal has a single edit site and the module stays under the
# no_duplicate_string ceiling (S1192).
_REFERENCE_LIBRARY_NAME = "reference-library"

# reference_library.index modes valid in kairix.config.yaml (#475). Kept as a
# literal tuple (mirrors _VALID_OVERRIDE_KEYS) so validation doesn't drag the
# config_loader dataclasses in as a runtime dependency.
_VALID_REFLIB_INDEX_MODES = ("eager", "lazy", "skip")

# F17 — the top-level YAML key appears in every reference_library error string.
_REFLIB_KEY = "reference_library"


def validate_config(
    data: dict[str, Any],
    *,
    document_root: Path | None = None,
    reflib_root: Path | None = None,
) -> list[str]:
    """Validate a parsed kairix.config.yaml dict.

    Returns a list of human-readable error messages. Empty list means valid.
    Never raises — returns errors as strings.

    Also runs the Wave D topology referential-integrity validators
    when any of the 6 Wave D blocks is present — empty / absent blocks
    skip cleanly so legacy configs see byte-identical behaviour.

    Filesystem-resolving path checks fire when ``document_root`` (or its
    env-var fallback ``$KAIRIX_DOCUMENT_ROOT``) points at a real
    directory — i.e. we're in a deployed environment rather than a
    test/CI shell. Each declared collection path must resolve to an
    existing directory; the reference-library collection is auto-resolved
    against ``reflib_root`` (or ``$KAIRIX_REFLIB_ROOT``) per the same
    harmoniser the scanner uses (catches the silent
    "path: reference-library" misconfiguration class).

    Tests pass ``document_root`` / ``reflib_root`` explicitly so they
    never mutate process env (F2-clean).
    """
    errors: list[str] = []
    errors.extend(_validate_collections(data.get(_COLLECTIONS_KEY)))
    errors.extend(_validate_agents(data.get("agents"), data.get(_COLLECTIONS_KEY)))
    errors.extend(_validate_reference_library(data.get(_REFLIB_KEY)))
    errors.extend(_validate_topology(data))
    errors.extend(
        _validate_collection_paths_resolve(
            data.get(_COLLECTIONS_KEY),
            document_root=document_root,
            reflib_root=reflib_root,
        )
    )
    return errors


def _validate_collection_paths_resolve(
    collections: Any,
    *,
    document_root: Path | None = None,
    reflib_root: Path | None = None,
) -> list[str]:
    """Every declared collection path must resolve to an existing dir.

    Silent-skip when ``document_root`` is None / not a real dir — that's
    the test-environment shape, not a real deployment. In a real
    deployment, an unresolvable path was previously a per-scan WARNING
    that operators missed; this turns it into a hard validate-time error
    with F21-actionable remediation.

    Reference-library is auto-resolved against ``reflib_root`` using the
    same logic as ``harmonise_reference_library`` so the operator sees
    the same "auto-corrected" outcome at validate time that they'd see
    at scan time.

    Production callers leave the kwargs as None — the boundary read
    happens here via ``KairixPaths.resolve()``. Tests pass explicit
    paths constructed in a tmp_path so they never mutate process env.
    """
    if collections is None or not isinstance(collections, dict):
        return []
    shared = collections.get("shared", [])
    if not isinstance(shared, list):
        return []

    # Default-skip when document_root is not explicitly supplied — keeps
    # legacy callers (existing schema-only validate_config tests, plus
    # the topology unit-test fixtures) byte-identical. Operators
    # running `kairix config validate` opt into path resolution by
    # passing the resolved KairixPaths.document_root from the CLI
    # boundary (see kairix.core.search.config_validator's CLI main).
    if document_root is None:
        return []
    if not document_root.is_dir():
        return []  # Document root supplied but doesn't exist — test shell, skip.

    if reflib_root is None:
        from kairix.paths import reference_library_root

        reflib_root = reference_library_root()

    errors: list[str] = []
    for i, item in enumerate(shared):
        error = _check_one_collection_path(i, item, document_root, reflib_root)
        if error:
            errors.append(error)
    return errors


def _shared_prefix(i: int) -> str:
    """Return the canonical `collections.shared[i]` error prefix.

    F17: extracted to one helper so the prefix template lives in one
    place (3+ call sites once the path-resolution check is in).
    """
    return f"collections.shared[{i}]"


def _check_one_collection_path(
    index: int,
    item: Any,
    document_root: Path,
    reflib_root: Path,
) -> str | None:
    """Return an actionable error string for one collection entry, or None when it resolves."""
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    raw_path = item.get("path")
    if not name or not raw_path:
        return None
    candidate = Path(raw_path) if Path(raw_path).is_absolute() else document_root / raw_path
    if candidate.is_dir():
        return None
    common = (
        f"{_shared_prefix(index)} ({name}): declared path {raw_path!r} resolves to {candidate} which does not exist"
    )
    # Reference-library auto-correction: if the declared path doesn't
    # resolve but $KAIRIX_REFLIB_ROOT does, return the actionable hint
    # that points operators at the canonical path.
    if name == _REFERENCE_LIBRARY_NAME and reflib_root and reflib_root.is_dir():
        return (
            f"{common}; the scanner auto-corrects this to {reflib_root} at runtime. "
            f"fix: change `path: {raw_path}` to `path: {reflib_root}` in your kairix.config.yaml. "
            f"next: kairix config validate. "
            f"run: docker compose restart kairix kairix-worker"
        )
    return (
        f"{common}. "
        f"fix: change `path:` to an absolute path that exists on the deployment, OR "
        f"remove this collection from kairix.config.yaml if it's no longer in scope. "
        f"next: kairix config validate. "
        f"run: ls {candidate.parent} to see what's actually mounted there."
    )


def _validate_reference_library(block: Any) -> list[str]:
    """Validate the optional ``reference_library:`` block (#475).

    Absence is valid (eager default — today's behaviour). Unknown keys
    and invalid ``index`` values render as F21-shaped errors naming the
    three valid modes so a typo never silently re-embeds the bundled
    library on a fresh install.
    """
    if block is None:
        return []
    if not isinstance(block, dict):
        return [
            f"{_REFLIB_KEY}: must be a mapping with an 'index' key. "
            f"fix: declare `{_REFLIB_KEY}:` as a block, e.g. `{_REFLIB_KEY}:` then `  index: eager`. "
            "next: kairix config validate. "
            "run: kairix config validate"
        ]
    errors: list[str] = []
    unknown = set(block.keys()) - {"index"}
    if unknown:
        errors.append(
            f"{_REFLIB_KEY}: unknown key(s) {sorted(unknown)} — valid: ['index']. "
            f"fix: keep only `index:` under `{_REFLIB_KEY}:`. "
            "next: kairix config validate. "
            "run: kairix config validate"
        )
    mode = block.get("index", "eager")
    if mode not in _VALID_REFLIB_INDEX_MODES:
        errors.append(
            f"{_REFLIB_KEY}.index: {mode!r} is not a valid mode — valid options: eager | lazy | skip. "
            "fix: set index to eager (bundled library embeds with your documents — default), "
            "lazy (your documents embed first; the library follows on later runs), or "
            "skip (the library is never embedded). "
            "next: kairix config validate. "
            "run: docker compose restart kairix kairix-worker"
        )
    return errors


def _validate_topology(data: dict[str, Any]) -> list[str]:
    """Parse + cross-reference-check the Wave D topology blocks.

    Default-safe: when the ``topology:`` parent key is absent (or
    null), returns ``[]`` without touching the parser. Parse errors and
    validation failures both render as F21-shaped operator-friendly
    strings.

    Post #305: the six Wave D blocks (connectors / credentials /
    cc_pairs / collections / scope_profiles / skills) live under a
    single ``topology:`` parent key so the Wave D ``collections:``
    block stops colliding with the legacy top-level
    ``collections.shared`` dict shape. With the namespace fix all five
    cross-reference rules — including rule 3
    (``collections.*.sources.*.cc_pair``) — now fire end-to-end against
    the Wave D ``topology.collections:`` declarations.

    A config written before the rename (carrying the legacy topology key) is normalized to
    ``topology`` first via :func:`normalize_topology_key` (PLA-287) so the
    default-safe guard below sees the block under its canonical name.
    """
    from kairix.config import normalize_topology_key, parse_topology, validate_topology_references
    from kairix.config.topology import TOPOLOGY_CONFIG_KEY, TopologyParseError

    data = normalize_topology_key(data)
    if data.get(TOPOLOGY_CONFIG_KEY) is None:
        return []

    try:
        parsed = parse_topology(data)
    except TopologyParseError as exc:
        return [f"topology: parse failed — {exc}"]
    failures = validate_topology_references(parsed)
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
    path_val = item.get("path")
    # ADR-036: synthetic projector-fed collections (e.g. entity-summaries)
    # carry no filesystem path. The reference-library relative form
    # (path == "reference-library") is auto-corrected at runtime by
    # harmonise_reference_library(), so accept it here too.
    is_synthetic = name in _SYNTHETIC_COLLECTION_NAMES
    path_auto_harmonised = name == _REFERENCE_LIBRARY_NAME and path_val == _REFERENCE_LIBRARY_NAME
    if not path_val and not is_synthetic and not path_auto_harmonised:
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
        errors.extend(_validate_shared_collection_item(_shared_prefix(i), item, seen_names))
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


def main(
    argv: list[str] | None = None,
    *,
    document_root: Path | None = None,
    reflib_root: Path | None = None,
) -> int:
    """CLI entry: kairix config validate [path]

    ``document_root`` / ``reflib_root`` are test seams — production
    callers leave them as None and the CLI resolves them at the
    boundary via KairixPaths.
    """
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

    # Opt into path resolution at the operator-facing CLI boundary so
    # `kairix config validate` catches misconfigured paths but unit tests
    # of the schema parser stay silent on the new check. Tests inject
    # document_root / reflib_root directly; production reads via the
    # KairixPaths boundary.
    if document_root is None:
        from kairix.paths import KairixPaths

        document_root = KairixPaths.resolve().document_root
    if reflib_root is None:
        from kairix.paths import reference_library_root

        reflib_root = reference_library_root()
    errors = validate_config(
        data,
        document_root=document_root,
        reflib_root=reflib_root,
    )
    if errors:
        print(f"Found {len(errors)} validation error(s) in {config_path}:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"OK: {config_path} is valid.")
    return 0
