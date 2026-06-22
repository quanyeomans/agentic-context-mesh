# Retrieval Boost Configuration

**Status:** Implemented — config-driven boosts shipped; YAML configuration (Layer 2) shipped
**Scope:** `kairix/core/search/config.py`, `kairix/core/search/config_loader.py`, `kairix/core/search/config_validator.py`, `kairix/core/search/boosts.py`, `kairix/core/search/rrf.py`

> The original design split this work into three layers. **Layer 1 (the
> `RetrievalConfig` dataclass) and Layer 2 (YAML configuration) have both
> shipped.** Every boost is now opt-in via config — there are no hardcoded,
> code-comment-disabled boosts left. This doc is now a **tuning reference**:
> the design narrative below records *why* the config surface is shaped the way
> it is; the live field set is owned by `kairix/core/search/config.py`, so read
> that module for the authoritative defaults. Suite paths reference the
> packaged suites under `kairix/data/suites/` (the top-level `suites/` tree no
> longer exists).

---

## Problem

The retrieval boosts (entity, procedural, temporal) encode assumptions about corpus structure that are not universally true. Different knowledge base types respond differently to each boost:

| Corpus type | Entity boost | Procedural boost | Date-path boost |
|---|---|---|---|
| Consulting / CRM knowledge base | ✅ high signal | ✅ if runbooks present | ❌ |
| Daily journal / meeting log corpus | ⚠️ weak | ❌ | ✅ high signal |
| Technical documentation | ❌ | ✅ extended patterns | ❌ |
| Code knowledge base | ❌ | ⚠️ | ❌ |
| Legal / compliance documents | ⚠️ | ⚠️ | ❌ |

**Root cause of the original design gap:** the boosts shipped with hardcoded factors and path patterns. Enabling a boost for a corpus where its assumptions don't hold caused regression, and there was no way to disable a boost without a code change.

**Resolution:** every boost now reads its `enabled` flag (and its factors / patterns) from a typed config object, so an operator disables or retunes any boost via `kairix.config.yaml` without touching code. The fragile code-comment disable pattern is gone.

---

## Design

### Core principle: boosts are opt-in, not opt-out

Each boost ships `enabled: false` in the zero-config baseline (with the
exception of the sweep-optimised `RetrievalConfig.defaults()` factory, which
turns on the chunk-date temporal boost — see below). Deployment configs enable
what their corpus supports.

### `RetrievalConfig` dataclass (shipped)

A frozen dataclass passed into the hybrid search entry point. It replaced all
module-level boost constants. The live definition is in
`kairix/core/search/config.py`; the abbreviated shape below shows the three
original boosts — the module has since grown several more config objects
(source-tier, content-quality, entity-first-routing, rerank, fusion strategy,
cross-layer dedup) documented in that file.

```python
# kairix/core/search/config.py

@dataclass(frozen=True)
class EntityBoostConfig:
    enabled: bool = True
    factor: float = 0.20      # log-scale weight on Neo4j MENTIONS in-degree
    cap: float = 2.0          # max boosted_score / rrf_score ratio

@dataclass(frozen=True)
class ProceduralBoostConfig:
    enabled: bool = True
    factor: float = 1.4
    path_patterns: tuple[str, ...] = (
        r"(?:^|/)how-to-",
        r"(?:^|/)runbooks?/",
        r"(?:^|/)runbook-",
        r"(?:^|/)procedure",
        r"(?:^|/)sop-",
        r"(?:^|/)guide-",
        r"(?:^|/)playbook-",
    )

@dataclass(frozen=True)
class TemporalBoostConfig:
    # Date-path boost: boosts docs whose path contains a date matching the query.
    # Enable only for corpora where YYYY-MM-DD.md files are the query target.
    date_path_boost_enabled: bool = False
    date_path_boost_factor: float = 1.35
    date_path_recency_window_days: int = 90

    # Chunk-date boost: boosts by chunk_date metadata column (TMP-7B).
    # Enable when chunk_date is populated at index time.
    chunk_date_boost_enabled: bool = False
    chunk_date_decay_halflife_days: int = 30

@dataclass(frozen=True)
class RetrievalConfig:
    entity: EntityBoostConfig = field(default_factory=EntityBoostConfig)
    procedural: ProceduralBoostConfig = field(default_factory=ProceduralBoostConfig)
    temporal: TemporalBoostConfig = field(default_factory=TemporalBoostConfig)
    # ... plus source_tier_boost, content_quality_boost,
    #     entity_first_routing, rerank, fusion_strategy, and more —
    #     see kairix/core/search/config.py for the authoritative set.

    @classmethod
    def defaults(cls) -> RetrievalConfig:
        """Sweep-optimised defaults: RRF fusion, entity/procedural off,
        chunk-date temporal boost on, vec_limit=10."""
        ...

    @classmethod
    def minimal(cls) -> RetrievalConfig:
        """All boosts disabled. Baseline RRF only. Use to isolate boost impact."""
        ...

    @classmethod
    def for_daily_log_corpus(cls) -> RetrievalConfig:
        """Date-named file corpus (journals, meeting logs). Enables date-path boost."""
        ...
```

The shipped module exposes additional factory methods —
`for_technical_documentation()` (entity off + extended procedural patterns) and
`for_semantic_corpus()` (RRF fusion for vector-dominant corpora). It also ships
a frozen `REFLIB_RETRIEVAL_CONFIG` baseline for the reference-library
collection. Read `config.py` for the current factory set and defaults.

### Search entry-point config parameter (shipped)

The hybrid search entry point takes an optional `config: RetrievalConfig | None`
parameter; when omitted it falls back to `RetrievalConfig.defaults()`. Existing
callers with no `config` argument keep working with default behaviour. (Fusion
itself now lives in `kairix/core/search/fusion.py` and the orchestrator in
`kairix/core/search/pipeline.py` — the old `kairix/search/hybrid.py` module was
removed when the tree moved under `kairix/core/search/`.)

### Boost functions are config-driven (shipped)

The boost functions in `kairix/core/search/rrf.py` accept typed config objects
instead of raw floats, and each short-circuits (returning results unmodified)
when its `enabled` flag is false — replacing the former code-comment disable
pattern. The boosts are additionally wrapped as `BoostStrategy` protocol
implementations in `kairix/core/search/boosts.py`, so the pipeline composes a
boost chain rather than calling functions inline.

```python
# kairix/core/search/rrf.py — config-driven signatures
def entity_boost_neo4j(results, neo4j_client, config: EntityBoostConfig | None = None)
def procedural_boost(results, config: ProceduralBoostConfig | None = None)
def temporal_date_boost(results, query, config: TemporalBoostConfig | None = None)
```

---

## YAML Configuration (shipped)

Boosts are configurable from `kairix.config.yaml` via
`kairix/core/search/config_loader.py`, with values range-checked by
`kairix/core/search/config_validator.py`:

```yaml
# kairix.config.yaml — example for a consulting knowledge base

retrieval:
  boosts:
    entity:
      enabled: true
      factor: 0.20
      cap: 2.0

    procedural:
      enabled: true
      factor: 1.4
      path_patterns:
        - "(?:^|/)how-to-"
        - "(?:^|/)runbooks?/"
        - "(?:^|/)runbook-"
        - "(?:^|/)procedure"
        - "(?:^|/)sop-"
        - "(?:^|/)guide-"
        - "(?:^|/)playbook-"

    temporal:
      date_path_boost:
        enabled: false      # Enable for date-named file corpora
        factor: 1.35
        recency_window_days: 90
      chunk_date_boost:
        enabled: false      # Enable when chunk_date metadata is populated
        decay_halflife_days: 30
```

Resolution order (see the `config_loader.py` module docstring for the
authoritative behaviour):

1. `KAIRIX_CONFIG_PATH` env var → explicit path
2. `./kairix.config.yaml` → working directory
3. Built-in defaults → no file required

The loader also participates in the layered config overlay
(`kairix/config_layers.py`) so a read-only base image plus a writable overlay
merge correctly. A missing file or YAML parse failure falls back to defaults; an
out-of-range value raises `ConfigValidationError` at startup rather than
silently degrading. The resolved config is cached per process.

---

## Per-Collection Profiles (forward-looking)

Per-collection boost profiles — different collections receiving different boost
settings — remain on the roadmap and are partially anticipated by the shipped
`source_tier_boost` config (Issue #432), which reweights results by an
operator-declared per-collection source tier. The fully general
`collection_profiles` / `boost_profiles` YAML shape sketched below is not yet
implemented:

```yaml
retrieval:
  collection_profiles:
    runbooks-collection:    {boost_profile: runbook_heavy}
    entity-graph-collection: {boost_profile: entity_heavy}

  boost_profiles:
    runbook_heavy:
      procedural: {enabled: true, factor: 1.6}
      entity: {enabled: false}
    entity_heavy:
      entity: {enabled: true, factor: 0.30}
      procedural: {enabled: false}
```

---

## Benchmark Testing Protocol

Every boost change follows this protocol:

1. **Baseline**: run the benchmark with `RetrievalConfig.minimal()` — record the "RRF only" score.
2. **Incremental**: enable one boost at a time — record the NDCG delta per category.
3. **Gate**: if the delta is negative in any category at its weight → do not enable by default.
4. **Document**: record the boost delta in the benchmark results.

Use the packaged gold suites under `kairix/data/suites/` (e.g.
`kairix/data/suites/reflib-gold-v3.yaml`) and drive the sweep with
`kairix eval hybrid-sweep`. This replaces the old pattern of enabling by default
and discovering regressions after deploy.

### Reference points

These two measurements are **distinct** — do not conflate them:

- **Production baseline (242-case `reflib` suite, v2026.6.9 measurement,
  standing baseline as of v2026.6.18):** weighted-total **0.808** ·
  NDCG@10 **0.884** · Hit@5 **0.913** · MRR@10 **0.831**. Per-category NDCG@10:
  recall 0.916 · temporal 0.558 (weakest) · entity 0.800 · conceptual 0.917 ·
  multi_hop 0.724 · procedural 0.977.
- **Clean reference-library sweep upper bound
  (`kairix/data/suites/reflib-gold-v3.yaml`, 2026-05-08):** hybrid-RRF
  NDCG@10 **0.949** · Hit@5 **0.965**. This is a separate clean-corpus
  upper-bound measurement, not the production baseline.

Temporal is the weakest production category (NDCG@10 0.558) — boost tuning here
has the most headroom. The temporal boosts (`date_path_boost`,
`chunk_date_boost`) are the levers to evaluate against your own corpus.

---

## Implementation status

| Layer | Status | Where it lives |
|---|---|---|
| Config dataclasses (`RetrievalConfig` + per-boost configs + factory methods) | ✅ Shipped | `kairix/core/search/config.py` |
| Config-driven boost functions + `BoostStrategy` chain | ✅ Shipped | `kairix/core/search/rrf.py`, `kairix/core/search/boosts.py` |
| YAML configuration + validation + layered overlay | ✅ Shipped | `kairix/core/search/config_loader.py`, `kairix/core/search/config_validator.py` |
| General per-collection boost profiles | ⏳ Roadmap (partially anticipated by `source_tier_boost`) | — |

---

## Acceptance Criteria (met)

- `RetrievalConfig.minimal()` produces the "all boosts disabled" RRF baseline.
- The `config` parameter is optional in the hybrid search entry point — no existing caller breaks.
- `temporal_date_boost` is config-gated (disabled by default via config), not code-comment-disabled.
- Procedural path patterns are configurable — `sop-`, `guide-`, `playbook-` are included by default.
- The existing test suite passes alongside the retrieval-config tests.
