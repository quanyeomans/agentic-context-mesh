"""
CLI entry point for `kairix search`.

Usage:
  kairix search "query" [--agent AGENT] [--scope SCOPE] [--collection COLLECTION]
                        [--budget N] [--limit N] [--snippet-width N] [--json]

Options:
  --agent AGENT             Agent name for collection scoping (shape, builder, etc.)
  --scope SCOPE             Collection scope: shared | agent | shared+agent |
                            all-agents | everything
  --collection COLLECTION   Restrict retrieval to a single collection. Short-circuits
                            scope-based collection resolution.
  --budget N                Token budget cap (default: 3000)
  --limit N                 Max results to display (default: 10)
  --snippet-width N         Max snippet length per result (default: 600). Useful for
                            triage (--snippet-width 200) vs deep-dive (--snippet-width 1200).
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
import re
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
    parser.add_argument(
        "--snippet-width",
        dest="snippet_width",
        type=int,
        default=600,
        help=(
            "Max snippet length per result (default: 600 chars). Use "
            "``--snippet-width 200`` for tighter triage output or "
            "``--snippet-width 1200`` for deep-dive readability."
        ),
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="Output raw JSON")
    parser.add_argument(
        "--no-entity-card",
        dest="include_entity_card",
        action="store_false",
        default=True,
        help="Skip the entity-graph augmentation",
    )
    parser.add_argument(
        "--max-tier",
        dest="max_tier",
        default="L2",
        choices=["L0", "L1", "L2"],
        help=(
            "Richest context tier per result (default: L2 full snippet). "
            "Use L0 for abstracts or L1 for overviews to triage many hits "
            "cheaply; takes effect when tier summaries are generated."
        ),
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
        if kwargs.get("collections") is None:
            kwargs["collections"] = [collection]
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


# #385 follow-up — strip the internal ``#<int>`` chunk-sequence suffix that
# ``_SqliteChunkWriter`` appends to ``documents.path`` so the operator's
# fallback title (``hit.path.split("/")[-1]``) reads ``alpha.md`` instead of
# ``alpha.md#0``. Archive-extracted chunks read ``something.zip#1536``;
# stripping the suffix turns useless chunk numbering into a recognisable
# source name. The underlying ``hit.path`` line below still shows the full
# path so debug context is preserved.
_CHUNK_SEQ_SUFFIX_RE = re.compile(r"#\d+$")


def clean_title_fallback(path: str) -> str:
    """Last-segment-of-path fallback title with chunk-sequence suffix stripped.

    Mirrors ``hit.path.split("/")[-1]`` plus a regex strip of any trailing
    ``#<int>``. Returns an empty string when ``path`` is empty so the
    caller can fall back further (e.g. to the path itself).
    """
    if not path:
        return ""
    last_segment = path.rsplit("/", 1)[-1]
    return _CHUNK_SEQ_SUFFIX_RE.sub("", last_segment)


def _render_hit_block(i: int, hit: Any, *, snippet_width: int) -> list[str]:
    """Render one hit's text block — extracted from ``format_text`` to
    keep its cognitive complexity under the F16 ceiling.

    Returns the list of output lines (header, optional snippet, title,
    path, blank-separator). ``snippet_width`` is the pre-clamped
    per-call cap from the caller.
    """
    title = hit.title or clean_title_fallback(hit.path)
    # ADR-036 §Q7 — surface Wikidata-sourced entity summaries with a
    # ``[Wikidata]`` badge so the operator can tell them apart from
    # vault chunks at a glance. Gated on the well-known ``entity://``
    # source-URI prefix the projector writes.
    if hit.path.startswith("entity://"):
        title = f"{title} [Wikidata]"
    tier = hit.tier or "search"
    snippet = ""
    if hit.snippet and snippet_width > 0:
        snippet = hit.snippet[:snippet_width].replace("\n", " ")
        if len(hit.snippet) > snippet_width:
            snippet += "…"
    # PLA-274 — surface the per-page citation in text mode. Pre-fix the page
    # number was carried in the JSON envelope but invisible in the rendered
    # text, so an operator reading the CLI couldn't see WHICH page a PDF /
    # PPTX / XLSX hit came from. Appended to the score header when present.
    page_suffix = f" · p.{hit.source_page}" if getattr(hit, "source_page", None) is not None else ""
    lines: list[str] = [f"{i}. [{tier}] {hit.collection} · score {hit.score:.4f}{page_suffix}"]
    if snippet:
        lines.append(f"   {snippet}")
    lines.append(f"   {title}")
    lines.append(f"   {hit.path}")
    # PLA-274 — render the canonical breadcrumb when it differs from the
    # display path (connector / archive content whose source_uri is the
    # resolvable pointer, not the synthetic ``<uri>#<seq>`` chunk key).
    ref = hit.source_ref()
    if ref.source_uri and ref.source_uri != hit.path:
        lines.append(f"   source: {ref.source_uri}")
    lines.append("")
    return lines


def format_text(out: SearchOutput, *, snippet_width: int = 600) -> str:
    """Render a ``SearchOutput`` as the human-readable text the CLI prints.

    ``snippet_width`` caps each result's snippet length (default 600
    chars). Operators tune for triage (``snippet_width=200``) or
    deep-dive (``snippet_width=1200``). Width applies to the rendered
    text only — the underlying ``hit.snippet`` is unchanged in case a
    caller wants to format differently downstream.
    """
    snippet_width = max(int(snippet_width), 0)
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
        lines.extend(_render_hit_block(i, hit, snippet_width=snippet_width))

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
        max_tier=args.max_tier,
        deps=effective_deps,
    )

    if args.as_json:
        print(json.dumps(to_json_envelope(out), indent=2))
    else:
        print(format_text(out, snippet_width=args.snippet_width))

    if out.error:
        sys.exit(1)


if __name__ == "__main__":
    main()
