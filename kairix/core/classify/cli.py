"""
kairix classify — auto-classify memory writes.

Usage:
  kairix classify "<content>" [--agent <agent>]
  echo "<content>" | kairix classify --agent builder

Output: JSON to stdout
  {"type": "...", "target_path": "...", "confidence": 0.xx, "reason": "..."}
  {"type": "...", "target_path": "...", "confidence": 0.xx, "reason": "...", "needs_confirmation": true}
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any


def _resolve_classifiers(
    rule_classifier: Callable[..., Any] | None,
    llm_classifier: Callable[..., Any] | None,
    config: dict[str, object] | None,
) -> tuple[Callable[..., Any], Callable[..., Any]]:
    """Resolve the production rule + LLM classifier when callers leave them ``None``.

    Lazy-imports keep heavy modules out of the CLI's import path until
    actually invoked; tests inject fakes through the public seams. The
    production closures thread ``config`` into the classifiers so the
    config-driven agent allowlist (#472) sees the same ``agents:`` block
    the CLI validated against; injected fakes keep their historical
    ``(content, *, agent)`` contract untouched.
    """
    if rule_classifier is None:
        from kairix.core.classify.rules import classify_content

        def _rule(content: str, *, agent: str) -> Any:
            return classify_content(content, agent=agent, config=config)

        rule_classifier = _rule
    if llm_classifier is None:
        from kairix.core.classify.judge import classify_with_llm

        def _llm(content: str, *, agent: str) -> Any:
            return classify_with_llm(content, agent=agent, config=config)

        llm_classifier = _llm
    return rule_classifier, llm_classifier


def _load_config() -> dict[str, object] | None:
    """Production config loader — parsed ``kairix.config.yaml`` or None."""
    from kairix.paths import load_top_level_config

    return load_top_level_config()


def _resolve_content(parsed_content: str | None) -> str:
    """Return the content to classify — argument or piped stdin.

    Exits 1 with an actionable stderr line when neither is supplied.
    """
    if parsed_content is not None:
        return parsed_content
    if not sys.stdin.isatty():
        return sys.stdin.read()
    print(
        "Error: no content provided (pass as argument or pipe via stdin)",
        file=sys.stderr,
    )
    sys.exit(1)


def _validate_agent(agent: str, config: dict[str, object] | None) -> None:
    """Exit 1 with the F21-actionable message when ``agent`` is not allowed.

    The allowlist is configured ``agents:`` names + the legacy built-in
    set (#472) — same rule the classifiers enforce downstream.
    """
    from kairix.core.classify.router import invalid_agent_message, valid_agents

    allowed = valid_agents(config)
    if agent not in allowed:
        print(
            f"Error: {invalid_agent_message(agent, allowed)}",
            file=sys.stderr,
        )
        sys.exit(1)


def _emit_classification_failure(detail: str, exc: Exception) -> None:
    """Log the failure detail and exit 1 with the masked JSON error envelope."""
    print(
        json.dumps({"error": "Classification failed — check server logs"}),
        file=sys.stderr,
    )
    import logging as _logging

    _logging.getLogger(__name__).warning("classify CLI %s: %s", detail, exc)
    sys.exit(1)


def main(
    args: list[str] | None = None,
    *,
    rule_classifier: Callable[..., Any] | None = None,
    llm_classifier: Callable[..., Any] | None = None,
    config: dict[str, object] | None = None,
) -> None:
    """Entry point for `kairix classify`.

    ``rule_classifier`` and ``llm_classifier`` are the public DI seams for
    tests that want to drive error paths through the public CLI surface
    instead of monkey-patching the classify-module imports. Production
    callers leave them at ``None`` and the CLI lazy-imports the real ones.
    ``config`` is the parsed ``kairix.config.yaml`` seam for the
    config-driven agent allowlist (#472) — production callers leave it
    ``None`` and the CLI loads the operator's file.
    """
    import argparse

    effective_config = config if config is not None else _load_config()
    rule_classifier, llm_classifier = _resolve_classifiers(rule_classifier, llm_classifier, effective_config)

    if args is None:
        args = sys.argv[2:]  # strip 'kairix classify'

    parser = argparse.ArgumentParser(
        prog="kairix classify",
        description="Auto-classify memory writes to the correct document path.",
    )
    parser.add_argument(
        "content",
        nargs="?",
        default=None,
        help="Content to classify (or pipe via stdin).",
    )
    parser.add_argument(
        "--agent",
        default="shared",
        help="Agent name for path scoping (builder, shape, growth, consultant, shared).",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        default=False,
        help="Disable LLM fallback — return unknown if no rule matches.",
    )

    parsed = parser.parse_args(args)

    content = _resolve_content(parsed.content)
    agent = parsed.agent
    use_llm = not parsed.no_llm

    # Run classification
    try:
        _validate_agent(agent, effective_config)

        result = rule_classifier(content, agent=agent)

        # If rule didn't match, try LLM judge
        if result.type == "unknown" and use_llm:
            result = llm_classifier(content, agent=agent)

        output: dict = {
            "type": result.type,
            "target_path": result.target_path,
            "confidence": round(result.confidence, 2),
            "reason": result.reason,
        }
        if result.needs_confirmation:
            output["needs_confirmation"] = True

        print(json.dumps(output))

    except ValueError as e:
        _emit_classification_failure("ValueError", e)
    except Exception as e:
        _emit_classification_failure("unexpected error", e)
