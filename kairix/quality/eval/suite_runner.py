"""Suite runner for the Plan B-parity ``kairix eval`` operator surface.

Discovers a conversation corpus on disk (``session-NNN.jsonl`` +
``ground-truth-queries.json`` + optional ``ground-truth-facts.json``),
ingests the sessions through the configured ``FactStore`` / ``FactExtractor``,
runs each query through the configured backend, and scores results against
ground truth.

Two metrics emerge from one suite run:

- **query-pass-rate** — per-question pass/score against the ground-truth
  answer, broken down by category (``single-hop`` / ``multi-hop`` /
  ``temporal`` / ``open-domain`` / ``adversarial``).
- **extractor-f1** — if ``ground-truth-facts.json`` is present, score the
  extractor's output via precision / recall / F1 of matched
  (entity, attribute, substring(value)) tuples.

Both metrics are emitted in a single :class:`SuiteResult` dataclass so the
operator surface and the regression-gate CI step read the same artefact.

Design contract:

- **Dependency injection is total.** ``fact_store`` / ``fact_extractor``
  / ``llm`` / ``paths`` are constructor-injected. Tests pass fakes from
  ``tests/fakes.py``; production wires the real implementations at the
  CLI layer. F1: no monkeypatching, no internal-attribute reassignment.
- **F26-clean.** Imports the ``LLMBackend`` Protocol from
  ``kairix.platform.llm.protocol`` rather than reaching into providers.
- **Hermetic ingest in tests.** Sessions are ingested into a
  caller-supplied :class:`KairixPaths` (typically ``tmp_path``-rooted)
  so a unit run never touches the operator's real document store.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kairix.core.protocols import FactExtractor, FactStore
from kairix.paths import KairixPaths
from kairix.platform.llm.protocol import LLMBackend

logger = logging.getLogger(__name__)

__all__ = [
    "SuiteResult",
    "SuiteRunner",
    "SuiteSpec",
]


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


# Recognised ground-truth-queries category vocabulary. Mirrors the LoCoMo
# taxonomy and matches what reference-library/conversations/README.md
# documents. Unknown categories fall back to "uncategorised" so a
# malformed entry is visible but doesn't crash the gate.
_KNOWN_CATEGORIES: tuple[str, ...] = (
    "single-hop",
    "multi-hop",
    "temporal",
    "open-domain",
    "adversarial",
)

# Pass-threshold for query-score against ground truth. A score of >= 0.5
# is considered a pass - the LLM-judge prompt is a 0.0-1.0 graded judgement
# and 0.5 is the documented "partially correct" boundary.
_PASS_THRESHOLD: float = 0.5


@dataclass(frozen=True)
class SuiteSpec:
    """Discovered shape of a conversation corpus on disk.

    Produced by :meth:`SuiteRunner.discover_suite`; consumed by
    :meth:`SuiteRunner.run`. Kept as a frozen dataclass so test
    helpers can build one directly without going through disk.
    """

    name: str
    path: Path
    session_paths: tuple[Path, ...]
    queries: tuple[dict[str, Any], ...]
    ground_truth_facts: tuple[dict[str, Any], ...] | None


@dataclass(frozen=True)
class SuiteResult:
    """Outcome of running one suite — both metrics in one envelope.

    The shape is JSON-serialisable (every value is a primitive, dict, or
    list). The CLI ``--json`` flag round-trips this dataclass via
    :func:`dataclasses.asdict`.
    """

    suite_name: str
    n_questions: int
    n_passed: int
    mean_score: float
    per_category: dict[str, dict[str, float]]
    per_extraction_f1: float | None
    extraction_precision: float | None
    extraction_recall: float | None
    rows: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public runner
# ---------------------------------------------------------------------------


class SuiteRunner:
    """Discover + run + score a conversation eval suite.

    All collaborators are constructor-injected; the runner itself is a
    pure orchestrator. Tests pass ``FakeFactStore`` / ``FakeFactExtractor``
    / ``FakeLLMBackend``; production wires real implementations.
    """

    def __init__(
        self,
        *,
        fact_store: FactStore,
        fact_extractor: FactExtractor,
        llm: LLMBackend,
        paths: KairixPaths,
    ) -> None:
        self._fact_store = fact_store
        self._fact_extractor = fact_extractor
        self._llm = llm
        self._paths = paths

    # -----------------------------------------------------------------
    # Discovery
    # -----------------------------------------------------------------

    def discover_suite(self, suite_path: Path) -> SuiteSpec:
        """Locate sessions + ground-truth files under ``suite_path``.

        Required: at least one ``session-*.jsonl`` AND
        ``ground-truth-queries.json``. Optional:
        ``ground-truth-facts.json`` — if absent, the run skips the
        extractor F1 metric (still emits query metrics).

        Raises:
            ValueError: with actionable ``fix:`` / ``next:`` markers
                if a required file is missing.
        """
        if not suite_path.exists() or not suite_path.is_dir():
            raise ValueError(
                f"Suite path {suite_path!r} does not exist or is not a directory. "
                f"fix: pass a directory under reference-library/conversations/. "
                f"next: run `ls reference-library/conversations/` to see candidates."
            )

        sessions = tuple(sorted(suite_path.glob("session-*.jsonl")))
        if not sessions:
            raise ValueError(
                f"No session-*.jsonl files found under {suite_path!r}. "
                f"fix: add at least one session-001.jsonl file with conversation turns. "
                f"next: see reference-library/conversations/README.md for the JSONL shape."
            )

        queries_path = suite_path / "ground-truth-queries.json"
        if not queries_path.exists():
            raise ValueError(
                f"Required file {queries_path!r} is missing. "
                f"fix: add a ground-truth-queries.json file with question/answer pairs. "
                f"next: see reference-library/conversations/README.md for the schema."
            )

        queries = _load_json_list(queries_path)

        facts_path = suite_path / "ground-truth-facts.json"
        gt_facts: tuple[dict[str, Any], ...] | None = None
        if facts_path.exists():
            gt_facts = _load_json_list(facts_path)

        return SuiteSpec(
            name=suite_path.name,
            path=suite_path,
            session_paths=sessions,
            queries=queries,
            ground_truth_facts=gt_facts,
        )

    # -----------------------------------------------------------------
    # Orchestration
    # -----------------------------------------------------------------

    def run(self, suite: SuiteSpec) -> SuiteResult:
        """Ingest sessions, score every query, optionally score extractor F1.

        Workflow:

        1. Read every ``session-*.jsonl`` and feed turns through the
           configured ``FactExtractor``; persist returned records via
           ``FactStore.add``.
        2. For each query in ``ground-truth-queries.json``, run a
           recall + LLM-judge pass; record per-question score + pass.
        3. If ``ground-truth-facts.json`` is present, compute F1 of
           the extractor's output against ground truth.
        """
        extracted_facts = self._ingest_sessions(suite.session_paths)

        rows, per_cat = self._score_queries(suite.queries)
        n_questions = len(rows)
        n_passed = sum(1 for r in rows if r["pass"])
        mean_score = (sum(r["score"] for r in rows) / n_questions) if n_questions else 0.0

        f1, precision, recall = self._score_extraction(extracted_facts, suite.ground_truth_facts)

        return SuiteResult(
            suite_name=suite.name,
            n_questions=n_questions,
            n_passed=n_passed,
            mean_score=mean_score,
            per_category=per_cat,
            per_extraction_f1=f1,
            extraction_precision=precision,
            extraction_recall=recall,
            rows=rows,
        )

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------

    def _ingest_sessions(self, session_paths: Iterable[Path]) -> list[Any]:
        """Read every session JSONL, extract facts, persist via FactStore.

        Returns the flat list of extracted facts so the extractor F1
        score path can compare them against ground truth without a
        second round-trip through the store.
        """
        extracted: list[Any] = []
        for sp in session_paths:
            turns = _read_session(sp)
            if not turns:
                continue
            facts = self._fact_extractor.extract(turns=turns)
            for fact in facts:
                self._fact_store.add(fact)
                extracted.append(fact)
        return extracted

    def _score_queries(
        self, queries: Iterable[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]]]:
        """Score every query and return (rows, per_category) summary."""
        rows: list[dict[str, Any]] = []
        per_cat_raw: dict[str, list[float]] = {}

        for q in queries:
            question = str(q.get("question", ""))
            answer = str(q.get("answer", ""))
            category = str(q.get("category", "uncategorised"))
            if category not in _KNOWN_CATEGORIES:
                category = "uncategorised"

            hits = self._fact_store.search(question, top_k=5)
            context = _hits_to_context(hits)
            score = self._judge(question=question, expected=answer, context=context)
            passed = score >= _PASS_THRESHOLD

            rows.append(
                {
                    "question": question,
                    "answer": answer,
                    "category": category,
                    "score": score,
                    "pass": passed,
                }
            )
            per_cat_raw.setdefault(category, []).append(score)

        per_cat = {
            cat: {
                "n": float(len(scores)),
                "passed": float(sum(1 for s in scores if s >= _PASS_THRESHOLD)),
                "mean": (sum(scores) / len(scores)) if scores else 0.0,
            }
            for cat, scores in per_cat_raw.items()
        }
        return rows, per_cat

    def _judge(self, *, question: str, expected: str, context: str) -> float:
        """LLM-judge prompt - returns a graded 0.0-1.0 score.

        The prompt asks the LLM for a single float on its own line. If
        the response is malformed (no parseable float, value outside
        [0,1]), the score is treated as 0.0 — degraded-mode fail-safe
        rather than crash-on-malformed-judge.
        """
        prompt = [
            {
                "role": "system",
                "content": (
                    "You score retrieval answers. Respond with a single float "
                    "between 0.0 and 1.0 on its own line. 1.0 = exact match. "
                    "0.5 = partially correct. 0.0 = wrong or missing."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Question: {question}\nExpected answer: {expected}\n"
                    f"Retrieved context:\n{context}\n\nScore (0.0-1.0):"
                ),
            },
        ]
        response = self._llm.chat(prompt, max_tokens=8)
        return _parse_score(response)

    def _score_extraction(
        self,
        extracted: list[Any],
        ground_truth: tuple[dict[str, Any], ...] | None,
    ) -> tuple[float | None, float | None, float | None]:
        """Compute precision/recall/F1 of extracted facts vs ground truth.

        Returns ``(None, None, None)`` when ``ground_truth is None`` —
        the use case tolerates missing ``ground-truth-facts.json`` so
        suites that only score query pass-rate still run.
        """
        if ground_truth is None:
            return None, None, None

        gt_total = len(ground_truth)
        ext_total = len(extracted)
        if gt_total == 0 and ext_total == 0:
            return 1.0, 1.0, 1.0
        if gt_total == 0:
            return 0.0, 0.0, 0.0

        matched = 0
        for gt in ground_truth:
            if _has_matching_extracted(gt, extracted):
                matched += 1

        precision = (matched / ext_total) if ext_total else 0.0
        recall = matched / gt_total
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        return f1, precision, recall


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _load_json_list(path: Path) -> tuple[dict[str, Any], ...]:
    """Read a JSON array of objects from ``path``.

    Raises:
        ValueError: with actionable markers if the file is not JSON or
            does not contain a list of objects.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"File {path!r} is not valid JSON: {exc}. "
            f"fix: validate with `python -m json.tool {path}`. "
            f"next: see reference-library/conversations/README.md for the schema."
        ) from exc
    if not isinstance(raw, list):
        raise ValueError(
            f"File {path!r} must contain a JSON array, got {type(raw).__name__}. "
            f"fix: wrap the entries in [ ... ]. "
            f"next: see reference-library/conversations/README.md."
        )
    return tuple(item for item in raw if isinstance(item, dict))


def _read_session(path: Path) -> list[dict[str, Any]]:
    """Parse a ``session-*.jsonl`` file into a list of turn dicts.

    Malformed lines are logged + skipped, mirroring the
    ``ingest_chat`` use case's tolerant parser.
    """
    turns: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("eval-suite: skipping malformed line in %s: %s", path, exc)
                continue
            if isinstance(obj, dict):
                turns.append(obj)
    return turns


def _hits_to_context(hits: list[Any]) -> str:
    """Stringify FactHit objects into a context block for the LLM judge."""
    lines: list[str] = []
    for hit in hits:
        try:
            rec = hit.record
            lines.append(f"- {rec.entity} {rec.attribute} = {rec.value}")
        except AttributeError:
            # Some backends return raw memory shapes; fall back to content.
            content = getattr(hit, "content", None)
            if content:
                lines.append(f"- {content}")
    return "\n".join(lines) if lines else "(no relevant facts retrieved)"


def _parse_score(response: str) -> float:
    """Parse the LLM-judge response into a 0.0-1.0 float.

    Robust to leading/trailing whitespace, surrounding text, and
    malformed responses (return 0.0 rather than raising).
    """
    if not response:
        return 0.0
    # Try the cleanest case first: the whole response is a float.
    stripped = response.strip()
    try:
        value = float(stripped)
    except ValueError:
        # Otherwise scan tokens for the first parseable float.
        value = _first_float_in(stripped)
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _first_float_in(text: str) -> float:
    """Return the first parseable float in ``text``, or 0.0 if none."""
    for token in text.replace(",", " ").split():
        try:
            return float(token)
        except ValueError:
            continue
    return 0.0


def _has_matching_extracted(gt_fact: dict[str, Any], extracted: list[Any]) -> bool:
    """Return True iff one of ``extracted`` matches ``gt_fact``.

    Match definition (per the brief): same ``entity`` + same
    ``attribute`` + ground-truth ``value`` is at least a substring of
    the extracted record's value (case-insensitive). The substring
    direction is "extracted value contains GT value" — an extractor
    that emits a longer-than-needed answer still counts as having
    found the ground-truth fact.
    """
    gt_entity = str(gt_fact.get("entity", "")).strip().lower()
    gt_attribute = str(gt_fact.get("attribute", "")).strip().lower()
    gt_value = str(gt_fact.get("value", "")).strip().lower()
    for ext in extracted:
        try:
            if (
                str(ext.entity).strip().lower() == gt_entity
                and str(ext.attribute).strip().lower() == gt_attribute
                and gt_value in str(ext.value).strip().lower()
            ):
                return True
        except AttributeError:
            continue
    return False
