"""``SkillsConnector`` — SourceConnector for locally-installed Claude Code skills.

Implements :class:`kairix.core.protocols.SourceConnector` plus the
:class:`PollConnector` + :class:`SlimConnector` capability surface
(design §3.4) for the host's ``~/.claude`` tree. It is credential-less:
the source is the local filesystem, so there is no auth, no
:class:`~kairix.secrets.SecretsResolver`, and no network. This is the
external half of the capability-recommender corpus (Feeder 2) — it
indexes the agent's installed skills, slash-commands, and sub-agents into
the ``capabilities`` collection so the recommender can rank them.

The connector walks (via :mod:`kairix.connectors.skills.fs`):

  * ``~/.claude/plugins/cache/**/{skills,commands,agents}``
  * ``~/.claude/skills/*.md`` (flat files)

parses each artefact's YAML frontmatter, **dedups by name preferring the
higher version** (the cache holds multiple plugin versions), renders each
to Markdown, and emits one :class:`ChangeEvent` per artefact with a
kind-prefixed ``item_id`` (``skill:brainstorming``, ``command:feature-dev``,
``agent:code-architect``).

Graceful degrade (design §3.4 / §7): where ``~/.claude`` is absent — the
production VM — the connector finds nothing and the corpus stays
kairix-caps-only. A missing tree is a warn-and-continue, NEVER an error.
The connector is gated by the ``connector_skills`` feature flag at the
worker-dispatch boundary (:func:`kairix.worker.dispatch_skills_sync`);
when OFF the connector slot is a no-op and this constructor is never
called.

Default sensitivity tier is ``internal`` — local dev-tooling metadata,
not secret (design §3.4). Per F35 this module imports only stdlib +
``kairix.connectors.skills.*`` (same plugin) + ``kairix.core.*`` (the
Protocol surface); no reach into other connectors or the extractor layer.

See ``tests/bdd/features/connector_skills.feature`` for the behaviour
spec this plugin pins.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

from kairix.connectors.skills.fs import SkillArtefact, iter_skill_artefacts
from kairix.core.protocols import (
    ChangeEvent,
    Container,
    Cursor,
    RawArtefact,
    Sensitivity,
    SourceMetadata,
)

logger = logging.getLogger(__name__)

CONNECTOR_NAME = "skills"

# Module-level constant so the F52 flag call-site scan picks up exactly
# one verbatim reference per call site (mirrors the linear convention).
CONNECTOR_SKILLS_FLAG = "connector_skills"

# Default sensitivity tier. Design §3.4: locally-installed dev tooling
# metadata is company-internal, not secret. Operators can override via the
# connector config's ``default_sensitivity`` key.
DEFAULT_SENSITIVITY: Sensitivity = "internal"

# Mime hint for the rendered Markdown. Bronze persists the markdown bytes;
# Silver routes through the passthrough extractor chain.
SKILLS_MARKDOWN_MIME = "text/markdown"

# Default per-tick item ceiling (F66). The local FS walk is cheap, but a
# bound keeps the contract uniform with the network connectors.
DEFAULT_PER_TICK_MAX_ITEMS = 500

# Valid F39 sensitivity tiers — single source for make_connector
# validation (mirrors the Sensitivity literal in protocols.py).
_VALID_SENSITIVITIES: frozenset[str] = frozenset({"public", "internal", "client-confidential", "personal"})

# item_id prefix separator (``<kind>:<name>``). ``fetch`` / ``metadata_for`` /
# ``source_link`` dispatch on this without a second lookup.
_PREFIX_SEP = ":"

# Capability-tag constants — referenced ≥3 times across metadata / tags
# so the F17 dup-literal gate stays green.
_CAPABILITY_TAG = "capability"
_KIND_TAG_PREFIX = "kind:"
_CAPABILITY_URI_SCHEME = "capability://"
# Metadata key carrying the artefact kind on each ChangeEvent.
_KIND_META_KEY = "kind"


def _iso_z(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _now_iso() -> str:
    return _iso_z(datetime.now(timezone.utc))


def _mtime_iso(path: Path) -> str:
    """ISO-8601 UTC mtime for ``path``; falls back to now on stat failure."""
    try:
        stat = path.stat()
    except OSError:
        return _now_iso()
    return _iso_z(datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc))


def _render_markdown(artefact: SkillArtefact) -> str:
    """Render one artefact to the Markdown payload Bronze persists.

    Heading is the capability name; the description is the retrieval
    document's lead line (matches the per-source ``when_to_use`` semantics
    Feeder 1 uses); the body follows verbatim.
    """
    parts = [f"# {artefact.name}", ""]
    if artefact.description:
        parts.extend([artefact.description, ""])
    if artefact.body:
        parts.append(artefact.body)
    return "\n".join(parts).strip() + "\n"


def _item_id(artefact: SkillArtefact) -> str:
    return f"{artefact.kind}{_PREFIX_SEP}{artefact.name}"


class SkillsConnector:
    """SourceConnector for locally-installed Claude Code skills (FS walk).

    Construction is cheap (no I/O, no walk). The first
    :meth:`list_changes` call walks the tree and caches each artefact so
    :meth:`fetch` / :meth:`metadata_for` / :meth:`source_link` resolve
    without re-walking. Methods called before any drain re-walk on demand
    so the connector works whichever order the orchestrator drives it.

    DI seams:

      * ``claude_root`` — the ``~/.claude`` tree to walk. Tests pass a
        ``tmp_path``-rooted fake tree; production defaults to
        ``Path.home() / ".claude"``.
      * ``default_sensitivity`` — connector-wide F39 tier; defaults to
        ``internal``.
      * ``per_tick_max_items`` — F66 per-tick budget.

    Flag gating happens at the worker-dispatch boundary
    (:func:`kairix.worker.dispatch_skills_sync`) — when ``connector_skills``
    is OFF the connector slot is a no-op and this constructor is never
    called.
    """

    name: str = CONNECTOR_NAME
    per_tick_max_items: int = DEFAULT_PER_TICK_MAX_ITEMS
    # F66-watermark-exempt: reads local FS only; no remote-fetch disk pressure
    disk_watermark_min_free_bytes: int | None = None

    def __init__(
        self,
        claude_root: Path | None = None,
        *,
        default_sensitivity: Sensitivity = DEFAULT_SENSITIVITY,
        per_tick_max_items: int = DEFAULT_PER_TICK_MAX_ITEMS,
    ) -> None:
        self._claude_root = (claude_root if claude_root is not None else Path.home() / ".claude").expanduser()
        self._default_sensitivity: Sensitivity = default_sensitivity
        self.per_tick_max_items = per_tick_max_items
        self._cache: dict[str, SkillArtefact] = {}
        self._last_max_modified_at: str | None = None

    # ------------------------------------------------------------------
    # SourceConnector Protocol surface
    # ------------------------------------------------------------------

    def list_changes(self, cursor: Cursor | None) -> Iterator[ChangeEvent]:
        """Stream one ChangeEvent per deduped artefact since ``cursor``.

        ``cursor`` is an ISO-8601 timestamp; artefacts whose file mtime is
        ``<= cursor`` are filtered out. A missing ``~/.claude`` yields zero
        events (graceful degrade) — never raises. The walk repopulates the
        cache so :meth:`fetch` / :meth:`metadata_for` resolve afterwards.
        """
        artefacts = self._walk_and_cache()
        op: Literal["created", "modified"] = "created" if cursor is None else "modified"
        events: list[ChangeEvent] = []
        for artefact in artefacts:
            modified_at = _mtime_iso(artefact.source_path)
            if cursor is not None and modified_at <= cursor:
                continue
            events.append(
                ChangeEvent(
                    op=op,
                    item_id=_item_id(artefact),
                    modified_at=modified_at,
                    metadata={"sensitivity": self._default_sensitivity, _KIND_META_KEY: artefact.kind},
                )
            )
        self._last_max_modified_at = max((ev.modified_at for ev in events), default=cursor)
        return iter(events)

    def next_cursor(self) -> str | None:
        """Return the ISO-8601 high-water-mark from the most recent drain."""
        return self._last_max_modified_at

    def fetch(self, item_id: str) -> RawArtefact:
        """Render the cached artefact for ``item_id`` to Markdown bytes."""
        artefact = self._resolve(item_id)
        markdown = _render_markdown(artefact)
        return RawArtefact(
            raw=markdown.encode("utf-8"),
            mime=SKILLS_MARKDOWN_MIME,
            fetched_at=_now_iso(),
            sensitivity_hint=self._default_sensitivity,
        )

    def source_link(self, item_id: str) -> str:
        """``capability://<kind>/<name>`` — the stable corpus id for the item."""
        kind, name = self._split_item_id(item_id)
        return f"{_CAPABILITY_URI_SCHEME}{kind}/{name}"

    def sensitivity_for(self, _item_id: str) -> Sensitivity:
        """Return the connector's configured sensitivity tier (``internal``)."""
        return self._default_sensitivity

    def metadata_for(self, item_id: str) -> SourceMetadata:
        """Return file-stat envelope metadata + capability/kind tags.

        ADR-021: surfaces the file mtime + the ``capability`` / ``kind:<k>``
        tags so the recommender can filter by kind. An unknown id collapses
        to an empty :class:`SourceMetadata` so the pipeline keeps running.
        """
        try:
            artefact = self._resolve(item_id)
        except KeyError:
            return SourceMetadata()
        return SourceMetadata(
            modified_at=_mtime_iso(artefact.source_path),
            tags=(_CAPABILITY_TAG, f"{_KIND_TAG_PREFIX}{artefact.kind}"),
        )

    # ------------------------------------------------------------------
    # PollConnector + SlimConnector capability surface
    # ------------------------------------------------------------------

    def list_changes_for_container(self, container: Container) -> Iterator[ChangeEvent]:
        """PollConnector surface — one cursor per container.

        The skills connector exposes a single logical container (the whole
        ``~/.claude`` tree), so this scopes change detection to
        ``container.cursor_token`` and delegates to :meth:`list_changes`.
        """
        return self.list_changes(container.cursor_token)

    def retrieve_all_slim_docs(self, _container: Container) -> Iterator[str]:
        """SlimConnector surface — id-only enumeration for the prune cycle."""
        return iter(_item_id(a) for a in self._walk_and_cache())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _walk_and_cache(self) -> list[SkillArtefact]:
        """Walk the tree, refresh the id→artefact cache, return the artefacts.

        Graceful degrade: a missing ``~/.claude`` logs an info line and
        returns an empty list, never raising.
        """
        if not self._claude_root.is_dir():
            logger.info(
                "skills connector: %s absent — corpus stays kairix-caps-only (graceful degrade)",
                self._claude_root,
            )
            self._cache = {}
            return []
        artefacts = list(iter_skill_artefacts(claude_root=self._claude_root))
        self._cache = {_item_id(a): a for a in artefacts}
        return artefacts

    def _resolve(self, item_id: str) -> SkillArtefact:
        """Return the cached artefact for ``item_id``, re-walking once if cold."""
        if item_id not in self._cache:
            self._walk_and_cache()
        if item_id not in self._cache:
            raise KeyError(
                f"skills connector: no artefact in cache for item_id {item_id!r}. "
                "fix: drive list_changes() before fetch()/metadata_for() so the walk "
                "populates the cache. "
                "next: confirm the artefact still exists under ~/.claude."
            )
        return self._cache[item_id]

    def _split_item_id(self, item_id: str) -> tuple[str, str]:
        """Split ``<kind>:<name>`` into its parts."""
        kind, sep, name = item_id.partition(_PREFIX_SEP)
        if not sep:
            raise ValueError(
                f"skills connector: item_id {item_id!r} is not kind-prefixed. "
                "fix: pass the item_id as emitted by list_changes (kind:name). "
                "next: see kairix/connectors/skills/connector.py for the item_id contract."
            )
        return kind, name


def make_connector(config: Mapping[str, Any]) -> SkillsConnector:
    """Construct a :class:`SkillsConnector` from a config mapping.

    Expected keys (all optional — the connector is credential-less and
    degrades gracefully where ``~/.claude`` is absent):

      * ``claude_root`` — path string / :class:`Path` to the ``~/.claude``
        tree; defaults to ``Path.home() / ".claude"``.
      * ``default_sensitivity`` — one of the F39 sensitivity literals;
        defaults to ``"internal"``.
      * ``per_tick_max_items`` — F66 per-tick budget; defaults to
        :data:`DEFAULT_PER_TICK_MAX_ITEMS`.

    Registered via ``[project.entry-points."kairix.connectors"]`` in
    kairix's ``pyproject.toml`` so the orchestration layer can resolve
    ``skills`` to this factory by name.
    """
    declared = config.get("default_sensitivity", DEFAULT_SENSITIVITY)
    if declared not in _VALID_SENSITIVITIES:
        raise ValueError(
            f"skills: default_sensitivity {declared!r} is not a valid F39 tier. "
            "fix: set default_sensitivity to one of "
            "public / internal / client-confidential / personal. "
            "next: see kairix/core/protocols.py Sensitivity for the literal set."
        )
    sensitivity = cast(Sensitivity, declared)

    raw_budget = config.get("per_tick_max_items", DEFAULT_PER_TICK_MAX_ITEMS)
    if not isinstance(raw_budget, int) or raw_budget < 1:
        raise ValueError(
            f"skills: per_tick_max_items {raw_budget!r} must be a positive integer. "
            "fix: set per_tick_max_items to an integer >= 1 (default 500). "
            "next: see kairix/connectors/skills/connector.py DEFAULT_PER_TICK_MAX_ITEMS."
        )

    raw_root = config.get("claude_root")
    claude_root = Path(raw_root).expanduser() if raw_root is not None else None
    return SkillsConnector(
        claude_root=claude_root,
        default_sensitivity=sensitivity,
        per_tick_max_items=raw_budget,
    )
