"""F30 outcome test — eval and prep converge within tolerance on a round-trip.

Plan B-parity D3 remediation pin. Before D3, ``kairix eval`` called
``fact_store.search(question, top_k=5)`` directly and the LLM judge
saw just fact triplets; ``kairix prep`` ran the full SearchPipeline +
L0 synthesis. Spike B1 measured the gap on 20 LoCoMo questions:

  * mean |eval - prep| score delta = 0.200  (HIGH > 0.15)
  * Pearson(eval, prep)            = 0.549  (HIGH < 0.6)
  * pass/fail disagreement         = 4/20 = 20.0%

After D3, ``kairix eval`` (default ``--via-prep``) and ``kairix prep``
share the same SearchPipeline. This test pins that convergence: the
score delta on a freshly-ingested single-fact corpus must stay within
the configured tolerance band (~0.30 to absorb LLM judge variance, but
tight enough that a regression to the legacy fact_store-only path would
push delta back to ~0.5+).

The test is the canonical F30 outcome shape for ``kairix eval``:
subprocess + realistic input + assertion on per-question score content
(not on returncode alone, not on internal fake call-counts).

Boundary chain exercised:

  subprocess([kairix, ingest-chat, <transcript>])
    → kairix/use_cases/ingest_chat.py
    → LLMFactExtractor (real LLM, KV-resolved creds)
    → SQLiteFactStore.add

  subprocess([kairix, eval, <suite>, --json])
    → kairix/use_cases/eval_suite.py
    → build_search_pipeline (auto-wires fact_retriever)
    → SuiteRunner._score_queries via SearchPipeline-mode branch
    → LLM judge over chunk + fact federation context
    → JSON SuiteResult on stdout

  subprocess([kairix, prep, <question>])
    → kairix/agents/prep/cli.py
    → run_prep → build_search_pipeline → SearchPipeline → synthesis
    → stdout

Then a comparison of the eval-side score (parsed from JSON ``rows[0]``)
against a rough prep-side competence proxy (does prep stdout contain
the expected fact's distinguishing token).

Run requirements (all four must be true; the test skips cleanly when
any are absent):

  * ``KAIRIX_E2E=1`` in the process env.
  * ``KAIRIX_KV_NAME`` resolvable to a Key Vault.
  * ``az`` CLI on PATH AND the operator logged in.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.e2e]


# ---------------------------------------------------------------------------
# Skip gates — duplicated locally so the file has no cross-test imports
# ---------------------------------------------------------------------------


def _e2e_enabled() -> bool:
    """``KAIRIX_E2E=1`` opts into real-credential e2e."""
    return os.environ.get("KAIRIX_E2E") == "1"


def _kv_name() -> str | None:
    """Return ``KAIRIX_KV_NAME`` when set, else None."""
    name = os.environ.get("KAIRIX_KV_NAME")
    return name or None


def _az_login_active() -> bool:
    """True when ``az account show`` returns 0 — operator is logged in."""
    if shutil.which("az") is None:
        return False
    try:
        proc = subprocess.run(
            ["az", "account", "show"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return proc.returncode == 0


def _missing_prereqs() -> str | None:
    """Return a human-readable skip reason, or None when everything is wired."""
    if not _e2e_enabled():
        return "KAIRIX_E2E=1 required (tests/conftest.py blocks real Azure calls by default)"
    if _kv_name() is None:
        return "KAIRIX_KV_NAME required — point at the Key Vault holding kairix-llm-* secrets"
    if not _az_login_active():
        return "az CLI not on PATH or operator not logged in (run `az login`)"
    return None


# ---------------------------------------------------------------------------
# Fixture data — a single-fact corpus
# ---------------------------------------------------------------------------


_TRANSCRIPT_TURNS = [
    {
        "role": "user",
        "speaker": "ops",
        "content": "Quick reminder: Bob is the CTO of Acme. He owns engineering.",
    },
    {
        "role": "assistant",
        "speaker": "agent",
        "content": "Got it — Bob (CTO, Acme) is the engineering owner.",
    },
]


def _write_transcript(tmp_path: Path) -> Path:
    """Write a 2-turn JSONL transcript naming Bob as CTO."""
    path = tmp_path / "session-001.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for turn in _TRANSCRIPT_TURNS:
            f.write(json.dumps(turn) + "\n")
    return path


def _write_eval_suite(suite_dir: Path, transcript: Path) -> None:
    """Lay out a single-question eval suite under ``suite_dir``.

    The suite references the same transcript that the ingest step
    indexed, and asks one single-hop question whose ground truth
    is 'CTO'. The eval CLI re-ingests the transcript into its own
    paths-pinned database — the production path's fact store and
    the eval suite's fact store are independent SQLite files under
    KAIRIX_DATA_DIR. (We don't try to share state across the two
    subprocesses; the assertion is on per-question score convergence,
    not on shared retrieval state.)
    """
    suite_dir.mkdir(parents=True, exist_ok=True)
    # Copy the transcript into the suite so the suite-runner ingests it.
    (suite_dir / "session-001.jsonl").write_bytes(transcript.read_bytes())
    (suite_dir / "ground-truth-queries.json").write_text(
        json.dumps(
            [
                {
                    "question": "What is Bob's role?",
                    "answer": "CTO",
                    "category": "single-hop",
                }
            ]
        ),
        encoding="utf-8",
    )


def _write_kairix_config(tmp_path: Path) -> Path:
    """Write the minimum ``kairix.config.yaml`` needed for the subprocess."""
    cfg = tmp_path / "kairix.config.yaml"
    cfg.write_text("provider: azure_foundry\n", encoding="utf-8")
    return cfg


def _kairix_subprocess_env(tmp_path: Path, cfg: Path) -> dict[str, str]:
    """Build the env dict for kairix subprocesses.

    Carries the operator's ``KAIRIX_KV_NAME`` + ``PATH`` + Azure CLI
    creds; pins data/config/document paths to ``tmp_path`` so the test
    can't see (or write to) the operator's real knowledge store.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "AZURE_CONFIG_DIR": os.environ.get("AZURE_CONFIG_DIR", str(Path.home() / ".azure")),
        "KAIRIX_KV_NAME": os.environ["KAIRIX_KV_NAME"],
        "KAIRIX_CONFIG_PATH": str(cfg),
        "KAIRIX_DATA_DIR": str(tmp_path / "data"),
        "KAIRIX_DOCUMENT_ROOT": str(tmp_path / "docs"),
        "KAIRIX_WORKSPACE_ROOT": str(tmp_path / "workspace"),
        # Trace on — surfaces D4 diagnostics if the eval-vs-prep delta
        # exposes a federation/snippet-filter regression. Quiet on success.
        "KAIRIX_TRACE": "1",
        "KAIRIX_E2E": "1",
    }
    return env


# ---------------------------------------------------------------------------
# F30 outcome test
# ---------------------------------------------------------------------------

# Loose enough to absorb LLM judge variance (the judge is graded 0.0-1.0
# in 0.5 increments by prompt design); tight enough that the pre-D3
# divergence (mean delta 0.200, with single-question disagreements
# hitting 1.0) would push delta back over the threshold.
_MAX_ABS_SCORE_DELTA: float = 0.30


def test_eval_and_prep_converge_within_tolerance_on_round_trip(tmp_path: Path) -> None:
    """End-to-end convergence: ingest a fact, score it via BOTH ``kairix eval``
    (--via-prep default) and ``kairix prep``; pin the score delta.

    A regression to the legacy fact_store-only eval path would push the
    delta back above 0.30 (Spike B1 measured 0.200 mean / 1.000 max on
    20 LoCoMo questions before D3). This is the F30 outcome shape — no
    internal fake call-counts; the assertion is on subprocess stdout
    content and per-question score arithmetic.

    Sabotage-proof: in ``SuiteRunner._retrieve_context``, drop the
    SearchPipeline branch so the eval CLI falls back to
    ``fact_store.search`` even in --via-prep mode. The eval-side judge
    then sees only fact triplets while the prep-side judge sees fact +
    chunk + synthesis context — score delta jumps past 0.30 and this
    assertion fails. Tested locally 2026-05-21 against develop HEAD
    52220741 (stream-D3 commit-3) — restoring the sabotage on a side
    branch reproduces the failure as a single delta-too-large
    assertion in <2 minutes.
    """
    skip_reason = _missing_prereqs()
    if skip_reason is not None:
        pytest.skip(reason=skip_reason)

    (tmp_path / "data").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "workspace").mkdir()

    transcript = _write_transcript(tmp_path)
    suite_dir = tmp_path / "eval-suite"
    _write_eval_suite(suite_dir, transcript)
    cfg = _write_kairix_config(tmp_path)
    env = _kairix_subprocess_env(tmp_path, cfg)

    # Step 1 — ingest the transcript via the production CLI subprocess.
    # This populates the production-path SQLite fact store; the prep
    # subprocess below reads from this database.
    ingest = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "ingest-chat", str(transcript)],
        capture_output=True,
        timeout=180,
        check=False,
        env=env,
    )
    ingest_stdout = ingest.stdout.decode("utf-8", errors="replace")
    ingest_stderr = ingest.stderr.decode("utf-8", errors="replace")
    assert ingest.returncode == 0, (
        f"ingest-chat exited {ingest.returncode}\nstdout:\n{ingest_stdout}\nstderr:\n{ingest_stderr}"
    )

    # Step 2 — score the question via ``kairix eval --json`` (default
    # ``--via-prep``). The suite-runner re-ingests the session into its
    # own scope (same KAIRIX_DATA_DIR, same SQLite file) and scores via
    # the SearchPipeline-mode branch.
    eval_proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "eval", str(suite_dir), "--json"],
        capture_output=True,
        timeout=240,
        check=False,
        env=env,
    )
    eval_stdout = eval_proc.stdout.decode("utf-8", errors="replace")
    eval_stderr = eval_proc.stderr.decode("utf-8", errors="replace")
    assert eval_proc.returncode == 0, (
        f"eval exited {eval_proc.returncode}\nstdout:\n{eval_stdout}\nstderr:\n{eval_stderr}"
    )

    try:
        eval_payload = json.loads(eval_stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"eval --json output was not valid JSON: {exc}\nstdout:\n{eval_stdout}\nstderr:\n{eval_stderr}")

    assert eval_payload["n_questions"] == 1, (
        f"expected 1-question eval, got {eval_payload['n_questions']} — "
        f"suite or runner shape changed; check ground-truth-queries.json fixture"
    )
    eval_row = eval_payload["rows"][0]
    eval_score = float(eval_row["score"])

    # Step 3 — score the same question via ``kairix prep``. Robust
    # surface assertion (does the answer surface mention 'CTO'?) maps to
    # a binary 1.0 / 0.0 prep-side score so the eval ↔ prep delta is
    # well-defined regardless of LLM phrasing.
    prep = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "prep", "What is Bob's role?"],
        capture_output=True,
        timeout=120,
        check=False,
        env=env,
    )
    prep_stdout = prep.stdout.decode("utf-8", errors="replace")
    prep_stderr = prep.stderr.decode("utf-8", errors="replace")
    assert prep.returncode == 0, f"prep exited {prep.returncode}\nstdout:\n{prep_stdout}\nstderr:\n{prep_stderr}"

    # Anti-template: the pre-D1 LoCoMo regression's signature failure mode.
    assert "No relevant content found" not in prep_stdout, (
        "prep emitted the anti-template — fact was retrieved but dropped before synthesis. "
        f"stdout:\n{prep_stdout}\nstderr:\n{prep_stderr}"
    )

    prep_lower = prep_stdout.lower()
    prep_matched = "cto" in prep_lower or "chief technology" in prep_lower
    prep_score = 1.0 if prep_matched else 0.0

    # Step 4 — pin the eval ↔ prep convergence.
    delta = abs(eval_score - prep_score)
    assert delta <= _MAX_ABS_SCORE_DELTA, (
        f"eval ↔ prep score delta {delta:.2f} exceeds tolerance "
        f"{_MAX_ABS_SCORE_DELTA:.2f} — D3 SearchPipeline routing may have "
        f"regressed.\n"
        f"  eval_score = {eval_score:.2f} (row: {eval_row})\n"
        f"  prep_score = {prep_score:.2f} (matched={prep_matched})\n"
        f"  prep stdout:\n{prep_stdout}\n"
        f"  eval stderr:\n{eval_stderr}\n"
        f"  prep stderr:\n{prep_stderr}"
    )

    # Step 5 — additional anti-regression pin: with D3 wired AND the
    # production-path facts in place, eval should pass-or-near-pass.
    # A 0.0 eval_score with prep_matched=True would mean federation
    # is firing in prep but NOT in eval — the exact regression D3
    # closes. Tolerate prep_matched=False (LLM phrasing variance) but
    # NOT eval=0.0 when prep matched cleanly.
    if prep_matched:
        assert eval_score >= 0.5, (
            f"prep surfaced the fact (CTO match) but eval scored {eval_score:.2f} — "
            f"federation must be firing on the prep side but not the eval side. "
            f"Check --via-prep default and SuiteRunner.search_pipeline wiring."
        )
