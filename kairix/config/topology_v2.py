"""Topology v2 operator-config dataclasses + YAML-dict parser (Wave D).

Six top-level optional blocks promoted into ``kairix.config.yaml`` per
ADR v2 §"Wave D":

* ``connectors:``      — connector instances (kind + name + config)
* ``credentials:``     — credential references (secret_name etc.)
* ``cc_pairs:``        — ConnectorCredentialPair triads
* ``collections:``     — retrieval buckets with source filters
* ``scope_profiles:``  — per-actor (collection, rights) entries
* ``skills:``          — composable retrieval strategies

All six are optional + permit empty lists, so existing operators see
zero behaviour change when they upgrade. The default-safe principle
(per the feature-flag architecture §2.1) is structurally enforced — a
new operator deployment without any topology v2 blocks parses to an
all-empty :class:`TopologyV2Config`, and the validator returns no
failures.

Parsing is permissive at the boundary (missing optional fields default
to None / empty tuple) but strict in shape: a dict where a list is
expected raises :exc:`TopologyV2ParseError`. The validator surface
(:mod:`kairix.config.topology_v2_validators`) handles cross-reference
checks separately so callers can stage parse → render → validate as
distinct steps in a CI pipeline.

F42 frozen-dataclass discipline: every public value object is
``@dataclass(frozen=True)``; collections are immutable tuples; no
``dict[str, Any]`` at the public boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kairix.core.protocols import CCPairAccessType, F39Tier, ScopeProfileActorKind

# F17 — collection-source path filter literal duplicated across two
# nested parsers (collections.* + skills.*.task_collections.*) plus the
# validator's diagnostic strings. Pull to a single constant.
_DEFAULT_PATH_FILTER = "*"


class TopologyV2ParseError(ValueError):
    """Raised when a Wave D config block has the wrong structural shape.

    Distinct from cross-reference validation failures (which are returned
    as a list of :class:`ValidationFailure` records, not raised). Parse
    errors signal "this YAML can't even be loaded into the dataclass
    surface"; validation failures signal "the loaded data has dangling
    references".
    """


# ---------------------------------------------------------------------------
# Public value objects — frozen dataclasses (F42).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConnectorConfig:
    """One entry in the operator's ``connectors:`` block.

    ``connector_specific_config`` is a tuple of (key, value-as-str) pairs
    so the dataclass stays frozen + hashable; Wave E reads it back as a
    dict at the per-connector boundary.
    """

    id: str
    kind: str
    name: str
    connector_specific_config: tuple[tuple[str, str], ...] = ()
    refresh_freq_seconds: int | None = None
    prune_freq_seconds: int | None = None
    perm_sync_freq_seconds: int | None = None
    default_sensitivity: F39Tier = "internal"


@dataclass(frozen=True)
class CredentialConfig:
    """One entry in the operator's ``credentials:`` block."""

    id: str
    kind: str
    secret_name: str
    user_id: str | None = None
    admin_public: bool = False


@dataclass(frozen=True)
class CCPairConfig:
    """One entry in the operator's ``cc_pairs:`` block.

    ``connector`` references the :attr:`ConnectorConfig.id`;
    ``credential`` references :attr:`CredentialConfig.id` (or ``None``
    for credential-less connectors like local Obsidian).

    ``collection_filters`` is the optional per-cc_pair allowlist — when
    populated, only collections whose source filters reference this
    cc_pair AND match one of these globs participate in routing.
    """

    id: str
    connector: str
    credential: str | None
    name: str
    access_type: CCPairAccessType = "PRIVATE"
    collection_filters: tuple[str, ...] = ()


@dataclass(frozen=True)
class CollectionSourceConfig:
    """One (cc_pair, path_filter, optional sensitivity_min) row inside a Collection."""

    cc_pair: str
    path_filter: str = _DEFAULT_PATH_FILTER
    sensitivity_min: F39Tier | None = None


@dataclass(frozen=True)
class CollectionConfig:
    """One entry in the operator's ``collections:`` block."""

    name: str
    sources: tuple[CollectionSourceConfig, ...]


@dataclass(frozen=True)
class ScopeEntryConfig:
    """One (actor_id, collection_name, mode) row inside a ScopeProfile.

    ``mode`` is one of ``read`` / ``write`` / ``read_write`` — Wave D
    treats absent ``mode`` as ``read``, matching the default-safe
    least-permissive principle.
    """

    actor_id: str
    collection_name: str
    mode: str = "read"


@dataclass(frozen=True)
class ScopeProfileConfig:
    """One entry in the operator's ``scope_profiles:`` block.

    ``actor_kind`` defaults to ``agent`` for the common case (most
    scope profiles bind one agent identity); operators with team /
    group / human profiles set it explicitly.
    """

    name: str
    entries: tuple[ScopeEntryConfig, ...]
    actor_kind: ScopeProfileActorKind = "agent"


@dataclass(frozen=True)
class SkillSourceConfig:
    """One (cc_pair, path_filter) row inside a SkillTaskCollectionConfig."""

    cc_pair: str
    path_filter: str = _DEFAULT_PATH_FILTER


@dataclass(frozen=True)
class SkillTaskCollectionConfig:
    """One task_collection inside a Skill — virtual aggregator of cc_pairs."""

    name: str
    sources: tuple[SkillSourceConfig, ...]


@dataclass(frozen=True)
class SkillConfig:
    """One entry in the operator's ``skills:`` block."""

    name: str
    task_collections: tuple[SkillTaskCollectionConfig, ...]


@dataclass(frozen=True)
class TopologyV2Config:
    """Aggregator over the 6 Wave D blocks — frozen, all-empty is valid.

    Empty-default tuples mean an operator config without any topology v2
    blocks parses successfully to ``TopologyV2Config()`` with no
    behaviour change (default-safe).
    """

    connectors: tuple[ConnectorConfig, ...] = ()
    credentials: tuple[CredentialConfig, ...] = ()
    cc_pairs: tuple[CCPairConfig, ...] = ()
    collections: tuple[CollectionConfig, ...] = ()
    scope_profiles: tuple[ScopeProfileConfig, ...] = ()
    skills: tuple[SkillConfig, ...] = ()


# ---------------------------------------------------------------------------
# Parser — YAML-dict → frozen dataclass tree.
# ---------------------------------------------------------------------------


def _require_list(prefix: str, value: Any) -> list[Any]:
    """Validate that ``value`` is a list (or None → []) and return it.

    Centralises the "block must be a list" type guard so every block
    parser raises the same shape of :exc:`TopologyV2ParseError`.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise TopologyV2ParseError(
            f"{prefix}: must be a list. fix: change the value to a YAML list. next: run kairix config validate"
        )
    return value


def _require_dict(prefix: str, value: Any) -> dict[str, Any]:
    """Validate that ``value`` is a dict and return it."""
    if not isinstance(value, dict):
        raise TopologyV2ParseError(
            f"{prefix}: must be a mapping. fix: change the entry to a YAML mapping. next: run kairix config validate"
        )
    return value


def _require_str(prefix: str, value: Any, *, field: str) -> str:
    """Validate that ``value`` is a non-empty string for ``field``."""
    if not isinstance(value, str) or not value.strip():
        raise TopologyV2ParseError(
            f"{prefix}: {field!r} is required and must be a non-empty string. "
            f"fix: add `{field}: <value>` to the entry. next: run kairix config validate"
        )
    return value


def _optional_str(value: Any) -> str | None:
    """Coerce an optional YAML scalar to ``str | None``."""
    if value is None:
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    """Coerce an optional YAML scalar to ``int | None``."""
    if value is None:
        return None
    return int(value)


def _parse_connector_specific_config(value: Any) -> tuple[tuple[str, str], ...]:
    """Render a connector_specific_config mapping as a tuple of (k, v) pairs."""
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise TopologyV2ParseError(
            "connectors[*].connector_specific_config: must be a mapping. "
            "fix: use `key: value` pairs. next: run kairix config validate"
        )
    return tuple((str(k), str(v)) for k, v in sorted(value.items()))


def _parse_one_connector(prefix: str, raw: Any) -> ConnectorConfig:
    item = _require_dict(prefix, raw)
    return ConnectorConfig(
        id=_require_str(prefix, item.get("id"), field="id"),
        kind=_require_str(prefix, item.get("kind"), field="kind"),
        name=_require_str(prefix, item.get("name"), field="name"),
        connector_specific_config=_parse_connector_specific_config(item.get("connector_specific_config")),
        refresh_freq_seconds=_optional_int(item.get("refresh_freq_seconds")),
        prune_freq_seconds=_optional_int(item.get("prune_freq_seconds")),
        perm_sync_freq_seconds=_optional_int(item.get("perm_sync_freq_seconds")),
        default_sensitivity=_parse_sensitivity(item.get("default_sensitivity"), default="internal"),
    )


def _parse_sensitivity(value: Any, *, default: F39Tier) -> F39Tier:
    """Validate that an optional sensitivity value is one of the F39 tiers."""
    if value is None:
        return default
    if value not in ("public", "internal", "confidential", "restricted"):
        raise TopologyV2ParseError(
            f"sensitivity={value!r} is not a valid F39 tier. "
            "fix: use one of public/internal/confidential/restricted. "
            "next: run kairix config validate"
        )
    # F3-rationale: the ``in`` guard above is a closed-set check that mypy doesn't narrow to F39Tier.
    return value  # type: ignore[no-any-return]  # F3-rationale: closed-set guard above narrows runtime but mypy can't infer Literal.


def _parse_one_credential(prefix: str, raw: Any) -> CredentialConfig:
    item = _require_dict(prefix, raw)
    return CredentialConfig(
        id=_require_str(prefix, item.get("id"), field="id"),
        kind=_require_str(prefix, item.get("kind"), field="kind"),
        secret_name=_require_str(prefix, item.get("secret_name"), field="secret_name"),
        user_id=_optional_str(item.get("user_id")),
        admin_public=bool(item.get("admin_public", False)),
    )


def _parse_access_type(value: Any) -> CCPairAccessType:
    """Validate access_type — one of PUBLIC / PRIVATE / SYNC; default PRIVATE."""
    if value is None:
        return "PRIVATE"
    if value not in ("PUBLIC", "PRIVATE", "SYNC"):
        raise TopologyV2ParseError(
            f"cc_pairs[*].access_type={value!r} is not valid. "
            "fix: use one of PUBLIC/PRIVATE/SYNC. next: run kairix config validate"
        )
    # F3-rationale: closed-set guard above; mypy doesn't narrow.
    return value  # type: ignore[no-any-return]  # F3-rationale: closed-set guard above narrows runtime but mypy can't infer Literal.


def _parse_one_cc_pair(prefix: str, raw: Any) -> CCPairConfig:
    item = _require_dict(prefix, raw)
    raw_filters = item.get("collection_filters")
    if raw_filters is None:
        filters: tuple[str, ...] = ()
    elif isinstance(raw_filters, list):
        filters = tuple(str(f) for f in raw_filters)
    else:
        raise TopologyV2ParseError(
            f"{prefix}.collection_filters: must be a list of glob strings. "
            "fix: render as a YAML list. next: run kairix config validate"
        )
    return CCPairConfig(
        id=_require_str(prefix, item.get("id"), field="id"),
        connector=_require_str(prefix, item.get("connector"), field="connector"),
        credential=_optional_str(item.get("credential")),
        name=_require_str(prefix, item.get("name"), field="name"),
        access_type=_parse_access_type(item.get("access_type")),
        collection_filters=filters,
    )


def _parse_collection_source(prefix: str, raw: Any) -> CollectionSourceConfig:
    item = _require_dict(prefix, raw)
    sensitivity_min: F39Tier | None = None
    if "sensitivity_min" in item:
        sensitivity_min = _parse_sensitivity(item.get("sensitivity_min"), default="internal")
    return CollectionSourceConfig(
        cc_pair=_require_str(prefix, item.get("cc_pair"), field="cc_pair"),
        path_filter=str(item.get("path_filter") or _DEFAULT_PATH_FILTER),
        sensitivity_min=sensitivity_min,
    )


def _parse_one_collection(prefix: str, raw: Any) -> CollectionConfig:
    item = _require_dict(prefix, raw)
    sources_raw = _require_list(f"{prefix}.sources", item.get("sources"))
    sources = tuple(_parse_collection_source(f"{prefix}.sources[{i}]", s) for i, s in enumerate(sources_raw))
    return CollectionConfig(
        name=_require_str(prefix, item.get("name"), field="name"),
        sources=sources,
    )


def _parse_scope_entry(prefix: str, raw: Any) -> ScopeEntryConfig:
    item = _require_dict(prefix, raw)
    mode = item.get("mode", "read")
    if mode not in ("read", "write", "read_write"):
        raise TopologyV2ParseError(
            f"{prefix}.mode={mode!r} is not valid. "
            "fix: use one of read/write/read_write. next: run kairix config validate"
        )
    return ScopeEntryConfig(
        actor_id=_require_str(prefix, item.get("actor_id"), field="actor_id"),
        collection_name=_require_str(prefix, item.get("collection_name"), field="collection_name"),
        mode=str(mode),
    )


def _parse_actor_kind(value: Any) -> ScopeProfileActorKind:
    """Validate actor_kind — one of agent/human/team/group/skill; default agent."""
    if value is None:
        return "agent"
    if value not in ("agent", "human", "team", "group", "skill"):
        raise TopologyV2ParseError(
            f"scope_profiles[*].actor_kind={value!r} is not valid. "
            "fix: use one of agent/human/team/group/skill. next: run kairix config validate"
        )
    # F3-rationale: closed-set guard above; mypy doesn't narrow.
    return value  # type: ignore[no-any-return]  # F3-rationale: closed-set guard above narrows runtime but mypy can't infer Literal.


def _parse_one_scope_profile(prefix: str, raw: Any) -> ScopeProfileConfig:
    item = _require_dict(prefix, raw)
    entries_raw = _require_list(f"{prefix}.entries", item.get("entries"))
    entries = tuple(_parse_scope_entry(f"{prefix}.entries[{i}]", e) for i, e in enumerate(entries_raw))
    return ScopeProfileConfig(
        name=_require_str(prefix, item.get("name"), field="name"),
        entries=entries,
        actor_kind=_parse_actor_kind(item.get("actor_kind")),
    )


def _parse_skill_source(prefix: str, raw: Any) -> SkillSourceConfig:
    item = _require_dict(prefix, raw)
    return SkillSourceConfig(
        cc_pair=_require_str(prefix, item.get("cc_pair"), field="cc_pair"),
        path_filter=str(item.get("path_filter") or _DEFAULT_PATH_FILTER),
    )


def _parse_skill_task_collection(prefix: str, raw: Any) -> SkillTaskCollectionConfig:
    item = _require_dict(prefix, raw)
    sources_raw = _require_list(f"{prefix}.sources", item.get("sources"))
    sources = tuple(_parse_skill_source(f"{prefix}.sources[{i}]", s) for i, s in enumerate(sources_raw))
    return SkillTaskCollectionConfig(
        name=_require_str(prefix, item.get("name"), field="name"),
        sources=sources,
    )


def _parse_one_skill(prefix: str, raw: Any) -> SkillConfig:
    item = _require_dict(prefix, raw)
    task_collections_raw = _require_list(f"{prefix}.task_collections", item.get("task_collections"))
    task_collections = tuple(
        _parse_skill_task_collection(f"{prefix}.task_collections[{i}]", t) for i, t in enumerate(task_collections_raw)
    )
    return SkillConfig(
        name=_require_str(prefix, item.get("name"), field="name"),
        task_collections=task_collections,
    )


def parse_topology_v2(data: dict[str, Any]) -> TopologyV2Config:
    """Parse a YAML-loaded dict into a :class:`TopologyV2Config`.

    All six blocks are optional. Empty data or data without any
    topology v2 keys parses to ``TopologyV2Config()``. Structural type
    errors raise :exc:`TopologyV2ParseError`; cross-reference checks
    are deferred to :func:`validate_topology_v2_references`.

    Per the F42 boundary discipline: returns a frozen dataclass tree;
    callers never touch ``dict[str, Any]`` again after parsing.
    """
    connectors_raw = _require_list("connectors", data.get("connectors"))
    credentials_raw = _require_list("credentials", data.get("credentials"))
    cc_pairs_raw = _require_list("cc_pairs", data.get("cc_pairs"))
    collections_raw = _require_list("collections", data.get("collections"))
    scope_profiles_raw = _require_list("scope_profiles", data.get("scope_profiles"))
    skills_raw = _require_list("skills", data.get("skills"))

    return TopologyV2Config(
        connectors=tuple(_parse_one_connector(f"connectors[{i}]", c) for i, c in enumerate(connectors_raw)),
        credentials=tuple(_parse_one_credential(f"credentials[{i}]", c) for i, c in enumerate(credentials_raw)),
        cc_pairs=tuple(_parse_one_cc_pair(f"cc_pairs[{i}]", p) for i, p in enumerate(cc_pairs_raw)),
        collections=tuple(_parse_one_collection(f"collections[{i}]", c) for i, c in enumerate(collections_raw)),
        scope_profiles=tuple(
            _parse_one_scope_profile(f"scope_profiles[{i}]", s) for i, s in enumerate(scope_profiles_raw)
        ),
        skills=tuple(_parse_one_skill(f"skills[{i}]", s) for i, s in enumerate(skills_raw)),
    )
