"""
kairix.knowledge.summaries — L0/L1 tiered context generation (Phase 2).

Modules:
  generate   gpt-4o-mini L0 abstract + L1 structured overview
  staleness  summaries.db tracking, stale detection by mtime
  loader     tier router (l0/l1/full based on token budget)
  cli        kairix summarise subcommand
"""
