"""LLM-driven ``FactExtractor`` — Plan B-parity Capability #2.

Production implementation of the :class:`~kairix.core.protocols.FactExtractor`
Protocol. Wires a windowed batch of conversation turns through a
configured :class:`~kairix.platform.llm.LLMBackend` and parses the
returned JSON array into :class:`~kairix.core.facts.records.StoredFactRecord`
instances.

Design contract
---------------

- **Dependency injection.** The constructor takes an ``LLMBackend``
  Protocol — never a concrete provider class. F26-clean: this module
  imports only from :mod:`kairix.core` and :mod:`kairix.platform.llm`.
- **Bundled prompt template.** The default prompt lives next to this
  module under ``prompts/fact_extractor_v1.txt`` and ships as Python
  package data. Callers can override via ``prompt_template=`` kwarg
  (tests pin a tiny template so a single string-equality assertion
  exercises the call surface).
- **Tolerant of malformed LLM output.** A non-JSON response, a JSON
  document that isn't a list, or a list element missing required keys
  is logged at WARNING and skipped — the extractor never raises on
  output-shape errors. Returning ``[]`` is a valid "no facts" signal,
  per the :class:`FactExtractor` Protocol.
- **Deterministic ids.** Each emitted record's id is computed via
  :meth:`StoredFactRecord.mint_id` from
  ``(entity, attribute, source_turn_ids)`` so re-extracting the same
  window produces the same ids — matches the
  :class:`~kairix.core.protocols.FactRecord` identity contract that
  makes :meth:`FactStore.add` idempotent.

Why ``temperature`` is an extractor attribute rather than a chat kwarg
---------------------------------------------------------------------

The :class:`LLMBackend` Protocol's ``chat(messages, max_tokens=800)``
signature deliberately does not carry sampling knobs — provider
plug-ins resolve those from their own configuration so the call surface
stays narrow. The extractor therefore *stores* the configured
temperature so callers (and tests) can verify the value the production
wire-up will hand to the backend factory, but the chat call itself is
shape-compatible with every backend the platform layer ships.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from importlib import resources
from typing import Any

from kairix.core.facts.records import StoredFactRecord
from kairix.core.protocols import FactRecord
from kairix.platform.llm.protocol import LLMBackend

logger = logging.getLogger(__name__)

__all__ = ["LLMFactExtractor"]


_DEFAULT_PROMPT_RESOURCE = "fact_extractor_v1.txt"
_PROMPT_PACKAGE = "kairix.core.facts.prompts"
_TURNS_PLACEHOLDER = "{{turns}}"

# Payload-key names — single source of truth so a schema rename here
# propagates everywhere the parser indexes the LLM's JSON output.
_KEY_ENTITY = "entity"
_KEY_ATTRIBUTE = "attribute"
_KEY_VALUE = "value"
_KEY_CONFIDENCE = "confidence"
_KEY_EVIDENCE = "evidence_turn_ids"

_REQUIRED_KEYS = (_KEY_ENTITY, _KEY_ATTRIBUTE, _KEY_VALUE, _KEY_CONFIDENCE, _KEY_EVIDENCE)
_MAX_TOKENS = 2000


def _load_default_prompt() -> str:
    """Return the bundled ``fact_extractor_v1.txt`` prompt template.

    Read via :func:`importlib.resources.files` so it works from both a
    source checkout and an installed wheel — no ``__file__``-based
    path lookups that break inside zipapps or shadow-mounted images.
    """
    return resources.files(_PROMPT_PACKAGE).joinpath(_DEFAULT_PROMPT_RESOURCE).read_text(encoding="utf-8")


def _format_turns(turns: list[dict[str, Any]]) -> str:
    """Render the windowed turns into the ``<id>:<speaker>: <content>`` line shape.

    Missing ``id`` / ``role`` / ``content`` keys are tolerated with
    sensible defaults: the extractor still emits a line so the LLM can
    decide whether the turn is groundable, rather than silently dropping
    upstream-malformed input.
    """
    lines = []
    for turn in turns:
        turn_id = str(turn.get("id", ""))
        speaker = str(turn.get("role", turn.get("speaker", "")))
        content = str(turn.get("content", turn.get("text", "")))
        lines.append(f"{turn_id}:{speaker}: {content}")
    return "\n".join(lines)


def _coerce_evidence_turn_ids(raw: Any) -> tuple[str, ...] | None:
    """Coerce the LLM's ``evidence_turn_ids`` field to a tuple of strs.

    Returns ``None`` if the value isn't list/tuple-shaped — the caller
    treats that as a malformed record and skips it. Accepts both
    int and str ids because some upstream transcripts number turns
    numerically while others use string ids.
    """
    if not isinstance(raw, list | tuple):
        return None
    return tuple(str(x) for x in raw)


def _coerce_confidence(raw: Any) -> float | None:
    """Coerce the LLM's confidence to a float in ``[0.0, 1.0]``.

    Returns ``None`` if the value isn't a number or falls outside the
    range — the caller treats that as a malformed record and skips it.
    """
    if isinstance(raw, bool):
        # bool is a subclass of int; reject to avoid silently coercing True→1.0.
        return None
    if not isinstance(raw, int | float):
        return None
    conf = float(raw)
    if not 0.0 <= conf <= 1.0:
        return None
    return conf


def _parse_response(raw_response: str) -> list[dict[str, Any]]:
    """Parse the LLM's raw text into a list of dicts; log + return ``[]`` on failure.

    Tolerant of leading/trailing whitespace and the LLM accidentally
    wrapping its JSON in a markdown fence (``... ``json ... `````).
    """
    stripped = raw_response.strip()
    if stripped.startswith("```"):
        # Strip a one-line fence + optional language tag, then the trailing fence.
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped
        if stripped.endswith("```"):
            stripped = stripped.rsplit("```", 1)[0]
        stripped = stripped.strip()
    if not stripped:
        return []
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        logger.warning("llm-fact-extractor: response was not valid JSON: %s", exc)
        return []
    if not isinstance(parsed, list):
        logger.warning("llm-fact-extractor: response was JSON but not a list: %r", type(parsed).__name__)
        return []
    return parsed


def _record_from_payload(payload: Any, namespace: str, extracted_at: str) -> StoredFactRecord | None:
    """Build a :class:`StoredFactRecord` from one parsed LLM payload dict.

    Returns ``None`` if the payload is missing any required key, has an
    unusable ``confidence`` or ``evidence_turn_ids`` shape, or carries
    an empty evidence list (the Protocol forbids empty source-turn
    provenance — see :class:`FactRecord`).
    """
    if not isinstance(payload, dict):
        logger.warning("llm-fact-extractor: list element was not a dict: %r", type(payload).__name__)
        return None
    missing = [key for key in _REQUIRED_KEYS if key not in payload]
    if missing:
        logger.warning("llm-fact-extractor: record missing required keys %r in %r", missing, payload)
        return None
    evidence = _coerce_evidence_turn_ids(payload[_KEY_EVIDENCE])
    if evidence is None or not evidence:
        logger.warning("llm-fact-extractor: record has unusable evidence_turn_ids: %r", payload[_KEY_EVIDENCE])
        return None
    confidence = _coerce_confidence(payload[_KEY_CONFIDENCE])
    if confidence is None:
        logger.warning("llm-fact-extractor: record has unusable confidence: %r", payload[_KEY_CONFIDENCE])
        return None
    entity = str(payload[_KEY_ENTITY])
    attribute = str(payload[_KEY_ATTRIBUTE])
    value = str(payload[_KEY_VALUE])
    fact_id = StoredFactRecord.mint_id(entity=entity, attribute=attribute, source_turn_ids=evidence)
    return StoredFactRecord(
        id=fact_id,
        entity=entity,
        attribute=attribute,
        value=value,
        confidence=confidence,
        source_turn_ids=evidence,
        extracted_at=extracted_at,
        superseded_by=None,
        namespace=namespace,
    )


class LLMFactExtractor:
    """Convert a window of conversation turns into ``FactRecord`` instances via an LLM.

    Implements the :class:`~kairix.core.protocols.FactExtractor`
    Protocol. The constructor takes a :class:`LLMBackend` Protocol
    object (typically obtained from
    :func:`kairix.platform.llm.get_default_backend`) plus optional
    prompt-template + temperature overrides.

    The extractor is **stateless** between calls — every ``extract()``
    formats turns into the template, dispatches one chat call, parses
    the response, and returns the FactRecord list. Malformed LLM output
    is logged and skipped, never raised.
    """

    def __init__(
        self,
        *,
        llm: LLMBackend,
        prompt_template: str | None = None,
        temperature: float = 0.0,
        namespace: str = "shared",
    ) -> None:
        """Construct the extractor.

        Parameters
        ----------
        llm:
            Any :class:`LLMBackend` Protocol implementation. Tests pass
            ``FakeLLMBackend`` from ``tests/fakes.py``; production wires
            the result of :func:`kairix.platform.llm.get_default_backend`.
        prompt_template:
            Optional override for the bundled prompt template. Must
            contain ``{{turns}}`` as the substitution placeholder. The
            default is loaded once on first construction from
            ``kairix/core/facts/prompts/fact_extractor_v1.txt``.
        temperature:
            Sampling temperature the operator intends the backend to
            use. Defaults to ``0.0`` for CI determinism. Stored as
            ``self.temperature`` for introspection; the
            :class:`LLMBackend` Protocol does not carry sampling knobs
            on its ``chat`` signature (provider plug-ins resolve those
            from their own config), so this attribute documents intent
            rather than passing through on every call.
        namespace:
            Default ``namespace`` stamped onto emitted records.
            Downstream :func:`kairix.use_cases.ingest_chat.ingest_chat`
            may re-stamp this from its own kwarg.
        """
        self._llm = llm
        self._prompt_template = prompt_template if prompt_template is not None else _load_default_prompt()
        self.temperature = temperature
        self._namespace = namespace

    def extract(
        self,
        *,
        turns: list[dict[str, Any]],
        window_hint: dict[str, Any] | None = None,
    ) -> list[FactRecord]:
        """Extract every grounded fact from ``turns`` via one LLM chat call.

        ``window_hint`` is accepted for Protocol conformance but is not
        yet woven into the prompt — Capability #4 wires session-scope
        metadata through this path. Today we log it at DEBUG so the
        F19 unused-params gate sees a Load-context reference, then
        proceed.

        Returns the empty list when there are no turns, when the LLM
        returns ``[]``, or when every parsed record is malformed. Never
        raises on shape errors.
        """
        logger.debug("llm-fact-extractor: extract %d turn(s); window_hint=%r", len(turns), window_hint)
        if not turns:
            return []
        formatted = _format_turns(turns)
        prompt = self._prompt_template.replace(_TURNS_PLACEHOLDER, formatted)
        raw = self._llm.chat([{"role": "user", "content": prompt}], max_tokens=_MAX_TOKENS)
        payloads = _parse_response(raw)
        extracted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        records: list[FactRecord] = []
        for payload in payloads:
            record = _record_from_payload(payload, namespace=self._namespace, extracted_at=extracted_at)
            if record is not None:
                records.append(record)
        return records
