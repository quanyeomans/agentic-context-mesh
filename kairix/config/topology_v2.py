"""Topology v2 operator-config dataclasses + YAML-dict parser (Wave D).

Six nested blocks under a single ``topology_v2:`` parent key in
``kairix.config.yaml`` per ADR v2 §"Wave D":

.. code-block:: yaml

    topology_v2:
      connectors:      []   # connector instances (kind + name + config)
      credentials:     []   # credential references (secret_name etc.)
      cc_pairs:        []   # ConnectorCredentialPair triads
      collections:     []   # retrieval buckets with source filters
      scope_profiles:  []   # per-actor (collection, rights) entries
      skills:          []   # composable retrieval strategies

The parent-key namespace (#305) keeps the Wave D ``collections:`` block
from colliding with the legacy top-level ``collections.shared`` dict
shape and aligns the YAML surface with the ``topology_v2_config``
feature flag name.

All six nested blocks are optional + permit empty lists, so existing
operators see zero behaviour change when they upgrade. The default-safe
principle (per the feature-flag architecture §2.1) is structurally
enforced — a new operator deployment without a ``topology_v2:`` block
parses to an all-empty :class:`TopologyV2Config`, and the validator
returns no failures.

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

    GH #373 — ``default_in_scope`` controls whether this entry surfaces
    under a "default" search (``collections=None``). When True (the
    back-compat default), the entry participates in the default-in-scope
    superset; when False, the entry is only reachable via explicit
    ``collections=[name]`` opt-in (e.g. ``reflib``).
    """

    actor_id: str
    collection_name: str
    mode: str = "read"
    default_in_scope: bool = True


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


def _parse_default_in_scope(prefix: str, value: Any) -> bool:
    """Validate ``default_in_scope`` is a bool — reject str / int / None.

    GH #373: this field gates whether the entry surfaces under default
    search. Operators conflate ``true`` / ``"yes"`` / ``1``; the parser
    rejects every non-bool to surface the misconfiguration loudly rather
    than silently coercing to ``True`` (the back-compat default).
    """
    if value is None or not isinstance(value, bool):
        observed_type = type(value).__name__
        raise TopologyV2ParseError(
            f"{prefix}.default_in_scope is not a bool (got {observed_type!r}). "
            f"fix: use `default_in_scope: true` or `default_in_scope: false` (lowercase YAML bool). "
            f"next: run kairix config validate. "
            f"run: grep -n default_in_scope kairix.config.yaml"
        )
    return bool(value)


def _parse_scope_entry(prefix: str, raw: Any) -> ScopeEntryConfig:
    item = _require_dict(prefix, raw)
    mode = item.get("mode", "read")
    if mode not in ("read", "write", "read_write"):
        raise TopologyV2ParseError(
            f"{prefix}.mode={mode!r} is not valid. "
            "fix: use one of read/write/read_write. next: run kairix config validate"
        )
    default_in_scope: bool = True
    if "default_in_scope" in item:
        default_in_scope = _parse_default_in_scope(prefix, item.get("default_in_scope"))
    return ScopeEntryConfig(
        actor_id=_require_str(prefix, item.get("actor_id"), field="actor_id"),
        collection_name=_require_str(prefix, item.get("collection_name"), field="collection_name"),
        mode=str(mode),
        default_in_scope=default_in_scope,
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


def _parse_applies_to(prefix: str, raw: Any) -> tuple[str, ...]:
    """Parse the optional ``applies_to:`` list on a scope_profile.

    Accepts ``None`` (legacy shape — no fan-out), a list of agent names,
    or the wildcard ``["*"]``. Empty-list rejected loudly to catch the
    "I deleted everyone from the list" misconfiguration.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise TopologyV2ParseError(
            f"{prefix}.applies_to: must be a list of agent names or ['*'] for wildcard. "
            f"fix: render as a YAML list (e.g. `applies_to: [agent-alpha, agent-beta]` "
            f'or `applies_to: ["*"]`). next: run kairix config validate.'
        )
    if not raw:
        raise TopologyV2ParseError(
            f"{prefix}.applies_to: must not be an empty list. "
            f'fix: list at least one agent name or use `["*"]` for wildcard fan-out. '
            f"next: run kairix config validate."
        )
    return tuple(str(name) for name in raw)


def _parse_one_scope_profile_raw(
    prefix: str, raw: Any
) -> tuple[str, ScopeProfileActorKind, tuple[str, ...], tuple[ScopeEntryConfig, ...]]:
    """Internal — parse the raw scope_profile shape including ``applies_to``.

    Returns ``(name, actor_kind, applies_to, entries)``. Wildcard
    expansion happens in :func:`parse_topology_v2` so the expansion has
    the registered-agents list in scope.
    """
    item = _require_dict(prefix, raw)
    entries_raw = _require_list(f"{prefix}.entries", item.get("entries"))
    entries = tuple(_parse_scope_entry(f"{prefix}.entries[{i}]", e) for i, e in enumerate(entries_raw))
    return (
        _require_str(prefix, item.get("name"), field="name"),
        _parse_actor_kind(item.get("actor_kind")),
        _parse_applies_to(prefix, item.get("applies_to")),
        entries,
    )


def _parse_agents_block(raw: Any) -> tuple[str, ...]:
    """Parse the top-level ``agents:`` block into a tuple of agent names.

    Accepts two shapes — the flat-string list common in tests and v2
    operator configs (``agents: [shape, builder]``) and the legacy
    list-of-dicts shape (``agents: [{name: shape, paths: [...]}, ...]``)
    from the pre-v2 registry. Both surface as a flat name tuple here so
    the wildcard expansion + agent-reachability validators in
    :func:`parse_topology_v2` have a uniform input.

    Empty / missing → empty tuple (the wildcard validator then catches
    "wildcard with no agents" as a separate error).
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise TopologyV2ParseError(
            "agents: must be a list of agent names or list of {name: ...} mappings. "
            "fix: render the agents block as a YAML list. next: run kairix config validate."
        )
    names: list[str] = []
    for item in raw:
        if isinstance(item, str):
            if item.strip():
                names.append(item)
        elif isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name)
    return tuple(names)


def _expand_wildcard_profiles(
    raw_profiles: list[tuple[str, ScopeProfileActorKind, tuple[str, ...], tuple[ScopeEntryConfig, ...]]],
    agents: tuple[str, ...],
) -> tuple[ScopeProfileConfig, ...]:
    """Expand ``applies_to`` fan-out into materialised per-actor profiles.

    Each raw profile becomes:

      * If ``applies_to == ()`` — one ``ScopeProfileConfig`` with the
        raw entries (legacy shape; entries already carry their own
        ``actor_id``).
      * If ``applies_to == ("*",)`` — N materialised profiles, one per
        registered agent. Each materialised profile has its entries'
        ``actor_id`` rewritten to the target agent so downstream
        consumers (resolver / DB applier) see concrete rows.
      * If ``applies_to == ("a", "b", ...)`` — N materialised profiles,
        one per named agent.

    Wildcard with zero registered agents raises F21 — it's a
    misconfiguration the operator wants to hear about loudly.
    """
    expanded: list[ScopeProfileConfig] = []
    for name, actor_kind, applies_to, entries in raw_profiles:
        if not applies_to:
            expanded.append(ScopeProfileConfig(name=name, entries=entries, actor_kind=actor_kind))
            continue
        if applies_to == ("*",):
            if not agents:
                raise TopologyV2ParseError(
                    f"scope_profiles[name={name!r}].applies_to=['*'] but the agents block "
                    f"is empty. The wildcard expands to zero profiles — a misconfiguration. "
                    f"fix: declare at least one agent in the top-level `agents:` block, OR "
                    f"replace the wildcard with an explicit `applies_to: [agent-name, ...]` list. "
                    f"next: run kairix config validate. "
                    f"run: grep -n '^agents:' kairix.config.yaml"
                )
            targets: tuple[str, ...] = agents
        else:
            targets = applies_to
        for target_actor in targets:
            rewritten = tuple(
                ScopeEntryConfig(
                    actor_id=target_actor,
                    collection_name=e.collection_name,
                    mode=e.mode,
                    default_in_scope=e.default_in_scope,
                )
                for e in entries
            )
            expanded.append(
                ScopeProfileConfig(
                    name=f"{name}::{target_actor}",
                    entries=rewritten,
                    actor_kind=actor_kind,
                )
            )
    return tuple(expanded)


def _validate_collection_references(
    profiles: tuple[ScopeProfileConfig, ...],
    collections: tuple[CollectionConfig, ...],
) -> None:
    """F21 — every scope-entry ``collection_name`` must exist in collections.

    Dangling references are the most common config typo; surfacing them
    at parse time (rather than at search time with an empty result)
    keeps the misconfiguration cycle short.

    Skipped when the collections block is empty (back-compat with
    legacy configs that declare scope_profiles without a paired
    collections block; the standalone
    :func:`kairix.config.topology_v2_validators.validate_topology_v2_references`
    surface remains the canonical cross-reference path for those cases).
    """
    if not collections:
        return
    declared = {c.name for c in collections}
    for profile in profiles:
        for entry in profile.entries:
            if entry.collection_name not in declared:
                raise TopologyV2ParseError(
                    f"scope_profiles[name={profile.name!r}].entries references "
                    f"collection_name={entry.collection_name!r} which is not declared in "
                    f"topology_v2.collections. "
                    f"fix: add `{entry.collection_name}` to the collections list OR remove "
                    f"the entry from the scope profile. "
                    f"next: run kairix config validate. "
                    f"run: grep -n 'name:' kairix.config.yaml"
                )


def _validate_agent_reachability(
    profiles: tuple[ScopeProfileConfig, ...],
    agents: tuple[str, ...],
) -> None:
    """F21 — every registered agent must be covered by at least one profile.

    A forgotten agent means default search returns zero results for
    them — a silent failure mode the validator catches at parse time so
    the operator sees the gap immediately rather than via an empty MCP
    response.

    Coverage rule: an agent counts as covered when its name appears as
    ``entry.actor_id`` in any materialised profile. Wildcard
    ``applies_to: ["*"]`` materialises one profile per registered agent
    so every agent is covered automatically; explicit
    ``applies_to: [name, ...]`` materialises only the listed names so
    registered-but-unlisted agents surface here as misconfigurations.
    """
    if not agents:
        return
    covered: set[str] = set()
    for profile in profiles:
        for entry in profile.entries:
            covered.add(entry.actor_id)
    unreachable = sorted(set(agents) - covered)
    if unreachable:
        names = ", ".join(unreachable)
        raise TopologyV2ParseError(
            f"agents not covered by any scope_profile: {names}. Default search for these "
            f"agents would return zero results. "
            f"fix: add a scope_profile entry for each unreachable agent, OR set "
            f'`applies_to: ["*"]` on a baseline profile to fan out to every registered agent. '
            f"next: run kairix config validate. "
            f"run: grep -n scope_profiles kairix.config.yaml"
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

    Reads the six Wave D blocks from ``data["topology_v2"]`` (the
    namespaced parent key landed in #305). Empty data, missing
    ``topology_v2`` key, or an explicit ``topology_v2: null`` all parse
    to ``TopologyV2Config()``. Structural type errors raise
    :exc:`TopologyV2ParseError`; cross-reference checks are deferred to
    :func:`validate_topology_v2_references`.

    Per the F42 boundary discipline: returns a frozen dataclass tree;
    callers never touch ``dict[str, Any]`` again after parsing.
    """
    raw_section = data.get("topology_v2")
    if raw_section is None:
        return TopologyV2Config()
    section = _require_dict("topology_v2", raw_section)

    connectors_raw = _require_list("topology_v2.connectors", section.get("connectors"))
    credentials_raw = _require_list("topology_v2.credentials", section.get("credentials"))
    cc_pairs_raw = _require_list("topology_v2.cc_pairs", section.get("cc_pairs"))
    collections_raw = _require_list("topology_v2.collections", section.get("collections"))
    scope_profiles_raw = _require_list("topology_v2.scope_profiles", section.get("scope_profiles"))
    skills_raw = _require_list("topology_v2.skills", section.get("skills"))

    collections = tuple(
        _parse_one_collection(f"topology_v2.collections[{i}]", c) for i, c in enumerate(collections_raw)
    )

    # GH #373 — parse the top-level agents block once so wildcard
    # expansion + agent-reachability validation share the same agent
    # set. Outside the topology_v2 namespace because it pre-dates Wave D.
    agents = _parse_agents_block(data.get("agents"))

    # Two-phase scope_profile parse: first the raw shape (carries
    # applies_to as ()/('*',)/explicit list), then expand wildcards
    # against the registered agents tuple.
    raw_profiles = [
        _parse_one_scope_profile_raw(f"topology_v2.scope_profiles[{i}]", s) for i, s in enumerate(scope_profiles_raw)
    ]
    scope_profiles = _expand_wildcard_profiles(raw_profiles, agents)

    # F21 — every collection_name in any scope entry must be declared;
    # every registered agent must be covered by at least one profile.
    _validate_collection_references(scope_profiles, collections)
    _validate_agent_reachability(scope_profiles, agents)

    return TopologyV2Config(
        connectors=tuple(_parse_one_connector(f"topology_v2.connectors[{i}]", c) for i, c in enumerate(connectors_raw)),
        credentials=tuple(
            _parse_one_credential(f"topology_v2.credentials[{i}]", c) for i, c in enumerate(credentials_raw)
        ),
        cc_pairs=tuple(_parse_one_cc_pair(f"topology_v2.cc_pairs[{i}]", p) for i, p in enumerate(cc_pairs_raw)),
        collections=collections,
        scope_profiles=scope_profiles,
        skills=tuple(_parse_one_skill(f"topology_v2.skills[{i}]", s) for i, s in enumerate(skills_raw)),
    )
