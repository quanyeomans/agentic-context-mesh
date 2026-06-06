"""
CLI entry point for `kairix search`.

Usage:
  kairix search "query" [--agent AGENT] [--scope SCOPE] [--collection COLLECTION]
                        [--budget N] [--limit N] [--json]

Options:
  --agent AGENT             Agent name for collection scoping (shape, builder, etc.)
  --scope SCOPE             Collection scope: shared | agent | shared+agent |
                            all-agents | everything
  --collection COLLECTION   Restrict retrieval to a single collection. Short-circuits
                            scope-based collection resolution.
  --budget N                Token budget cap (default: 3000)
  --limit N                 Max results to display (default: 10)
  --json                    Output raw JSON instead of formatted text
  --no-entity-card          Skip the entity-graph augmentation when the query is an
                            entity lookup

Adapter only — business logic lives in ``kairix.use_cases.search.run_search``.

``--collection`` is plumbed at this adapter layer rather than through
``run_search`` — the CLI binds a ``collections`` list onto the search
callable inside ``SearchDeps`` so the use-case public signature stays
single-collection-agnostic. Closes C3 Gap 3.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from kairix.core.search.scope import Scope
from kairix.use_cases.search import SearchDeps, SearchOutput, run_search, search_output_to_envelope


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kairix search",
        description="Hybrid BM25 + vector search over your document store.",
    )
    parser.add_argument("query", help="Search query string")
    parser.add_argument("--agent", default=None, help="Agent name for collection scoping")
    parser.add_argument(
        "--scope",
        default="shared+agent",
        choices=["shared", "agent", "shared+agent", "all-agents", "everything"],
        help="Collection scope (default: shared+agent)",
    )
    parser.add_argument(
        "--collection",
        default=None,
        help=(
            "Restrict retrieval to a single collection (e.g. reference-library). "
            "Short-circuits scope-based collection resolution. The CLI threads "
            "the flag through SearchDeps.search_fn; the use case signature is "
            "unchanged."
        ),
    )
    parser.add_argument("--budget", type=int, default=3000, help="Token budget (default: 3000)")
    parser.add_argument("--limit", type=int, default=10, help="Max results to display")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Output raw JSON")
    parser.add_argument(
        "--no-entity-card",
        dest="include_entity_card",
        action="store_false",
        default=True,
        help="Skip the entity-graph augmentation",
    )
    return parser


def _bind_collection_to_deps(deps: SearchDeps | None, collection: str | None) -> SearchDeps | None:
    """Return a ``SearchDeps`` whose ``search_fn`` injects ``collections=[collection]``.

    Mirrors ``kairix.agents.prep.cli._bind_collection_to_deps``. When
    ``collection`` is None the input deps pass through; otherwise the
    existing ``search_fn`` is wrapped so every call carries the
    collections list. The use case never sees the flag.
    """
    if collection is None:
        return deps
    base = deps if deps is not None else SearchDeps()
    inner = base.search_fn

    def _search_with_collection(**kwargs: Any) -> Any:
        kwargs.setdefault("collections", [collection])
        return inner(**kwargs)

    # SearchDeps is frozen — rebuild with the wrapped search_fn while
    # carrying every other field forward. Any new field on SearchDeps must
    # be mirrored here (loud failure preferred over silent default-fallback).
    return SearchDeps(
        search_fn=_search_with_collection,
        entity_card_fn=base.entity_card_fn,
        classify_fn=base.classify_fn,
        health_deps=base.health_deps,
    )


def format_text(out: SearchOutput) -> str:
    """Render a ``SearchOutput`` as the human-readable text the CLI prints."""
    lines: list[str] = [f"Query: {out.query}", f"Intent: {out.intent}"]
    if out.error:
        lines.append(f"Error: {out.error}")
        return "\n".join(lines)

    diagnostics = (
        f"Results: {len(out.results)} returned "
        f"(BM25={out.bm25_count}, vec={out.vec_count}"
        + (", vec_failed=True" if out.vec_failed else "")
        + f") | {out.total_tokens} tokens | {out.latency_ms:.0f}ms"
    )
    lines.append(diagnostics)
    lines.append("")

    for i, hit in enumerate(out.results, start=1):
        title = hit.title or hit.path.split("/")[-1]
        tier = hit.tier or "search"
        # Lead with snippet — the content is what the operator's eye should
        # land on first. Title + path follow, then score/collection. URLs
        # are typically long + uninformative without context.
        snippet = ""
        if hit.snippet:
            snippet = hit.snippet[:600].replace("\n", " ")
            if len(hit.snippet) > 600:
                snippet += "…"
        lines.append(f"{i}. [{tier}] {hit.collection} · score {hit.score:.4f}")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append(f"   {title}")
        lines.append(f"   {hit.path}")
        lines.append("")

    if not out.results:
        lines.append("No results found.")

    return "\n".join(lines)


def to_json_envelope(out: SearchOutput) -> dict[str, Any]:
    """Serialise the ``SearchOutput`` to the JSON envelope the ``--json`` flag emits.

    PR 2.2 / #421 aligned this with ``search_output_to_envelope`` —
    the dict shape ``tool_search`` returns over MCP — so the warm-MCP
    dispatch path can round-trip the envelope through
    ``SearchOutput.from_envelope`` and render byte-identical text.
    Operators previously saw a CLI-only subset (no ``health`` snapshot,
    no per-hit ``tokens`` or ``source_page``); the canonical shape now
    surfaces every field MCP callers already received.
    """
    return search_output_to_envelope(out)


def main(argv: list[str] | None = None, *, deps: SearchDeps | None = None) -> None:
    args = build_parser().parse_args(argv)
    effective_deps = _bind_collection_to_deps(deps, args.collection)

    out = run_search(
        args.query,
        agent=args.agent,
        scope=Scope.parse(args.scope),
        budget=args.budget,
        limit=args.limit,
        include_entity_card=args.include_entity_card,
        deps=effective_deps,
    )

    if args.as_json:
        print(json.dumps(to_json_envelope(out), indent=2))
    else:
        print(format_text(out))

    if out.error:
        sys.exit(1)


if __name__ == "__main__":
    main()
