"""
kairix prep — tiered L0/L1 context summary.

Usage:
  kairix prep <query> [--tier l0|l1] [--agent AGENT] [--scope SCOPE]
                      [--collection COLLECTION] [--json]

Adapter only — business logic lives in
``kairix.use_cases.prep.run_prep``.

``--collection`` is plumbed at this adapter layer rather than through
``run_prep`` — the CLI binds a ``collections`` list onto the search
callable inside ``PrepDeps`` so the use-case public signature stays
single-collection-agnostic.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Literal

from kairix.core.search.scope import Scope
from kairix.use_cases.prep import PrepDeps, PrepOutput, prep_output_to_envelope, run_prep


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kairix prep",
        description="Tiered L0/L1 context summary for a topic, grounded in retrieved documents.",
    )
    parser.add_argument("query", help="Topic to summarise")
    parser.add_argument(
        "--tier",
        choices=["l0", "l1"],
        default="l0",
        help="l0 = 2-3 sentences (default), l1 = structured overview",
    )
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
            "Short-circuits scope-based collection resolution. Closes the C3 "
            "spike's Gap 3 — the underlying SearchPipeline already accepted "
            "collections, the CLI just didn't expose it."
        ),
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="Output JSON envelope")
    return parser


def _bind_collection_to_deps(deps: PrepDeps | None, collection: str | None) -> PrepDeps | None:
    """Return a ``PrepDeps`` whose ``search_fn`` injects ``collections=[collection]``.

    When ``collection`` is None, the input ``deps`` passes through
    unchanged — keeping the no-flag path identical to before this commit.
    When set, wraps the existing ``search_fn`` (production default or
    test-injected) so every call carries the collections list. The use
    case never sees the flag; the CLI adapter owns it.
    """
    if collection is None:
        return deps
    base = deps if deps is not None else PrepDeps()
    inner = base.search_fn

    def _search_with_collection(**kwargs: Any) -> Any:
        # Caller may already pass collections; respect that and only inject
        # when absent. Keeps the adapter idempotent even when callers wire
        # collections themselves.
        kwargs.setdefault("collections", [collection])
        return inner(**kwargs)

    # PrepDeps is frozen — rebuild with the wrapped search_fn while
    # carrying every other field forward. Any new field on PrepDeps must
    # be mirrored here (loud failure preferred over silent default-fallback).
    return PrepDeps(search_fn=_search_with_collection, chat_fn=base.chat_fn)


def format_text(out: PrepOutput) -> str:
    """Render a ``PrepOutput`` as the human-readable text the CLI prints."""
    if out.error:
        return f"error: {out.error}"
    lines: list[str] = [
        f"Query: {out.query}",
        f"Tier:  {out.tier}",
        "",
        out.summary,
    ]
    if out.sources:
        lines.append("")
        lines.append("Sources:")
        for src in out.sources:
            # PLA-274 / #437 — sources are resolvable SourceRefs; show the
            # canonical breadcrumb (with title + page when present) so the
            # operator can re-open the grounding source, not just read a title.
            label = src.title or src.source_uri or src.path
            page_suffix = f" · p.{src.source_page}" if src.source_page is not None else ""
            lines.append(f"  - {label}{page_suffix}")
            if src.source_uri and src.source_uri != label:
                lines.append(f"    ↳ {src.source_uri}")
    return "\n".join(lines)


def main(argv: list[str] | None = None, *, deps: PrepDeps | None = None) -> int:
    """Entry point for ``kairix prep``."""
    args = build_parser().parse_args(argv)
    effective_deps = _bind_collection_to_deps(deps, args.collection)
    out = run_prep(
        args.query,
        agent=args.agent,
        scope=Scope.parse(args.scope),
        tier=_as_tier(args.tier),
        deps=effective_deps,
    )

    if args.as_json:
        print(json.dumps(prep_output_to_envelope(out), indent=2))
    else:
        print(format_text(out))

    return 1 if out.error else 0


def _as_tier(value: str) -> Literal["l0", "l1"]:
    """Narrow argparse's ``str`` to the ``Literal`` the use case expects."""
    if value == "l1":
        return "l1"
    return "l0"


if __name__ == "__main__":
    sys.exit(main())
