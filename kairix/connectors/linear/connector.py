"""``LinearConnector`` — SourceConnector for the Linear GraphQL API.

Implements :class:`kairix.core.protocols.SourceConnector` plus the
:class:`PollConnector`, :class:`CredentialsConnector`, and
:class:`SlimConnector` capability mix-ins (spec §1) for one Linear
workspace (one workspace API key = one workspace, per spec §1).

Change detection rides incremental polling: each tick queries the five
entity types (issue / project / document / initiative / projectUpdate)
filtered + ordered by ``updatedAt > cursor`` and emits one
:class:`ChangeEvent` per node. The cursor is an opaque ``str`` token that
JSON-encodes a **per-entity-type** ``updatedAt`` watermark map; each type
advances its OWN watermark to the max ``updatedAt`` it emitted this tick
(forward progress), so no type is starved or skipped under per-tick-budget
pressure (spec §4).

Decision record — incremental poll, NOT webhooks (spec §13)
-----------------------------------------------------------
The MVP detects changes by **incremental polling** (``PollConnector``,
``updatedAt`` cursor), not webhooks (``EventConnector``). Three reasons:
(1) roadmap + docs change on a human cadence — an agent answering
"what's our roadmap / what does this doc say" is not sensitive to a
sub-15-minute edit lag; (2) webhooks require a public HTTPS callback
Linear can reach — exactly the inbound-exposure friction that conflicts
with hardened / closed deployments, whereas polling needs only outbound
HTTPS; (3) lower novelty risk — mirrors the proven Notion poll connector.
Freshness is the operator-tunable poll cadence; webhooks remain a clean
Phase-2 capability. See
``docs/architecture/connector-scope-topology/connector-design-specs/linear.md``
§13 for the full decision context.

Per F35, this module only imports from ``kairix.connectors.linear.*``
(same plugin) and ``kairix.core.*`` / ``kairix.secrets.*`` (the Protocol
+ secret-resolution surfaces). No reach into other connectors, no reach
into the extractor layer.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from kairix.connectors.linear.api_client import LinearApiClient
from kairix.connectors.linear.render import render
from kairix.core.protocols import (
    ChangeEvent,
    Container,
    Cursor,
    RawArtefact,
    Sensitivity,
    SourceMetadata,
)
from kairix.secrets.loader import SecretsLoader, SecretsResolver

logger = logging.getLogger(__name__)

CONNECTOR_NAME = "linear"

# Default sensitivity tier. Spec §1 / §7: roadmap + docs are
# company-internal; the connector ships with ``internal`` as the safer
# default. Operators routing client-confidential workspaces override via
# the connector config's ``default_sensitivity`` key.
DEFAULT_SENSITIVITY: Sensitivity = "internal"

# Mime hint for the rendered Markdown (spec §5). Bronze persists the raw
# markdown bytes; Silver routes through the passthrough / markitdown
# extractor chain.
LINEAR_MARKDOWN_MIME = "text/markdown"

# Topology v2 flag name — same convention as the other connector pilots.
# Module-level constant so the F52 call-site scan picks up exactly one
# verbatim reference per call site.
CONNECTOR_LINEAR_FLAG = "connector_linear"

# Default per-tick item ceiling (F66, spec §4). The tick stops early and
# resumes next tick rather than blowing the budget.
DEFAULT_PER_TICK_MAX_ITEMS = 500

# item_id type prefixes (spec §4). ``fetch`` / ``metadata_for`` /
# ``source_link`` dispatch on these without a second lookup.
_PREFIX_SEP = ":"

# Node field names referenced ≥3 times — extracted so the F17 dup-literal
# gate stays green.
_FIELD_IDENTIFIER = "identifier"

# GraphQL field constants — extracted so the F17 dup-literal gate stays
# green across the five per-entity queries.
_F_PAGE_INFO = "pageInfo { hasNextPage endCursor }"
_F_UPDATED_FILTER = "filter: { updatedAt: { gt: $since } }, orderBy: updatedAt"
_F_PAGE_ARGS = "first: 100, after: $after"
_F_HEADER = "query($after: String, $since: DateTimeOrDuration!)"

# Per-entity query specs: (item_id prefix, GraphQL connection name,
# node selection set). The selection sets pull the fields §5 renders +
# §6 needs for provenance. Each is paginated by the api client.
_ISSUE_NODES = (
    "id identifier title description url createdAt updatedAt "
    "state { name type } assignee { displayName email } creator { displayName email } "
    "team { key name } project { id name } labels { nodes { name } }"
)
_PROJECT_NODES = (
    "id name description url createdAt updatedAt state targetDate "
    "status { name } lead { displayName email } "
    "projectMilestones { nodes { name } }"
)
_DOCUMENT_NODES = "id title content url createdAt updatedAt creator { displayName email } project { id name }"
_INITIATIVE_NODES = (
    "id name description url createdAt updatedAt status creator { displayName email } projects { nodes { name } }"
)
_UPDATE_NODES = "id body health url createdAt updatedAt user { displayName email } project { id name }"


@dataclass(frozen=True)
class _EntitySpec:
    """One entity type's polling spec.

    Frozen per F42 — the spec table is data, not behaviour.
    ``prefix`` is the item_id type prefix; ``connection`` is the GraphQL
    connection field name; ``nodes`` is the node selection set.
    """

    prefix: str
    connection: str
    nodes: str


# Spec table — drives list_changes + retrieve_all_slim_docs. The
# ``projectUpdate`` prefix matches the spec §4 item_id shape exactly.
_ENTITY_SPECS: tuple[_EntitySpec, ...] = (
    _EntitySpec("issue", "issues", _ISSUE_NODES),
    _EntitySpec("project", "projects", _PROJECT_NODES),
    _EntitySpec("document", "documents", _DOCUMENT_NODES),
    _EntitySpec("initiative", "initiatives", _INITIATIVE_NODES),
    _EntitySpec("projectUpdate", "projectUpdates", _UPDATE_NODES),
)

# Linear's beginning-of-time sentinel for the initial (cursor=None) sync.
# A fixed early ISO timestamp so ``updatedAt > $since`` matches everything.
_EPOCH_ISO = "1970-01-01T00:00:00.000Z"

# Valid F39 sensitivity tiers — single source for the make_connector
# validation (mirrors the Sensitivity literal in protocols.py).
_VALID_SENSITIVITIES: frozenset[str] = frozenset({"public", "internal", "client-confidential", "personal"})


def _now_iso() -> str:
    """Return a current ISO-8601 UTC timestamp (``...Z`` suffix)."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_query(spec: _EntitySpec) -> str:
    """Build the paginated GraphQL query string for one entity spec."""
    return (
        f"{_F_HEADER} {{ "
        f"{spec.connection}({_F_PAGE_ARGS}, {_F_UPDATED_FILTER}) {{ "
        f"{_F_PAGE_INFO} nodes {{ {spec.nodes} }} }} }}"
    )


@dataclass(frozen=True)
class LinearCredentials:
    """Resolved API-key credential for one Linear workspace sync.

    Frozen per F42 — the typed shape that crosses the boundary between
    secret resolution and the connector constructor. Tests construct a
    literal and pass it via the ``credentials`` kwarg; production resolves
    via the injected :class:`SecretsResolver`.
    """

    api_key: str


def _resolve_credentials_from_secrets(secrets: SecretsResolver) -> LinearCredentials:
    """Resolve the Linear workspace API key via :class:`SecretsResolver`.

    Canonical leaf ``("connector", "linear", None, "api_key")`` resolves
    through canonical env → KV mount. When no source resolves,
    :meth:`SecretsResolver.require` raises with an actionable message —
    module import never crashes, only first-use of list_changes / fetch.

    F15-clean: the resolved key is captured into the frozen dataclass and
    never logged through any code path in this module.
    """
    api_key = secrets.require(scope="connector", area="linear", instance=None, leaf="api_key")
    return LinearCredentials(api_key=api_key)


class LinearConnector:
    """SourceConnector for one Linear workspace (incremental poll).

    Construction is cheap (no I/O, no Linear API call). The first
    :meth:`list_changes` call drains the five entity-type queries and
    caches each node so :meth:`fetch` / :meth:`metadata_for` /
    :meth:`source_link` resolve without a second round-trip.

    Decision record (spec §13): change detection is incremental polling,
    NOT webhooks — see the module docstring for the full rationale. The
    poll cadence is the freshness bound; webhooks are a Phase-2 capability.

    DI seams:

      * ``credentials`` — resolved :class:`LinearCredentials`. Tests pass
        a literal; production callers omit and the connector resolves via
        the injected :class:`SecretsResolver`.
      * ``client_builder`` — builds the :class:`LinearApiClient`. Tests
        pass a builder returning a client backed by an
        ``httpx.MockTransport`` (or a fake client) so no real Linear call
        leaks.
      * ``default_sensitivity`` — connector-wide F39 tier; defaults to
        ``internal``. Operators set the matching key in
        ``connector_specific_config`` to override.
      * ``secrets`` — :class:`SecretsResolver`. Tests pass
        :class:`tests.fakes.FakeSecretsLoader`; production defaults to
        :class:`SecretsLoader`.

    Flag gating happens at the worker-dispatch boundary
    (:func:`kairix.worker.dispatch_linear_sync`) — when the
    ``connector_linear`` flag is OFF the connector slot is a no-op and
    this constructor is never called.
    """

    name: str = CONNECTOR_NAME
    per_tick_max_items: int = DEFAULT_PER_TICK_MAX_ITEMS
    # F66-watermark-exempt: Linear nodes stream as small markdown
    # envelopes; no large disk writes per item.
    disk_watermark_min_free_bytes: int | None = None

    def __init__(
        self,
        *,
        credentials: LinearCredentials | None = None,
        client_builder: Callable[[LinearCredentials], LinearApiClient] | None = None,
        default_sensitivity: Sensitivity = DEFAULT_SENSITIVITY,
        per_tick_max_items: int = DEFAULT_PER_TICK_MAX_ITEMS,
        secrets: SecretsResolver | None = None,
    ) -> None:
        self._default_sensitivity: Sensitivity = default_sensitivity
        self.per_tick_max_items = per_tick_max_items
        # Canonical-naming seam. Tests inject FakeSecretsLoader; production
        # constructs a real SecretsLoader lazily so the connector imports
        # cleanly without a provisioned secrets backend.
        self._secrets: SecretsResolver = secrets if secrets is not None else SecretsLoader()

        resolved = credentials if credentials is not None else _resolve_credentials_from_secrets(self._secrets)
        self._credentials = resolved

        if client_builder is not None:
            self._api = client_builder(resolved)
        else:
            self._api = LinearApiClient(resolved.api_key)

        # Per-item node cache populated by :meth:`list_changes` so
        # ``fetch`` / ``metadata_for`` / ``source_link`` resolve without a
        # second API call. Keyed by the full type-prefixed item_id.
        self._node_cache: dict[str, Mapping[str, Any]] = {}
        # Per-entity-type ``updatedAt`` watermark map, JSON-encoded into the
        # opaque cursor token by :meth:`next_cursor` (spec §4). ``None``
        # before the first :meth:`list_changes` call.
        self._next_cursor: str | None = None

    # ------------------------------------------------------------------
    # SourceConnector Protocol surface (base)
    # ------------------------------------------------------------------

    def list_changes(self, cursor: Cursor | None) -> Iterator[ChangeEvent]:
        """Stream changes across all five entity types since ``cursor``.

        ``cursor`` is an opaque token JSON-encoding a per-entity-type
        ``updatedAt`` watermark map; ``None`` (or a malformed/legacy
        single-string token) triggers a full enumeration (initial sync) for
        every type. Each tick queries the five types filtered by
        ``updatedAt > <that type's watermark>``, yielding one ``modified``
        :class:`ChangeEvent` per node with a type-prefixed ``item_id``.

        ONE global :attr:`per_tick_max_items` budget (F66) is shared across
        the types this tick — a budget-hit on an earlier type just means
        later types get fewer/none THIS tick. Each type advances its OWN
        watermark to the max ``updatedAt`` it EMITTED (forward progress even
        on a budget-limited partial drain), so no type is ever skipped past
        its unprocessed items and every type makes progress across ticks.
        Idempotent upsert makes re-fetching a boundary item harmless.
        """
        watermarks = _decode_cursor(cursor)
        events: list[ChangeEvent] = []
        for spec in _ENTITY_SPECS:
            self._drain_spec(spec, watermarks, events)
        # Each type's watermark moved forward to its own last-emitted
        # updatedAt (spec §4). A type that drained nothing this tick keeps
        # its prior watermark — never moves backwards, never skips.
        self._next_cursor = _encode_cursor(watermarks)
        return iter(events)

    def _drain_spec(self, spec: _EntitySpec, watermarks: dict[str, str], events: list[ChangeEvent]) -> None:
        """Drain one entity-type query into ``events``, advancing its watermark.

        Reads + advances ONLY this type's watermark in ``watermarks``.
        Appends ChangeEvents (and caches nodes) until the page is exhausted
        OR the shared :attr:`per_tick_max_items` budget is reached. The
        type's watermark advances to the max ``updatedAt`` of the items it
        EMITTED this tick — forward progress even on a budget-limited
        partial drain (spec §4), so the next tick never re-skips an
        un-emitted item nor re-fetches an already-emitted prefix endlessly.

        Per-item isolation (spec §9): a node that raises on conversion is
        logged at WARNING and skipped — never failing the whole tick.
        """
        since = watermarks.get(spec.prefix, _EPOCH_ISO)
        for node in self._api.paginate(
            _build_query(spec),
            {"since": since},
            connection=spec.connection,
        ):
            if len(events) >= self.per_tick_max_items:
                return
            try:
                event = self._node_to_event(spec, node)
            # spec §9 per-item isolation: one malformed node fails just that
            # item (logged), never the whole tick — the broad catch IS the
            # intended isolation boundary, hence the rationale-tagged catch.
            except Exception:  # NOSONAR S112 — F3: spec §9 per-item isolation boundary.
                logger.warning(
                    "linear: skipping a malformed %s node — conversion raised; the tick continues.",
                    spec.prefix,
                    exc_info=True,
                )
                continue
            if event is None:
                continue
            self._node_cache[event.item_id] = node
            events.append(event)
            # Forward progress: advance THIS type's watermark to the latest
            # updatedAt it has emitted, never moving it backwards. ``max``
            # over the prior watermark keeps the mark monotonic even if the
            # source returns a type's nodes out of updatedAt order.
            prior = watermarks.get(spec.prefix)
            watermarks[spec.prefix] = event.modified_at if prior is None else max(prior, event.modified_at)

    def fetch(self, item_id: str) -> RawArtefact:
        """Render one cached Linear entity as Markdown.

        Dispatches by the ``item_id`` type prefix to the matching
        per-entity renderer (spec §5). The node must have been cached by a
        previous :meth:`list_changes` call.
        """
        kind, node = self._cached(item_id)
        markdown = render(kind, node)
        return RawArtefact(
            raw=markdown.encode("utf-8"),
            mime=LINEAR_MARKDOWN_MIME,
            fetched_at=_now_iso(),
            sensitivity_hint=self._default_sensitivity,
        )

    def source_link(self, item_id: str) -> str:
        """Return the linear.app URL for the cached node.

        Falls back to a ``linear://`` shape when the node didn't carry a
        URL (rare partial fetch).
        """
        node = self._node_cache.get(item_id)
        if node is not None:
            url = node.get("url")
            if isinstance(url, str) and url:
                return url
        return f"linear://{item_id}"

    def sensitivity_for(self, _item_id: str) -> Sensitivity:
        """Return the connector-configured default sensitivity tier.

        Spec §6: per-team / per-project overrides are Phase 2; v1 returns
        the configured default for every item.
        """
        return self._default_sensitivity

    def next_cursor(self) -> str | None:
        """Return the JSON-encoded per-entity-type watermark cursor.

        Spec §4: ``None`` before the first :meth:`list_changes` call.
        After a tick it is the opaque token encoding each type's own
        ``updatedAt`` watermark — every type advanced to its last-emitted
        ``updatedAt`` (forward progress), so a budget-limited tick still
        persists progress for the types it drained without skipping any
        type's un-emitted items.
        """
        return self._next_cursor

    def metadata_for(self, item_id: str) -> SourceMetadata:
        """Return cached Linear envelope metadata for ``item_id`` (F65).

        Spec §6: ``author`` / ``author_email`` from the item's creator
        (issue/doc/update author; project lead); ``tags`` from Linear
        labels; ``properties`` carry state / team / identifier / project /
        url / health (whichever apply). Cache miss collapses to an empty
        :class:`SourceMetadata`.
        """
        node = self._node_cache.get(item_id)
        if node is None:
            return SourceMetadata()
        author, author_email = self._author_of(node)
        return SourceMetadata(
            modified_at=_opt_str(node.get("updatedAt")),
            created_at=_opt_str(node.get("createdAt")),
            author=author,
            author_email=author_email,
            tags=self._labels_of(node),
            properties=self._properties_of(item_id, node),
        )

    # ------------------------------------------------------------------
    # PollConnector capability
    # ------------------------------------------------------------------

    def list_changes_for_container(self, container: Container) -> Iterator[ChangeEvent]:
        """Yield ChangeEvents since the container's cursor token.

        Spec §1: the MVP treats the workspace as a single container, so
        the container-scoped poll delegates to :meth:`list_changes` with
        the container's cursor token as the high-water-mark.
        """
        return self.list_changes(container.cursor_token)

    # ------------------------------------------------------------------
    # SlimConnector capability
    # ------------------------------------------------------------------

    def retrieve_all_slim_docs(self, _container: Container) -> Iterator[str]:
        """Enumerate every current item_id for the prune cycle (spec §4).

        Yields the type-prefixed item_id for every node across all five
        entity types from the EPOCH cursor (full enumeration). The
        framework diffs the returned set against ``documents.item_id`` to
        detect archives / deletes (id present last tick, absent now).
        """
        for spec in _ENTITY_SPECS:
            for node in self._api.paginate(
                _build_query(spec),
                {"since": _EPOCH_ISO},
                connection=spec.connection,
            ):
                item_id = self._item_id(spec, node)
                if item_id is not None:
                    yield item_id

    # ------------------------------------------------------------------
    # CredentialsConnector capability
    # ------------------------------------------------------------------

    def load_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
        """Validate + normalise the raw credential mapping (spec §1).

        Accepts a mapping carrying an ``api_key`` (or legacy ``token``)
        entry; returns the normalised ``{"api_key": <key>}`` mapping, or
        ``None`` when no usable key is present (signalling the credential
        is invalid for the Linear source kind).
        """
        raw = credentials.get("api_key") or credentials.get("token")
        if not isinstance(raw, str) or not raw.strip():
            return None
        return {"api_key": raw.strip()}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _node_to_event(self, spec: _EntitySpec, node: Mapping[str, Any]) -> ChangeEvent | None:
        """Translate one entity node into a typed ``modified`` ChangeEvent."""
        item_id = self._item_id(spec, node)
        if item_id is None:
            return None
        modified_at = _opt_str(node.get("updatedAt")) or _now_iso()
        parent_id = self._parent_id(node)
        return ChangeEvent(
            op="modified",
            item_id=item_id,
            modified_at=modified_at,
            parent_id=parent_id,
            metadata={
                "sensitivity": self._default_sensitivity,
                "kind": spec.prefix,
                "mime": LINEAR_MARKDOWN_MIME,
            },
        )

    def _item_id(self, spec: _EntitySpec, node: Mapping[str, Any]) -> str | None:
        """Compute the type-prefixed item_id for a node.

        Issues key on their human ``identifier`` (e.g. ``ENG-42``); every
        other entity keys on its UUID ``id`` (spec §4). Returns ``None``
        when the node carries no usable key.
        """
        if spec.prefix == "issue":
            key = _opt_str(node.get(_FIELD_IDENTIFIER)) or _opt_str(node.get("id"))
        else:
            key = _opt_str(node.get("id"))
        if not key:
            return None
        return f"{spec.prefix}{_PREFIX_SEP}{key}"

    def _cached(self, item_id: str) -> tuple[str, Mapping[str, Any]]:
        """Return ``(kind, node)`` for a cached item_id, or raise KeyError."""
        node = self._node_cache.get(item_id)
        if node is None:
            raise KeyError(
                f"linear: item_id {item_id!r} not in the per-tick node cache. "
                "fix: call list_changes() before fetch()/metadata_for() so the "
                "poll drain populates the node cache before the orchestrator asks for the body. "
                "next: see kairix/core/connectors/pipeline.py for the list_changes -> fetch contract."
            )
        kind = item_id.split(_PREFIX_SEP, 1)[0]
        return kind, node

    @staticmethod
    def _author_of(node: Mapping[str, Any]) -> tuple[str | None, str | None]:
        """Resolve (author, author_email) from the node's creator/lead/user."""
        for key in ("creator", "lead", "user", "assignee"):
            person = node.get(key)
            if isinstance(person, Mapping):
                name = _opt_str(person.get("displayName"))
                email = _opt_str(person.get("email"))
                if name or email:
                    return name, email
        return None, None

    @staticmethod
    def _labels_of(node: Mapping[str, Any]) -> tuple[str, ...]:
        """Pull Linear label names into a tuple (spec §6 tags)."""
        labels = node.get("labels")
        if not isinstance(labels, Mapping):
            return ()
        nodes = labels.get("nodes")
        if not isinstance(nodes, list):
            return ()
        out: list[str] = []
        for entry in nodes:
            if isinstance(entry, Mapping):
                name = _opt_str(entry.get("name"))
                if name:
                    out.append(name)
        return tuple(out)

    def _properties_of(self, item_id: str, node: Mapping[str, Any]) -> dict[str, str]:
        """Build the spec §6 properties block (only the keys that apply)."""
        props: dict[str, str] = {"kind": item_id.split(_PREFIX_SEP, 1)[0]}
        _put(props, _FIELD_IDENTIFIER, node.get(_FIELD_IDENTIFIER))
        _put(props, "state", _nested_name(node.get("state")))
        _put(props, "team", _nested_name(node.get("team")))
        _put(props, "project", _nested_name(node.get("project")))
        _put(props, "url", node.get("url"))
        _put(props, "health", node.get("health"))
        return props

    @staticmethod
    def _parent_id(node: Mapping[str, Any]) -> str | None:
        """Return the parent project id (issues / updates), prefixed, or None."""
        project = node.get("project")
        if isinstance(project, Mapping):
            pid = _opt_str(project.get("id"))
            if pid:
                return f"project{_PREFIX_SEP}{pid}"
        return None


def _opt_str(value: Any) -> str | None:
    """Return ``value`` as a non-empty stripped string, else ``None``."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _nested_name(value: Any) -> str | None:
    """Pull ``.name`` from a nested object, or None."""
    if isinstance(value, Mapping):
        return _opt_str(value.get("name"))
    return None


def _put(props: dict[str, str], key: str, value: Any) -> None:
    """Insert ``key`` into ``props`` when ``value`` resolves to a string."""
    resolved = _opt_str(value)
    if resolved is not None:
        props[key] = resolved


def _decode_cursor(cursor: Cursor | None) -> dict[str, str]:
    """Decode the opaque cursor token into a per-entity-type watermark map.

    Spec §4: the cursor JSON-encodes ``{prefix: <iso-updatedAt>, ...}``.
    Degrades safely so existing state never skips data:

      * ``None`` → ``{}`` (no watermark for any type → full enumeration).
      * a malformed / non-JSON / legacy single-ISO-string token → ``{}``
        (treated as "no watermark for any type" → full enumeration, so a
        pre-upgrade single-watermark cursor re-syncs rather than skipping).
      * a JSON object → only its ``str: str`` entries are kept; any
        non-string value is dropped (that type falls back to full enum).
    """
    if cursor is None:
        return {}
    try:
        decoded = json.loads(cursor)
    except (ValueError, TypeError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    return {key: value for key, value in decoded.items() if isinstance(key, str) and isinstance(value, str) and value}


def _encode_cursor(watermarks: Mapping[str, str]) -> str:
    """JSON-encode the per-entity-type watermark map into the opaque token.

    Keys are sorted so the token is deterministic (stable round-trip +
    stable persisted state across ticks with the same watermarks).
    """
    return json.dumps(dict(watermarks), sort_keys=True)


def make_connector(config: Mapping[str, Any]) -> LinearConnector:
    """Construct a :class:`LinearConnector` from a config mapping.

    Decision record (spec §13): the connector detects changes by
    incremental polling (``PollConnector``, ``updatedAt`` cursor), NOT
    webhooks — see :class:`LinearConnector` / spec §13 for the rationale.

    Expected keys:

      * ``default_sensitivity`` (optional) — one of the F39 sensitivity
        literals; defaults to ``"internal"``.
      * ``per_tick_max_items`` (optional) — F66 per-tick budget; defaults
        to :data:`DEFAULT_PER_TICK_MAX_ITEMS`.

    Credentials resolve via the connector's injected
    :class:`SecretsResolver` — the canonical leaf is
    ``("connector", "linear", None, "api_key")``.

    Registered via ``[project.entry-points."kairix.connectors"]`` in
    kairix's ``pyproject.toml`` so the orchestration layer can resolve
    ``linear`` to this factory by name.
    """
    declared = config.get("default_sensitivity", DEFAULT_SENSITIVITY)
    if declared not in _VALID_SENSITIVITIES:
        raise ValueError(
            f"linear: default_sensitivity {declared!r} is not a valid F39 tier. "
            "fix: set default_sensitivity to one of "
            "public / internal / client-confidential / personal. "
            "next: see kairix/core/protocols.py Sensitivity for the literal set."
        )
    sensitivity = cast(Sensitivity, declared)
    raw_budget = config.get("per_tick_max_items", DEFAULT_PER_TICK_MAX_ITEMS)
    if not isinstance(raw_budget, int) or raw_budget < 1:
        raise ValueError(
            f"linear: per_tick_max_items {raw_budget!r} must be a positive integer. "
            "fix: set per_tick_max_items to an integer >= 1 (default 500). "
            "next: see kairix/connectors/linear/connector.py DEFAULT_PER_TICK_MAX_ITEMS."
        )
    return LinearConnector(default_sensitivity=sensitivity, per_tick_max_items=raw_budget)
