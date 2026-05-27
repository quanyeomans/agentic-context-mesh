"""Registry resolvers for connector + extractor plugins.

Plugin discovery is via :func:`importlib.metadata.entry_points` (PEP
621 entry-point groups). First-party plugins register in kairix's own
``pyproject.toml``; third parties ship a separate pip distribution
declaring the same entry-point group (see spec doc §8). The
resolver here is the seam consumers depend on — ``factory.build_*``
calls it once at startup to materialise the configured plugin.

Entry-point group names:

  * ``kairix.connectors`` — :class:`~kairix.core.protocols.SourceConnector`
    factories. Each entry-point exposes a ``make_connector`` callable.
  * ``kairix.extractors`` — :class:`~kairix.core.protocols.Extractor`
    factories. Each entry-point exposes a ``make_extractor`` callable.

The :class:`ConnectorRegistry` / :class:`ExtractorRegistry` classes
keep the historical resolve-by-name shape used by the worker; the
new :func:`iter_connectors` / :func:`iter_extractors` /
:func:`resolve_connector` / :func:`resolve_extractor` module-level
functions are the IM-2 surface ``factory.build_connector_pipeline``
consumes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from importlib import metadata
from typing import Any, cast

from kairix.core.protocols import Extractor, SourceConnector

CONNECTOR_GROUP = "kairix.connectors"
EXTRACTOR_GROUP = "kairix.extractors"


def iter_connectors() -> Iterator[tuple[str, Callable[..., SourceConnector]]]:
    """Yield ``(name, factory)`` for every registered connector entry-point.

    The factory is the loaded ``make_connector`` callable; calling it
    with a config mapping returns a :class:`SourceConnector` instance.
    """
    for ep in metadata.entry_points(group=CONNECTOR_GROUP):
        yield ep.name, ep.load()


def iter_extractors() -> Iterator[tuple[str, Callable[..., Extractor]]]:
    """Yield ``(name, factory)`` for every registered extractor entry-point.

    The factory is the loaded ``make_extractor`` callable; calling it
    with a config mapping returns an :class:`Extractor` instance.
    """
    for ep in metadata.entry_points(group=EXTRACTOR_GROUP):
        yield ep.name, ep.load()


def resolve_connector(name: str) -> Callable[..., SourceConnector]:
    """Return the ``make_connector`` factory registered under ``name``.

    Raises :class:`KeyError` if no plugin is registered under that name.
    """
    for ep_name, factory in iter_connectors():
        if ep_name == name:
            return factory
    raise KeyError(
        f"connector {name!r} not registered. "
        f'fix: declare a [project.entry-points."{CONNECTOR_GROUP}"] row in the '
        f"installing distribution's pyproject.toml. "
        f"next: pip install the distribution that ships the connector, or add "
        f"a row to kairix's own pyproject.toml for a first-party connector. "
        f"run: python -c 'from importlib.metadata import entry_points; "
        f'print([ep.name for ep in entry_points(group="{CONNECTOR_GROUP}")])\''
    )


def resolve_extractor(name: str) -> Callable[..., Extractor]:
    """Return the ``make_extractor`` factory registered under ``name``.

    Raises :class:`KeyError` if no plugin is registered under that name.
    """
    for ep_name, factory in iter_extractors():
        if ep_name == name:
            return factory
    raise KeyError(
        f"extractor {name!r} not registered. "
        f'fix: declare a [project.entry-points."{EXTRACTOR_GROUP}"] row in the '
        f"installing distribution's pyproject.toml. "
        f"next: pip install the distribution that ships the extractor, or add "
        f"a row to kairix's own pyproject.toml for a first-party extractor. "
        f"run: python -c 'from importlib.metadata import entry_points; "
        f'print([ep.name for ep in entry_points(group="{EXTRACTOR_GROUP}")])\''
    )


def build_extractor_from_entry(entry: dict[str, Any]) -> Extractor:
    """Construct the right extractor shape from a connector config entry.

    Precedence (highest to lowest):

    1. ``extractor_chain: [name1, name2, ...]`` — opt-in escalation chain.
       Resolves each name and wraps in :class:`EscalatingExtractor` so
       :meth:`Extractor.quality_ok` drives fall-through.

    2. ``extractor: <name>`` — single-extractor (backward compatible).
       Existing operator configs work unchanged.

    3. Neither field set — defaults to ``passthrough``.

    Per-extractor kwargs go through ``extractor_config`` (single) or
    ``extractor_chain_configs: {name: {...}}`` (per-chain-member).

    Used by both ``_run_one_connector_batch`` (regular sync) and
    ``_build_reextract_components`` (Bug D recovery) so both paths
    benefit from escalation when configured.
    """
    from kairix.core.connectors.escalation import EscalatingExtractor

    chain_names = entry.get("extractor_chain")
    if chain_names:
        if not isinstance(chain_names, list) or not all(isinstance(n, str) for n in chain_names):
            raise ValueError(
                f"extractor_chain must be a list of strings, got {chain_names!r}. "
                f"fix: set 'extractor_chain: [markitdown, pdf_fallback, ocr]' in "
                f"kairix.config.yaml. "
                f"next: see docs/architecture/connector-ingestion-architecture.md § 4 "
                f"for the escalation chain pattern."
            )
        per_member_configs = entry.get("extractor_chain_configs") or {}
        members: list[Extractor] = []
        for name in chain_names:
            factory = resolve_extractor(name)
            member_kwargs = per_member_configs.get(name, {})
            members.append(factory(**member_kwargs) if member_kwargs else factory())
        # Cast widens the EscalatingExtractor concrete type to the Extractor
        # Protocol so Sonar's S5886 narrowing check sees the contract the
        # function signature declares — EscalatingExtractor satisfies the
        # Protocol structurally but Sonar tracks the concrete class. Both
        # branches return something callers consume identically.
        return cast(Extractor, EscalatingExtractor(members))

    name = entry.get("extractor", "passthrough")
    factory = resolve_extractor(name)
    ex_config = entry.get("extractor_config")
    return factory(**ex_config) if ex_config else factory()


def build_bronze_from_entry(entry: dict[str, Any], *, db: Any) -> Any:
    """Construct the StreamingBronzeStore for a connector config entry.

    Phase 7 of the streaming-bronze rollout: streaming bronze is the
    only bronze persistence model. The function exists for symmetry with
    ``build_extractor_from_entry`` (one resolver per layer per entry)
    and for any future per-connector bronze-store variants.

    Operators with legacy ``bronze_mode`` config fields get a
    fix-pointer error at config-load time — the field is no longer
    accepted because there's nothing to choose between.
    """
    from kairix.core.connectors.streaming_bronze import StreamingBronzeStore

    if "bronze_mode" in entry:
        raise ValueError(
            "'bronze_mode' config field is no longer accepted — streaming "
            "bronze is the only persistence model. "
            "fix: remove the 'bronze_mode' line from your kairix.config.yaml. "
            "next: see docs/architecture/streaming-bronze-plan.md § 7 for the "
            "default-flip + legacy-removal rationale."
        )
    return StreamingBronzeStore(db)


class ConnectorRegistry:
    """Resolves a :class:`~kairix.core.protocols.SourceConnector` by name.

    Thin wrapper over :func:`resolve_connector` — kept for the
    pre-existing resolve-by-name shape. The factory is invoked with
    the connector-specific config mapping; ``make_connector`` is the
    convention every plugin follows (positional ``config: Mapping``
    argument).
    """

    def resolve(self, name: str, *, config: dict[str, Any] | None = None) -> SourceConnector:
        """Load the registered factory and call it with ``config``.

        Raises :class:`KeyError` if no plugin is registered under
        ``name`` (see :func:`resolve_connector`).
        """
        factory = resolve_connector(name)
        return factory(config or {})


class ExtractorRegistry:
    """Resolves an :class:`~kairix.core.protocols.Extractor` by mime + magic bytes.

    Enumerates every registered extractor; returns the first one whose
    :meth:`Extractor.can_extract` returns ``True``. Raises
    :class:`KeyError` if no extractor claims the format.
    """

    def __init__(self, *, configs: dict[str, dict[str, Any]] | None = None) -> None:
        self._configs = dict(configs) if configs is not None else {}

    def resolve(self, mime: str, magic_bytes: bytes) -> Extractor:
        """Return the first registered extractor that claims ``(mime, magic_bytes)``.

        Raises :class:`KeyError` if no extractor accepts the format.
        Per-extractor config (if any) comes from ``configs`` passed at
        construction; absent that, the factory is called with no args.
        """
        for name, factory in iter_extractors():
            kwargs = self._configs.get(name, {})
            extractor = factory(**kwargs) if kwargs else factory()
            if extractor.can_extract(mime, magic_bytes):
                return extractor
        raise KeyError(
            f"no extractor accepts mime={mime!r}, magic_bytes={magic_bytes[:8]!r}. "
            f"fix: register an extractor whose can_extract() claims this format. "
            f"next: see docs/architecture/connector-ingestion-architecture.md §3 "
            f"for the Extractor Protocol; pdf_fallback / ocr are the typical "
            f"escalation chain. "
            f"run: python -c 'from importlib.metadata import entry_points; "
            f'print([ep.name for ep in entry_points(group="{EXTRACTOR_GROUP}")])\''
        )
