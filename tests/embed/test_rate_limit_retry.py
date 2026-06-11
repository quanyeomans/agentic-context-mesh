"""#475 — bounded 429 retry around the embed provider call.

Verified failure this pins: a manual ``kairix embed`` died with a raw
``openai.RateLimitError`` traceback when a 429 escaped the SDK's own
retries. Contract of ``with_rate_limit_retry`` (public surface in
``kairix.core.embed.embed``):

  * success on attempt 1 → exactly one call, zero sleeps
  * 429 with Retry-After → wait exactly that many seconds, then retry
  * 429 without Retry-After → exponential backoff capped at 60s
  * exhaustion after max attempts → RuntimeError with an F21-shaped
    remediation message (fix:/next:/run:), chained to the 429
  * wired through ``run_embed``: a permanently-429ing provider marks
    chunks failed and the run COMPLETES — it never dies

The sleeper is injected through the explicit ``sleep_fn`` seam
(production: ``EmbedDependencies.rate_limit_sleep``) — no
``time.sleep`` patching (F1-clean).
"""

from __future__ import annotations

import sqlite3

import httpx
import openai
import pytest

from kairix.core.embed.deps import EmbedDependencies
from kairix.core.embed.embed import retry_after_seconds, run_embed, with_rate_limit_retry

pytestmark = pytest.mark.unit


def _make_429(retry_after: str | None = None) -> openai.RateLimitError:
    """Construct a real SDK RateLimitError carrying an httpx 429 response."""
    request = httpx.Request("POST", "https://ep.example/embeddings")
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    response = httpx.Response(429, headers=headers, request=request)
    return openai.RateLimitError("Rate limit exceeded", response=response, body=None)


class _FlakyEmbed:
    """Embed-batch fake that raises 429 for the first ``failures`` calls."""

    def __init__(self, failures: int, retry_after: str | None = None) -> None:
        self._failures = failures
        self._retry_after = retry_after
        self.calls = 0

    def __call__(self, texts: list[str], *_a: object, **_kw: object) -> list[list[float]]:
        self.calls += 1
        if self.calls <= self._failures:
            raise _make_429(self._retry_after)
        return [[0.1] * 1536 for _ in texts]


def test_success_first_attempt_calls_once_and_never_sleeps() -> None:
    fake = _FlakyEmbed(failures=0)
    sleeps: list[float] = []

    wrapped = with_rate_limit_retry(fake, sleep_fn=sleeps.append)
    out = wrapped(["text-a", "text-b"], "key", "ep", "deploy", 1536)

    assert len(out) == 2
    assert fake.calls == 1
    assert sleeps == []


def test_retry_after_header_drives_the_wait() -> None:
    """Two 429s carrying ``Retry-After: 7`` → two 7-second waits, then success.

    Sabotage proof: dropping the ``retry_after_seconds`` branch in
    ``with_rate_limit_retry`` falls back to exponential (2.0, 4.0) and
    the sleeps assertion fails.
    """
    fake = _FlakyEmbed(failures=2, retry_after="7")
    sleeps: list[float] = []

    wrapped = with_rate_limit_retry(fake, sleep_fn=sleeps.append)
    out = wrapped(["text"], "key", "ep", "deploy", 1536)

    assert len(out) == 1
    assert fake.calls == 3
    assert sleeps == [7.0, 7.0]


def test_missing_retry_after_uses_exponential_backoff_capped() -> None:
    """No header → 2^attempt seconds, capped at 60: 2, 4, 8, 16 for 5 attempts."""
    fake = _FlakyEmbed(failures=4, retry_after=None)
    sleeps: list[float] = []

    wrapped = with_rate_limit_retry(fake, sleep_fn=sleeps.append)
    wrapped(["text"], "key", "ep", "deploy", 1536)

    assert fake.calls == 5
    assert sleeps == [2.0, 4.0, 8.0, 16.0]


def test_huge_retry_after_is_capped_at_max_backoff() -> None:
    """``Retry-After: 300`` waits the 60s cap, not five minutes."""
    fake = _FlakyEmbed(failures=1, retry_after="300")
    sleeps: list[float] = []

    wrapped = with_rate_limit_retry(fake, sleep_fn=sleeps.append)
    wrapped(["text"], "key", "ep", "deploy", 1536)

    assert sleeps == [60.0]


def test_exhaustion_raises_runtime_error_with_f21_affordance() -> None:
    """Persistent 429 → RuntimeError (catchable by the batch loop) with
    fix:/next:/run: remediation, chained to the underlying RateLimitError.

    Sabotage proof: raising the bare RateLimitError on exhaustion (the
    pre-#475 behaviour) fails the ``pytest.raises(RuntimeError)`` match.
    """
    fake = _FlakyEmbed(failures=99)
    sleeps: list[float] = []

    wrapped = with_rate_limit_retry(fake, max_attempts=3, sleep_fn=sleeps.append)
    with pytest.raises(RuntimeError) as info:
        wrapped(["text"], "key", "ep", "deploy", 1536)

    msg = str(info.value)
    assert "429" in msg
    assert "fix:" in msg and "next:" in msg and "run: kairix embed" in msg
    assert isinstance(info.value.__cause__, openai.RateLimitError)
    assert fake.calls == 3
    assert len(sleeps) == 2, "no sleep after the final attempt"


def test_retry_after_seconds_handles_missing_and_malformed_headers() -> None:
    assert retry_after_seconds(_make_429(None)) is None
    assert retry_after_seconds(_make_429("not-a-number")) is None
    assert retry_after_seconds(_make_429("12.5")) == 12.5
    assert retry_after_seconds(_make_429("-3")) == 0.0
    assert retry_after_seconds(RuntimeError("no response attached")) is None


# ---------------------------------------------------------------------------
# Wiring through run_embed — the run must never die on a 429.
# ---------------------------------------------------------------------------


def _seed_one_doc(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE documents (hash TEXT PRIMARY KEY, path TEXT, active INTEGER DEFAULT 1, source_modified_at TEXT)"
    )
    db.execute("CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT)")
    db.execute(
        "CREATE TABLE content_vectors"
        " (hash TEXT, seq INTEGER, pos INTEGER, model TEXT, embedded_at INTEGER, chunk_date TEXT)"
    )
    db.execute("INSERT INTO content (hash, doc) VALUES ('h1', ?)", ("Document body text. " * 5,))
    db.execute("INSERT INTO documents (hash, path, active) VALUES ('h1', 'doc.md', 1)")
    db.commit()


def test_run_embed_survives_persistent_429_and_reports_failed_chunks() -> None:
    """A provider that 429s on every call: run_embed completes, reports the
    chunk as failed (retryable next run), and the backoff seam was used.

    Sabotage proof: unwrapping ``deps.embed_batch`` in ``run_embed``
    (passing it to the loop directly) lets the RateLimitError escape
    ``_embed_batch_only``'s except clause and this test errors instead
    of returning a result.
    """
    db = sqlite3.connect(":memory:")
    _seed_one_doc(db)
    sleeps: list[float] = []

    def _always_429(_texts: list[str], *_a: object, **_kw: object) -> list[list[float]]:
        raise _make_429("1")

    deps = EmbedDependencies(
        get_azure_config=lambda: ("key", "https://ep.example", "deploy"),
        preflight_check=lambda *_a, **_kw: 1536,
        migrate_content_vectors=lambda _db: None,
        open_usearch_index=lambda: None,
        get_document_root=lambda: None,
        embed_batch=_always_429,
        get_reflib_index_mode=lambda: "eager",
        rate_limit_sleep=sleeps.append,
    )

    result = run_embed(db, batch_size=10, deps=deps)

    assert result["failed"] == 1
    assert result["embedded"] == 0
    assert sleeps == [1.0] * 4, "5 attempts → 4 Retry-After waits through the injected seam"


def test_run_embed_recovers_when_429_clears_mid_run() -> None:
    """Transient 429 (clears after two attempts) → the chunk embeds fine."""
    db = sqlite3.connect(":memory:")
    _seed_one_doc(db)
    fake = _FlakyEmbed(failures=2, retry_after="2")
    sleeps: list[float] = []

    deps = EmbedDependencies(
        get_azure_config=lambda: ("key", "https://ep.example", "deploy"),
        preflight_check=lambda *_a, **_kw: 1536,
        migrate_content_vectors=lambda _db: None,
        open_usearch_index=lambda: None,
        get_document_root=lambda: None,
        embed_batch=fake,
        get_reflib_index_mode=lambda: "eager",
        rate_limit_sleep=sleeps.append,
    )

    result = run_embed(db, batch_size=10, deps=deps)

    assert result["embedded"] == 1
    assert result["failed"] == 0
    assert sleeps == [2.0, 2.0]
