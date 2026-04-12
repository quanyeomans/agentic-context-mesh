"""
kairix — contextual intelligence layer for QMD + Obsidian agent stacks.

Subcommands:
  embed       Embed vault documents into QMD sqlite-vec (text-embedding-3-large)
  search      Hybrid search: BM25 + vector via RRF (Phase 1)
  entity      Entity graph: lookup, write, extract (Phase 1)
  curator     Curator agent: entity health monitoring and enrichment (CA-1)
  contradict  Contradiction detection: check new content against vault knowledge
  timeline    Temporal query rewriting + date-aware retrieval (Phase 2)
  summarise   L0/L1 tiered context generation (Phase 2)
  classify    Auto-classify memory writes (Phase 3)
  brief       Session briefing synthesis (Phase 3)
  benchmark   Run retrieval quality benchmark (Phase 5)
  wikilinks   Inject [[wikilinks]] on first mention in agent-written vault files (ADR-M07)

See PRD.md for architecture, phase targets, and ADRs.
"""

import sys


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "embed":
        from kairix.embed.cli import main as embed_main

        embed_main()

    elif cmd == "entity":
        from kairix.entities.cli import main as entity_main

        entity_main(sys.argv[2:])

    elif cmd == "curator":
        from kairix.curator.cli import main as curator_main

        curator_main(sys.argv[2:])

    elif cmd == "search":
        from kairix.search.cli import main as search_main

        search_main(sys.argv[2:])

    elif cmd == "benchmark":
        from kairix.benchmark.cli import main as benchmark_main

        benchmark_main(sys.argv[2:])

    elif cmd == "summarise":
        from kairix.summaries.cli import main as summarise_main

        summarise_main(sys.argv[2:])

    elif cmd == "timeline":
        from kairix.temporal.cli import main as timeline_main

        timeline_main(sys.argv[2:])

    elif cmd == "wikilinks":
        from kairix.wikilinks.cli import main as wikilinks_main

        wikilinks_main(sys.argv[2:])

    elif cmd == "classify":
        from kairix.classify.cli import main as classify_main

        classify_main(sys.argv[2:])

    elif cmd == "brief":
        from kairix.briefing.cli import main as brief_main

        brief_main(sys.argv[2:])

    elif cmd == "contradict":
        from kairix.contradict.cli import main as contradict_main

        contradict_main(sys.argv[2:])

    else:
        print(f"Unknown command: {cmd}\n{__doc__}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
