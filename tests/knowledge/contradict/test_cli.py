"""Unit tests for ``kairix.knowledge.contradict.cli`` pure helpers.

Phase 2 of #168 made the CLI a thin adapter — argv parsing + result
formatting only. The use case logic lives in
``kairix.use_cases.contradict.run_contradict`` (covered in
``tests/use_cases/test_contradict.py``). These tests pin the formatters.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from kairix.knowledge.contradict.cli import build_parser, format_text, to_json_envelope
from kairix.use_cases.contradict import ContradictDeps, ContradictionHit, ContradictOutput

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------


def test_build_parser_check_subcommand_accepts_content() -> None:
    args = build_parser().parse_args(["check", "some claim"])
    assert args.subcommand == "check"
    assert args.content == "some claim"
    assert args.top_k == 5
    assert args.threshold == pytest.approx(0.45)
    assert args.top_claims == 3
    assert args.format == "text"
    assert args.agent == "shared"


def test_build_parser_check_accepts_all_flags() -> None:
    args = build_parser().parse_args(
        [
            "check",
            "claim",
            "--top-k",
            "8",
            "--threshold",
            "0.7",
            "--top-claims",
            "5",
            "--format",
            "json",
            "--agent",
            "builder",
        ]
    )
    assert args.top_k == 8
    assert args.threshold == pytest.approx(0.7)
    assert args.top_claims == 5
    assert args.format == "json"
    assert args.agent == "builder"


def test_build_parser_rejects_unknown_format() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["check", "claim", "--format", "yaml"])


# ---------------------------------------------------------------------------
# format_text
# ---------------------------------------------------------------------------


def test_format_text_no_contradictions_renders_default_message() -> None:
    out = ContradictOutput(content="claim")
    text = format_text(out, top_k=5, threshold=0.45)
    assert "No contradictions found" in text
    assert "top_k=5" in text
    assert "threshold=0.45" in text


def test_format_text_renders_each_hit_with_category_score_path() -> None:
    out = ContradictOutput(
        content="System uses A",
        contradictions=[
            ContradictionHit(
                path="docs/old.md",
                score=0.78,
                reason="contradicts X",
                snippet="The system uses option B." * 5,
                category="status_mismatch",
                claim="System uses A",
            ),
        ],
        has_contradictions=True,
    )
    text = format_text(out, top_k=5, threshold=0.45)
    assert "1 contradiction(s) found" in text
    assert "Category: status_mismatch" in text
    assert "Score: 0.78" in text
    assert "Path: docs/old.md" in text
    assert "Reason: contradicts X" in text
    assert "Snippet:" in text
    assert "..." in text  # snippet truncated at 120 chars


def test_format_text_short_circuits_on_error() -> None:
    out = ContradictOutput(content="c", error="ConnectionError: no Neo4j")
    text = format_text(out, top_k=5, threshold=0.45)
    assert text.startswith("error:")
    assert "ConnectionError" in text


# ---------------------------------------------------------------------------
# to_json_envelope
# ---------------------------------------------------------------------------


def test_to_json_envelope_returns_array_of_hit_dicts() -> None:
    out = ContradictOutput(
        content="c",
        contradictions=[
            ContradictionHit(
                path="docs/old.md",
                score=0.785432,
                reason="contradicts X",
                snippet="snippet",
                category="overstatement",
                claim="C",
            ),
        ],
        has_contradictions=True,
    )
    payload = to_json_envelope(out)
    assert isinstance(payload, list)
    assert len(payload) == 1
    row = payload[0]
    assert row["doc_path"] == "docs/old.md"
    assert row["score"] == pytest.approx(0.7854)  # rounded to 4 decimals
    assert row["reason"] == "contradicts X"
    assert row["category"] == "overstatement"
    assert row["claim"] == "C"
    # Round-trip via json to confirm serialisable.
    assert json.loads(json.dumps(payload)) == payload


def test_to_json_envelope_empty_returns_empty_array() -> None:
    out = ContradictOutput(content="c")
    assert to_json_envelope(out) == []


# ---------------------------------------------------------------------------
# main — drives the use case via ContradictDeps injection
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): replaced ``sys.exit(1)`` with ``return``
# in the no-subcommand branch; ``pytest.raises(SystemExit)`` no longer
# fired and the test failed. Restored.
def test_main_with_no_subcommand_prints_help_and_exits_one(capsys: pytest.CaptureFixture[str]) -> None:
    """``main([])`` falls through the subcommand guard → exit(1)."""
    from kairix.knowledge.contradict.cli import main

    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    # argparse prints help to stdout.
    assert "kairix contradict" in captured.out or "kairix contradict" in captured.err


def _check_deps_returning_empty() -> ContradictDeps:
    """A ContradictDeps that returns no hits and a no-op LLM (no real I/O)."""

    class _NoopLLM:
        def chat(self, _messages: list[dict[str, Any]]) -> str:
            return "{}"

    def _check_fn(**_kwargs: Any) -> list[Any]:
        return []

    return ContradictDeps(check_fn=_check_fn, llm_backend=_NoopLLM())


# Sabotage-proof (executed): removed ``args.as_json`` branch entirely so
# ``main`` always entered ``--format text``; the
# ``parsed["has_contradictions"]`` assertion fired with KeyError because
# stdout was the text "No contradictions found" line, not the envelope
# dict. Restored.
def test_main_with_json_flag_prints_envelope_dict_to_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main([..., "--json"])`` emits the MCP envelope dict on stdout."""
    from kairix.knowledge.contradict.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["check", "agent-alpha claim", "--json"], deps=_check_deps_returning_empty())
    assert exc.value.code == 0
    parsed = json.loads(capsys.readouterr().out)
    assert isinstance(parsed, dict)
    assert parsed["content"] == "agent-alpha claim"
    assert parsed["contradictions"] == []
    assert parsed["has_contradictions"] is False
    assert parsed["error"] == ""


# Sabotage-proof (executed): made the ``--format json`` branch print a
# dict instead of the legacy list; the ``isinstance(parsed, list)``
# assertion fired. Restored.
def test_main_with_format_json_prints_legacy_list_to_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main([..., "--format", "json"])`` keeps the legacy flat-list shape."""
    from kairix.knowledge.contradict.cli import main

    with pytest.raises(SystemExit) as exc:
        main(
            ["check", "agent-beta claim", "--format", "json"],
            deps=_check_deps_returning_empty(),
        )
    assert exc.value.code == 0
    parsed = json.loads(capsys.readouterr().out)
    assert isinstance(parsed, list)
    assert parsed == []


# Sabotage-proof (executed): mutated the text-mode default branch to
# print "" instead of ``format_text(out, ...)``; the
# ``"No contradictions found" in captured.out`` assertion fired.
# Restored.
def test_main_default_text_mode_renders_human_readable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Default (no ``--json``, no ``--format``) emits the operator text line."""
    from kairix.knowledge.contradict.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["check", "agent-gamma claim"], deps=_check_deps_returning_empty())
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "No contradictions found" in captured.out
    # Text mode must not produce a JSON dict.
    assert not captured.out.lstrip().startswith("{")
    assert not captured.out.lstrip().startswith("[")
