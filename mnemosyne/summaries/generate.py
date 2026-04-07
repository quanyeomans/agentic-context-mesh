"""
L0/L1 summary generation via gpt-4o-mini (Azure OpenAI).

L0: 1-2 sentence abstract (~100 tokens)
L1: structured overview (~500 tokens) — main topic, key points, status

Both functions raise on API failure. The batch helper (generate_summaries)
catches and logs failures per-file so callers always get partial results.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class SummaryResult:
    path: str  # Source file path
    l0: str  # 1-2 sentence abstract (~100 tokens)
    l1: str | None  # Structured overview (~500 tokens), None if not requested
    model: str  # Model used
    generated_at: str  # ISO timestamp
    tokens_used: int  # Total tokens consumed


# ---------------------------------------------------------------------------
# Prompt constants
# ---------------------------------------------------------------------------

_L0_SYSTEM = (
    "You are a precise document summariser. "
    "Summarise in 1-2 sentences (max 100 tokens). "
    "Be specific and factual — name the main topic, key decisions or actions, "
    "and the outcome or current state."
)

_L1_SYSTEM = (
    "You are a precise document summariser. "
    "Write a structured overview (max 500 tokens). Include:\n"
    "- Main topic (1 sentence)\n"
    "- Key points or decisions (bullet list, max 5)\n"
    "- Current status or outcome (1 sentence)\n"
    "Be specific: name tools, dates, people, and decisions where present."
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _first_n_words(text: str, n: int) -> str:
    """Return the first n whitespace-separated words of text."""
    words = text.split()
    return " ".join(words[:n])


def _call_chat(
    messages: list[dict],
    api_key: str,
    endpoint: str,
    deployment: str,
    max_tokens: int,
) -> tuple[str, int]:
    """
    POST to Azure OpenAI chat completions endpoint.

    Returns (content, total_tokens_used). Raises httpx.HTTPStatusError on non-2xx.
    """
    url = f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/chat/completions?api-version=2024-02-01"
    headers = {
        "api-key": api_key,
        "Content-Type": "application/json",
    }
    body = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }
    with httpx.Client(timeout=60) as client:
        resp = client.post(url, headers=headers, json=body)
        resp.raise_for_status()

    data = resp.json()
    content: str = data["choices"][0]["message"]["content"].strip()
    tokens_used: int = data.get("usage", {}).get("total_tokens", 0)
    return content, tokens_used


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_l0(
    path: str,
    content: str,
    api_key: str,
    endpoint: str,
    deployment: str = "gpt-4o-mini",
) -> str:
    """
    Generate L0 abstract for a document.

    Uses the first 800 words of content. Returns the abstract string.
    Raises on API failure.
    """
    truncated = _first_n_words(content, 800)
    messages = [
        {"role": "system", "content": _L0_SYSTEM},
        {"role": "user", "content": f"Document path: {path}\n\n{truncated}"},
    ]
    abstract, _ = _call_chat(messages, api_key, endpoint, deployment, max_tokens=150)
    return abstract


def generate_l1(
    path: str,
    content: str,
    api_key: str,
    endpoint: str,
    deployment: str = "gpt-4o-mini",
) -> str:
    """
    Generate L1 structured overview for a document.

    Uses the first 2000 words of content. Returns the overview string.
    Raises on API failure.
    """
    truncated = _first_n_words(content, 2000)
    messages = [
        {"role": "system", "content": _L1_SYSTEM},
        {"role": "user", "content": f"Document path: {path}\n\n{truncated}"},
    ]
    overview, _ = _call_chat(messages, api_key, endpoint, deployment, max_tokens=600)
    return overview


def generate_summaries(
    paths: list[str],
    api_key: str,
    endpoint: str,
    deployment: str = "gpt-4o-mini",
    include_l1: bool = False,
    batch_size: int = 10,
    sleep_ms: int = 100,
) -> list[SummaryResult]:
    """
    Batch generate summaries for a list of file paths.

    Reads file content, calls generate_l0 (and generate_l1 if include_l1).
    Failures on individual files are logged and skipped — never raised.
    Sleeps sleep_ms milliseconds between each file call for rate limiting.
    batch_size controls how many files are processed before each sleep.
    """
    results: list[SummaryResult] = []

    for i, path in enumerate(paths):
        # Sleep between batches (after the first batch_size items)
        if i > 0 and i % batch_size == 0:
            time.sleep(sleep_ms / 1000.0)

        try:
            file_path = Path(path)
            if not file_path.exists():
                logger.warning("generate_summaries: file not found — %s", path)
                continue

            content = file_path.read_text(encoding="utf-8", errors="replace")
            now = datetime.now(timezone.utc).isoformat()
            tokens_total = 0

            l0 = generate_l0(path, content, api_key, endpoint, deployment)
            # Rough token estimate for L0 if usage not tracked here
            tokens_total += len(l0.split()) * 4 // 3

            l1: str | None = None
            if include_l1:
                l1 = generate_l1(path, content, api_key, endpoint, deployment)
                tokens_total += len(l1.split()) * 4 // 3

            results.append(
                SummaryResult(
                    path=path,
                    l0=l0,
                    l1=l1,
                    model=deployment,
                    generated_at=now,
                    tokens_used=tokens_total,
                )
            )

        except Exception as exc:
            logger.error("generate_summaries: failed for %s — %s", path, exc)
            continue

        # Sleep between individual calls (within batch) as well
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)

    return results
