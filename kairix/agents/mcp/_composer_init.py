"""Canonical wiring module that registers every PR 2.1-2.7 composer.

Imported once at dispatcher first-use (via
:mod:`kairix.agents.mcp.client_dispatcher`) so the warm-MCP text-mode
routing has every composer it needs without each CLI module needing to
self-register on import (which would force the CLI tree to load even
for ``kairix --json`` invocations).

Import direction:

* This module imports from ``kairix.agents.*`` CLI modules + ``kairix.use_cases.*``
  use-case modules.
* It imports ``register_composer`` from :mod:`kairix.agents.mcp.text_mode_composers`.
* No one imports FROM this module's symbols — it's pure side-effect.

The composer's ``format_text`` signature is normalised to
``(result, argv) -> str`` even when the underlying formatter only
needs the result. argv-derived parameters (``--top-k`` /
``--threshold`` for contradict, ``--limit`` for timeline,
``--print-output`` for brief) are extracted via :func:`_parse_kv_flags`
+ :func:`_int_or` / :func:`_float_or` reused from
:mod:`kairix.agents.mcp.client_dispatcher`.

F46-clean: this is a composition module, not pipeline construction.
"""

from __future__ import annotations

from typing import Any

from kairix.agents.briefing.cli import format_output as _brief_format_output
from kairix.agents.mcp.client_dispatcher import (
    _float_or,
    _int_or,
    _parse_kv_flags,
)
from kairix.agents.mcp.text_mode_composers import (
    TextModeComposer,
    register_composer,
)
from kairix.agents.prep.cli import format_text as _prep_format_text
from kairix.agents.research.cli import format_text as _research_format_text
from kairix.core.search.cli import format_text as _search_format_text
from kairix.core.temporal.cli import format_header as _timeline_format_header
from kairix.core.temporal.cli import format_results as _timeline_format_results
from kairix.knowledge.contradict.cli import format_text as _contradict_format_text
from kairix.quality.probe.caches_cli import (
    CacheRow,
)
from kairix.quality.probe.caches_cli import (
    format_text as _caches_format_text,
)
from kairix.use_cases.bootstrap import (
    BootstrapOutput,
)
from kairix.use_cases.bootstrap import (
    bootstrap_output_to_markdown as _bootstrap_format_text,
)
from kairix.use_cases.brief import BriefOutput
from kairix.use_cases.contradict import ContradictOutput
from kairix.use_cases.prep import PrepOutput
from kairix.use_cases.research import ResearchOutput
from kairix.use_cases.search import SearchOutput
from kairix.use_cases.timeline import TimelineResult

# ---------------------------------------------------------------------------
# brief — uses format_output(out, *, print_full: bool)
# ---------------------------------------------------------------------------


def _brief_render(result: BriefOutput, argv: list[str]) -> str:
    _positionals, flags = _parse_kv_flags(argv)
    print_full = "print-output" in flags
    return _brief_format_output(result, print_full=print_full)


register_composer(
    "brief",
    TextModeComposer(
        from_envelope=BriefOutput.from_envelope,
        format_text=_brief_render,
        name="brief",
    ),
)


# ---------------------------------------------------------------------------
# search — uses format_text(out)
# ---------------------------------------------------------------------------


def _search_render(result: SearchOutput, _argv: list[str]) -> str:
    return _search_format_text(result)


register_composer(
    "search",
    TextModeComposer(
        from_envelope=SearchOutput.from_envelope,
        format_text=_search_render,
        name="search",
    ),
)


# ---------------------------------------------------------------------------
# bootstrap — uses bootstrap_output_to_markdown(out)
# ---------------------------------------------------------------------------


def _bootstrap_render(result: BootstrapOutput, _argv: list[str]) -> str:
    return _bootstrap_format_text(result)


register_composer(
    "bootstrap",
    TextModeComposer(
        from_envelope=BootstrapOutput.from_envelope,
        format_text=_bootstrap_render,
        name="bootstrap",
    ),
)


# ---------------------------------------------------------------------------
# prep — uses format_text(out)
# ---------------------------------------------------------------------------


def _prep_render(result: PrepOutput, _argv: list[str]) -> str:
    return _prep_format_text(result)


register_composer(
    "prep",
    TextModeComposer(
        from_envelope=PrepOutput.from_envelope,
        format_text=_prep_render,
        name="prep",
    ),
)


# ---------------------------------------------------------------------------
# research — uses format_text(out)
# ---------------------------------------------------------------------------


def _research_render(result: ResearchOutput, _argv: list[str]) -> str:
    return _research_format_text(result)


register_composer(
    "research",
    TextModeComposer(
        from_envelope=ResearchOutput.from_envelope,
        format_text=_research_render,
        name="research",
    ),
)


# ---------------------------------------------------------------------------
# contradict — uses format_text(out, top_k, threshold) — both from argv
# ---------------------------------------------------------------------------


def _contradict_render(result: ContradictOutput, argv: list[str]) -> str:
    _positionals, flags = _parse_kv_flags(argv)
    top_k = _int_or(flags.get("top-k"), 5)
    threshold = _float_or(flags.get("threshold"), 0.45)
    return _contradict_format_text(result, top_k=top_k, threshold=threshold)


register_composer(
    "contradict",
    TextModeComposer(
        from_envelope=ContradictOutput.from_envelope,
        format_text=_contradict_render,
        name="contradict",
    ),
)


# ---------------------------------------------------------------------------
# timeline — uses format_header(out, limit) + format_results(out)
# ---------------------------------------------------------------------------


def _timeline_render(result: TimelineResult, argv: list[str]) -> str:
    _positionals, flags = _parse_kv_flags(argv)
    limit = _int_or(flags.get("limit"), 10)
    return _timeline_format_header(result, limit) + "\n\n" + _timeline_format_results(result)


register_composer(
    "timeline",
    TextModeComposer(
        from_envelope=TimelineResult.from_envelope,
        format_text=_timeline_render,
        name="timeline",
    ),
)


# ---------------------------------------------------------------------------
# caches — uses _format_text(rows: list[CacheRow]) over the envelope's
# ``caches`` list. PR 3.1 / #422 — operators run ``kairix caches`` and
# the dispatcher routes through the warm MCP server's ``caches_status``
# tool so the rendered table reflects the long-running process's cache
# effectiveness instead of a freshly-spawned CLI's empty caches.
# ---------------------------------------------------------------------------


def _caches_from_envelope(envelope: dict[str, Any]) -> list[CacheRow]:
    """Project the ``tool_caches_status`` envelope back to a list of rows.

    Only the ``caches`` key matters for text rendering; ``process_pid``
    + ``process_uptime_s`` are JSON-mode operator affordances that
    ``_format_text`` doesn't surface (and the in-process path never
    has, so byte-parity is preserved).
    """
    raw_rows = envelope.get("caches") or []
    return [CacheRow.from_envelope(row) for row in raw_rows if isinstance(row, dict)]


def _caches_render(rows: list[CacheRow], _argv: list[str]) -> str:
    """Render the warm-MCP envelope's rows through the existing text formatter."""
    return _caches_format_text(rows)


register_composer(
    "caches",
    TextModeComposer(
        from_envelope=_caches_from_envelope,
        format_text=_caches_render,
        name="caches",
    ),
)


# Side-effect module — no public symbols. Importing the module is what
# wires every composer into the registry.
__all__: list[str] = []
