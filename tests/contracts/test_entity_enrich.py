"""#415 — entity enricher contract tests.

Pins the four load-bearing behaviours:

1. ``fetch_wikidata_summary`` parses the canonical EntityData JSON shape
   and returns an EntitySummary; returns None on any failure.
2. ``enrich_entity`` is a no-op when the target has no ``wikidata_qid``
   (don't burn API budget on entities that won't enrich).
3. ``enrich_entity`` writes ``n.summary`` via the same Cypher shape the
   audit + health check assume (``SET n.summary = $summary``).
4. ``enrich_entity`` is idempotent: a second call against an entity with
   a populated summary skips the fetch + write (unless ``overwrite=True``).

F-rule discipline:
  - F1: no @patch on kairix internals — inject http_get + use
    FakeNeo4jClient.
  - F8: ``pytestmark = pytest.mark.contract``.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.knowledge.entities.enrich import (
    EntitySummary,
    enrich_all_missing,
    enrich_entity,
    fetch_wikidata_summary,
)

pytestmark = pytest.mark.contract


class _FakeResponse:
    """Minimal requests.Response stand-in for fetch_wikidata_summary."""

    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


def _wikidata_payload(qid: str, label: str, description: str) -> dict[str, Any]:
    """Build the canonical EntityData JSON shape Wikidata returns."""
    return {
        "entities": {
            qid: {
                "labels": {"en": {"language": "en", "value": label}},
                "descriptions": {"en": {"language": "en", "value": description}},
            }
        }
    }


class _RecordingNeo4j:
    """Minimal duck-typed Neo4j client capturing every cypher call.

    Returns ``lookup_rows`` for the first call, a single-row "matched"
    response for the SET cypher (proves the write landed) — mirrors the
    read-then-write flow inside enrich_entity. The ``cypher_calls`` list
    captures every call's (query, params, write_kwarg) so tests can
    assert the SET cypher used ``write=True`` (#416).
    """

    def __init__(self, *, lookup_rows: list[dict[str, Any]] | None = None) -> None:
        self._lookup_rows = lookup_rows or []
        self.cypher_calls: list[tuple[str, dict[str, Any], bool]] = []
        self._call_count = 0

    def cypher(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        *,
        write: bool = False,
    ) -> list[dict[str, Any]]:
        self.cypher_calls.append((query, dict(params or {}), write))
        self._call_count += 1
        if "MATCH (n {name: $name}) RETURN" in query:
            return list(self._lookup_rows)
        if "MATCH (n {name: $name}) SET" in query:
            # SET cypher carries a RETURN clause; a non-empty row signals
            # the MATCH landed and the SET applied.
            return [{"name": (params or {}).get("name", "")}]
        # MATCH (n) WHERE n.wikidata_qid ... — batch candidate query
        return list(self._lookup_rows)


# ---------------------------------------------------------------------------
# fetch_wikidata_summary
# ---------------------------------------------------------------------------


def test_fetch_wikidata_summary_parses_canonical_payload_shape() -> None:
    """Happy-path: Wikidata returns the standard EntityData JSON shape
    with labels + descriptions; the function returns a populated EntitySummary.

    Sabotage-proof: change the parser to read ``descriptions.en.label``
    instead of ``.value`` → test fails because description is empty.
    """
    calls: list[Any] = []

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        calls.append((url, kwargs))
        return _FakeResponse(_wikidata_payload("Q42", "Douglas Adams", "English author"))

    summary = fetch_wikidata_summary("Q42", http_get=fake_get)
    assert summary is not None
    assert summary.qid == "Q42"
    assert summary.label == "Douglas Adams"
    assert summary.description == "English author"
    assert summary.source == "wikidata"
    assert calls and "Q42.json" in calls[0][0]


def test_fetch_wikidata_summary_returns_none_on_http_error() -> None:
    """A 404/429/network error must not raise — return None so the caller
    can skip + continue with the next candidate.

    Sabotage-proof: remove the ``except Exception`` block → test raises
    instead of returning None.
    """

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse({}, status=404)

    assert fetch_wikidata_summary("Q42", http_get=fake_get) is None


def test_fetch_wikidata_summary_returns_none_when_payload_missing_qid() -> None:
    """Wikidata returned a 200 but the entity dict for our qid isn't
    present (deleted entity, redirect, malformed payload). Return None.

    Sabotage-proof: change the parser to default ``entities.get(qid, {})``
    → test fails because we'd return an EntitySummary with empty label +
    description, which the contract says should be None.
    """

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse({"entities": {"Q999": {"labels": {"en": {"value": "Other"}}}}})

    assert fetch_wikidata_summary("Q42", http_get=fake_get) is None


def test_fetch_wikidata_summary_returns_none_for_empty_qid() -> None:
    """Defensive guard: an empty qid never hits the network."""
    calls: list[Any] = []

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse({})

    assert fetch_wikidata_summary("", http_get=fake_get) is None
    assert not calls, "empty qid should not hit the network"


# ---------------------------------------------------------------------------
# enrich_entity
# ---------------------------------------------------------------------------


def test_enrich_entity_skips_when_no_wikidata_qid() -> None:
    """An entity without ``wikidata_qid`` returns skipped_reason='no_qid'
    and never touches the network — operators must run ``validate
    --update`` first.

    Sabotage-proof: remove the ``if not qid: return ... skipped_reason``
    guard → fetch_wikidata_summary is called with empty qid (test sees
    the fetch_count > 0).
    """
    fetch_count = {"n": 0}

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        fetch_count["n"] += 1
        return _FakeResponse({})

    neo4j = _RecordingNeo4j(lookup_rows=[{"qid": "", "summary": ""}])
    result = enrich_entity("Acme Corp", neo4j, http_get=fake_get)

    assert result.skipped_reason == "no_qid"
    assert result.updated is False
    assert fetch_count["n"] == 0


def test_enrich_entity_writes_summary_via_set_cypher() -> None:
    """The fix's load-bearing behaviour: a SET cypher writes the description
    to the n.summary property in the exact shape the audit + health check
    query for.

    Sabotage-proof: change the SET clause to ``SET n.notes = $summary``
    → test fails at the write-query inspection.
    """

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(_wikidata_payload("Q123", "Acme Corp", "Fictional corporation"))

    neo4j = _RecordingNeo4j(lookup_rows=[{"qid": "Q123", "summary": ""}])
    result = enrich_entity("Acme Corp", neo4j, http_get=fake_get)

    assert result.updated is True
    assert result.qid == "Q123"
    assert result.description == "Fictional corporation"
    assert result.error == ""

    write_calls = [(q, p) for q, p, _w in neo4j.cypher_calls if "SET n.summary" in q]
    assert len(write_calls) == 1, "exactly one SET n.summary call expected"
    write_q, write_p = write_calls[0]
    assert "MATCH (n {name: $name})" in write_q
    assert "SET n.summary = $summary" in write_q
    assert write_p["name"] == "Acme Corp"
    assert write_p["summary"] == "Fictional corporation"


def test_enrich_entity_is_idempotent_on_populated_summary() -> None:
    """A repeat call against an entity that already has a summary skips
    the fetch + write — no API burn, no Neo4j churn.

    Sabotage-proof: remove the ``if existing_summary and not overwrite``
    guard → the write cypher fires every run (test sees a SET call when
    none should occur).
    """
    fetch_count = {"n": 0}

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        fetch_count["n"] += 1
        return _FakeResponse(_wikidata_payload("Q123", "Acme Corp", "Fictional corporation"))

    neo4j = _RecordingNeo4j(lookup_rows=[{"qid": "Q123", "summary": "existing"}])
    result = enrich_entity("Acme Corp", neo4j, http_get=fake_get)

    assert result.skipped_reason == "already_summary"
    assert result.updated is False
    assert fetch_count["n"] == 0, "must not fetch when summary already populated"
    write_calls = [q for q, _, _w in neo4j.cypher_calls if "SET n.summary" in q]
    assert not write_calls, "no SET cypher should fire on idempotency skip"


def test_enrich_entity_overwrite_replaces_existing_summary() -> None:
    """``overwrite=True`` bypasses the idempotency guard so operators can
    refresh stale summaries.

    Sabotage-proof: change ``overwrite=False`` default to ``overwrite=True``
    → the idempotency test above fires the write, breaking that test.
    """

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(_wikidata_payload("Q123", "Acme Corp", "Updated description"))

    neo4j = _RecordingNeo4j(lookup_rows=[{"qid": "Q123", "summary": "stale"}])
    result = enrich_entity("Acme Corp", neo4j, overwrite=True, http_get=fake_get)

    assert result.updated is True
    assert result.description == "Updated description"


def test_enrich_entity_no_description_skips_write() -> None:
    """Wikidata returned a valid item with a label but no description in
    English — skipped_reason='no_description', no SET cypher.

    Sabotage-proof: remove the ``if not summary.description`` guard →
    the test fails because n.summary gets SET to an empty string,
    creating noise for the audit.
    """

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        payload = {
            "entities": {
                "Q123": {
                    "labels": {"en": {"language": "en", "value": "Acme Corp"}},
                    "descriptions": {},
                }
            }
        }
        return _FakeResponse(payload)

    neo4j = _RecordingNeo4j(lookup_rows=[{"qid": "Q123", "summary": ""}])
    result = enrich_entity("Acme Corp", neo4j, http_get=fake_get)

    assert result.skipped_reason == "no_description"
    assert result.updated is False
    write_calls = [q for q, _, _w in neo4j.cypher_calls if "SET n.summary" in q]
    assert not write_calls


# ---------------------------------------------------------------------------
# enrich_all_missing
# ---------------------------------------------------------------------------


class _BatchNeo4j:
    """Neo4j fake that returns different rows for the candidate query vs
    per-entity lookups — minimal shape needed for enrich_all_missing.
    Accepts the ``write=`` kwarg added in #416.
    """

    def __init__(self, candidates: list[str], per_entity: dict[str, dict[str, Any]]) -> None:
        self._candidates = candidates
        self._per_entity = per_entity
        self.cypher_calls: list[tuple[str, dict[str, Any], bool]] = []

    def cypher(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        *,
        write: bool = False,
    ) -> list[dict[str, Any]]:
        self.cypher_calls.append((query, dict(params or {}), write))
        if "MATCH (n {name: $name}) SET" in query:
            # SET RETURN clause — empty if name not in per_entity
            name = (params or {}).get("name", "")
            return [{"name": name}] if name in self._per_entity else []
        if "MATCH (n) " in query and "WHERE n.wikidata_qid" in query:
            return [{"name": n} for n in self._candidates]
        if "MATCH (n {name: $name}) RETURN" in query:
            return [self._per_entity.get((params or {}).get("name", ""), {})]
        return []


def test_enrich_all_missing_iterates_candidates_and_buckets_outcomes() -> None:
    """The batch driver enriches each candidate and bucketises results
    into updated / skipped / failed for the operator summary.

    Sabotage-proof: drop one of the buckets (e.g. remove ``if result.error:
    failed_n += 1``) → the totals don't sum to requested.
    """

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        if "Q1" in url:
            return _FakeResponse(_wikidata_payload("Q1", "Org One", "First org"))
        if "Q3" in url:
            return _FakeResponse({}, status=500)  # failed
        return _FakeResponse({"entities": {}})  # missing → returns None → failed

    neo4j = _BatchNeo4j(
        candidates=["Org One", "Org Two", "Org Three"],
        per_entity={
            "Org One": {"qid": "Q1", "summary": ""},
            "Org Two": {"qid": "Q2", "summary": "already here"},
            "Org Three": {"qid": "Q3", "summary": ""},
        },
    )
    batch = enrich_all_missing(neo4j, limit=10, http_get=fake_get)

    assert batch.requested == 3
    assert batch.updated == 1
    assert batch.skipped == 1
    assert batch.failed == 1
    assert batch.updated + batch.skipped + batch.failed == batch.requested


def test_enrich_all_missing_rejects_zero_limit() -> None:
    """Defensive: limit=0 returns an error instead of running unbounded.

    Sabotage-proof: remove the ``if limit < 1`` guard → the LIMIT 0 query
    silently returns no rows, hiding misconfiguration.
    """
    neo4j = _BatchNeo4j(candidates=[], per_entity={})
    batch = enrich_all_missing(neo4j, limit=0)
    assert "limit must be >= 1" in batch.error
    assert batch.requested == 0


def test_enrich_entity_set_cypher_uses_write_session() -> None:
    """#416 — the SET cypher must be invoked with ``write=True`` so the
    Neo4j driver opens a WRITE-mode session. Without this, every SET on a
    READ session silently fails with ``Neo.ClientError.Statement.AccessMode``,
    the enricher's ``cypher()`` call swallows the exception and returns [],
    and the wrapper falsely reports ``updated=True`` because it didn't
    check the return value.

    Sabotage-proof: remove the ``write=True`` kwarg in
    ``enrich_entity`` → this test fails because the recorded ``write``
    flag is False on the SET call. (Verified locally before commit.)
    """

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(_wikidata_payload("Q123", "Acme Corp", "Fictional corporation"))

    neo4j = _RecordingNeo4j(lookup_rows=[{"qid": "Q123", "summary": ""}])
    result = enrich_entity("Acme Corp", neo4j, http_get=fake_get)

    assert result.updated is True
    set_calls = [(q, p, w) for q, p, w in neo4j.cypher_calls if "SET n.summary" in q]
    assert len(set_calls) == 1
    _q, _p, write_flag = set_calls[0]
    assert write_flag is True, (
        "SET n.summary cypher must be invoked with write=True (#416); "
        "READ-mode session rejects writes with AccessMode error."
    )


def test_enrich_entity_returns_error_when_set_match_returns_zero_rows() -> None:
    """#416 — the SET cypher must include ``RETURN n.name`` and the
    enricher must check the result; a 0-row return means the MATCH
    didn't land (entity was deleted between lookup and write, or the
    write was rejected silently). Without this check the enricher
    falsely reports updated=True for writes that never happened.

    Sabotage-proof: remove the ``if not rows: return ... error`` guard
    in ``enrich_entity`` → this test fails because the result reports
    updated=True instead of the expected error.
    """

    class _NoMatchNeo4j(_RecordingNeo4j):
        """Lookup returns the qid; SET returns 0 rows (no MATCH)."""

        def cypher(
            self,
            query: str,
            params: dict[str, Any] | None = None,
            *,
            write: bool = False,
        ) -> list[dict[str, Any]]:
            self.cypher_calls.append((query, dict(params or {}), write))
            if "MATCH (n {name: $name}) RETURN" in query:
                return [{"qid": "Q123", "summary": ""}]
            if "MATCH (n {name: $name}) SET" in query:
                return []  # zero-row return — MATCH didn't land
            return []

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(_wikidata_payload("Q123", "Vanished Corp", "Was here"))

    neo4j = _NoMatchNeo4j()
    result = enrich_entity("Vanished Corp", neo4j, http_get=fake_get)

    assert result.updated is False
    assert result.error == "neo4j_write_no_match"


def test_entity_summary_is_frozen_dataclass() -> None:
    """F42 + immutability: EntitySummary instances cannot be mutated post-construction.

    Sabotage-proof: drop ``frozen=True`` from the dataclass → setattr
    succeeds, this test fails.
    """
    from dataclasses import FrozenInstanceError

    s = EntitySummary(qid="Q1", label="X", description="Y")
    with pytest.raises(FrozenInstanceError):
        s.qid = "Q2"  # type: ignore[misc]  # frozen dataclass assignment — mypy flags but FrozenInstanceError is the runtime contract under test
