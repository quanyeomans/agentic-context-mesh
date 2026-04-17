# Retrieval Boost Configuration

**Status:** Planned (Sprint 9)  
**Scope:** `kairix/search/config.py`, `rrf.py`, `hybrid.py`

---

## Problem

The three retrieval boosts (entity, procedural, temporal) encode assumptions about corpus structure that are not universally true. Different knowledge base types respond differently to each boost:

| Corpus type | Entity boost | Procedural boost | Date-path boost |
|---|---|---|---|
| Consulting / CRM knowledge base | ✅ high signal | ✅ if runbooks present | ❌ |
| Daily journal / meeting log corpus | ⚠️ weak | ❌ | ✅ high signal |
| Technical documentation | ❌ | ✅ extended patterns | ❌ |
| Code knowledge base | ❌ | ⚠️ | ❌ |
| Legal / compliance documents | ⚠️ | ⚠️ | ❌ |

**Root cause of the design gap:** All three boosts have hardcoded factors and path patterns. Enabling any boost for a corpus where its assumptions don't hold causes regression. There is currently no way to disable a boost without a code change.

**Observed regression (Sprint 8):** Temporal date-path boost caused temporal NDCG to drop from 0.668 → 0.597 on a corpus where temporal queries target concept notes, not date-named files. Boost was reverted by disabling via code comment — a fragile workaround.

---

## Design

### Core principle: boosts are opt-in, not opt-out

Each boost ships `enabled: false` in the zero-config baseline. Deployment configs enable what their corpus supports.

### `RetrievalConfig` dataclass

A frozen dataclass passed into `hybrid_search()`. Replaces all module-level constants in `rrf.py`.

```python
# kairix/search/config.py

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
        r"/runbooks?/",
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

    @classmethod
    def defaults(cls) -> RetrievalConfig:
        """Consulting knowledge base defaults (entity + procedural boost, no temporal)."""
        return cls()

    @classmethod
    def minimal(cls) -> RetrievalConfig:
        """All boosts disabled. Baseline RRF only. Use to isolate boost impact."""
        return cls(
            entity=EntityBoostConfig(enabled=False),
            procedural=ProceduralBoostConfig(enabled=False),
            temporal=TemporalBoostConfig(),
        )

    @classmethod
    def for_daily_log_corpus(cls) -> RetrievalConfig:
        """Date-named file corpus (journals, meeting logs). Enables date-path boost."""
        return cls(
            temporal=TemporalBoostConfig(date_path_boost_enabled=True),
        )

    @classmethod
    def for_technical_documentation(cls) -> RetrievalConfig:
        """Technical docs. Entity boost off; extended procedural patterns."""
        return cls(
            entity=EntityBoostConfig(enabled=False),
            procedural=ProceduralBoostConfig(
                factor=1.5,
                path_patterns=(
                    r"(?:^|/)how-to-", r"/runbooks?/", r"(?:^|/)runbook-",
                    r"(?:^|/)procedure", r"(?:^|/)sop-", r"(?:^|/)guide-",
                    r"(?:^|/)playbook-", r"(?:^|/)tutorial-", r"/docs?/", r"/reference/",
                ),
            ),
        )
```

### `hybrid_search()` signature change

```python
# kairix/search/hybrid.py

def hybrid_search(
    query: str,
    *,
    agent: str = "shape",
    config: RetrievalConfig | None = None,    # NEW — optional, defaults to RetrievalConfig.defaults()
    # ... existing params unchanged
) -> HybridResult:
    cfg = config or RetrievalConfig.defaults()
```

The `config` parameter is optional. Existing callers with no `config` argument continue to work with default behaviour unchanged.

### Boost function signatures

All three boost functions accept a typed config object instead of raw floats:

```python
# rrf.py — before
def entity_boost_neo4j(results, neo4j_client, boost_factor=0.20, cap=2.0)
def procedural_boost(results, boost_factor=1.4)
def temporal_date_boost(results, query, boost_factor=1.35)

# rrf.py — after
def entity_boost_neo4j(results, neo4j_client, config: EntityBoostConfig | None = None)
def procedural_boost(results, config: ProceduralBoostConfig | None = None)
def temporal_date_boost(results, query, config: TemporalBoostConfig | None = None)
```

Each function's first action is to check `config.enabled` and short-circuit (returning results unmodified) when disabled. This replaces the current code-comment disable pattern.

---

## YAML Configuration (Layer 2, Sprint 10)

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
        - "/runbooks?/"
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

Resolution order:
1. `KAIRIX_CONFIG_PATH` env var → explicit path
2. `./kairix.config.yaml` → working directory
3. Built-in defaults → no file required

Missing file or parse failure silently falls back to defaults.

---

## Per-Collection Profiles (Layer 3, v1.0)

Future: `FusedResult` gains `source_collection: str`. Different collections get different `BoostProfile` instances:

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

## Implementation Plan

### Layer 1 — Sprint 9 (~2h agentic)

| File | Action |
|---|---|
| `kairix/search/config.py` | CREATE — all three config dataclasses + `RetrievalConfig` factory methods |
| `kairix/search/rrf.py` | UPDATE — replace module constants with config params; remove `_PROCEDURAL_PATH_PATTERNS` module-level def |
| `kairix/search/hybrid.py` | UPDATE — add `config` param; re-enable `temporal_date_boost` call (no-ops by default via config) |
| `tests/search/test_retrieval_config.py` | CREATE — enabled/disabled paths for all three boosts; factory method tests |

### Layer 2 — Sprint 10 (~2h agentic)

| File | Action |
|---|---|
| `kairix/search/config_loader.py` | CREATE — YAML load with env var resolution + lru_cache |
| `kairix.example.config.yaml` | CREATE — documented example for common corpus types |
| `kairix/cli.py` | UPDATE — load config at CLI startup, pass to search |

### Layer 3 — v1.0 (~4h agentic)

| File | Action |
|---|---|
| `kairix/search/rrf.py` | UPDATE — `FusedResult.source_collection: str` field |
| `kairix/search/hybrid.py` | UPDATE — thread collection name through RRF to `FusedResult` |
| `kairix/search/config.py` | UPDATE — `CollectionProfile` mapping, profile lookup in boost functions |

---

## Benchmark Testing Protocol

After this refactor, every boost change follows this protocol:

1. **Baseline**: run benchmark with `RetrievalConfig.minimal()` — record "RRF only" score
2. **Incremental**: enable one boost at a time — record NDCG delta per category
3. **Gate**: if delta is negative in any category at its weight → do not enable by default
4. **Document**: record boost delta in `benchmark-results/RESULTS.md`

This replaces the current pattern of enabling by default and discovering regressions after deploy.

---

## Acceptance Criteria (Layer 1)

- `RetrievalConfig.minimal()` produces identical output to current "all boosts disabled" state
- `config` param is optional in `hybrid_search()` — no existing caller breaks
- `temporal_date_boost` re-enabled in call site (no code comment hack); disabled by default via config
- Procedural path patterns are configurable — `sop-`, `guide-`, `playbook-` included by default
- All 979 existing tests pass + new `test_retrieval_config.py`
