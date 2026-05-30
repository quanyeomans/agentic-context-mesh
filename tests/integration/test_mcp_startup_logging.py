"""Integration test for KFEAT-020 Part 3 — structured MCP startup logs.

The HTTP-transport branch of ``kairix mcp serve`` must emit three
structured log events on the dedicated ``kairix.mcp.startup`` logger so
operators can pivot on container-restart frequency in their log
analytics layer:

  - ``event=mcp_process_started`` — fired once before warm-up. Carries
    ``pid`` / ``host`` / ``port`` / ``python_version`` / ``kairix_version``
    / ``previous_warm_age_s`` so operators can answer "was the previous
    process warm when it died?".
  - ``event=mcp_warm_started`` — fired when warm returns ``ready=True``.
    Carries ``pid`` / ``elapsed_ms``.
  - ``event=mcp_warm_failed`` — fired when warm returns ``ready=False``.
    Carries ``pid`` / ``warm_result`` (full envelope as JSON string).

All three are emitted as ``logger.info(...)`` calls in grep-friendly
``event=<name> key=value`` shape so plain ``docker logs | grep`` works
without a log shipper.

Sabotage-proof (executed): removed the ``pid=`` field from
``_format_event``'s output in ``kairix/agents/mcp/cli.py`` — every
``assert "pid=" in record.getMessage()`` assertion below fired
``AssertionError``. Restored. Documented in commit body.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any

import pytest

from kairix.agents.mcp.cli import McpCliDeps
from kairix.agents.mcp.cli import main as mcp_main

pytestmark = pytest.mark.integration

_STARTUP_LOGGER = "kairix.mcp.startup"


# ---------------------------------------------------------------------------
# Fakes — Protocol-shape minimum that drives the HTTP-transport branch
# without binding a port.
# ---------------------------------------------------------------------------


class _FakeMcpServer:
    """Minimum shape build_mcp_app needs: streamable+sse apps + settings."""

    def __init__(self) -> None:
        import types as _types

        self.settings = _types.SimpleNamespace(
            json_response=False,
            stateless_http=False,
            streamable_http_path="/mcp",
        )

    def streamable_http_app(self) -> Any:
        from starlette.applications import Starlette

        return Starlette(routes=[])

    def sse_app(self, *, mount_path: str = "/sse") -> Any:
        from starlette.applications import Starlette

        return Starlette(routes=[])


@dataclass
class _FakeUvicornRunner:
    """Records the (app, host, port) invocation; never actually serves."""

    calls: list[tuple[tuple, dict]] = field(default_factory=list)

    def __call__(self, *a: Any, **kw: Any) -> None:
        self.calls.append((a, kw))


def _build_deps(*, warm_result: dict[str, Any], warm_flag_path: Any) -> McpCliDeps:
    """Construct an McpCliDeps that drives the HTTP branch without binding.

    ``warm_flag_path`` is the per-test tmp path the cli reads the
    previous-warm-flag mtime from — passed via the
    ``warm_flag_path_fn`` DI seam on McpCliDeps so F2 (no
    ``monkeypatch.setenv("KAIRIX_WARM_FLAG_PATH", ...)``) stays clean.
    """

    def _fake_build_server(**_kwargs: Any) -> _FakeMcpServer:
        return _FakeMcpServer()

    return McpCliDeps(
        build_server_factory=lambda: _fake_build_server,
        uvicorn_runner_factory=lambda: _FakeUvicornRunner(),
        warm_retrieval_stack_fn=lambda: warm_result,
        warm_flag_path_fn=lambda: warm_flag_path,
    )


def _records_with_event(records: list[logging.LogRecord], event_name: str) -> list[logging.LogRecord]:
    """Return startup-logger records whose message carries ``event=<name>``."""
    needle = f"event={event_name}"
    return [r for r in records if r.name == _STARTUP_LOGGER and needle in r.getMessage()]


def _drive_serve_http(
    caplog: pytest.LogCaptureFixture,
    deps: McpCliDeps,
    monkeypatch: pytest.MonkeyPatch,
    port: int,
) -> None:
    """Invoke ``mcp serve --transport http`` once with the fake deps."""
    # Force --port through _resolve_port without the port-scan branch.
    monkeypatch.setattr(sys, "argv", ["kairix", "mcp", "serve", "--port", str(port)])
    with caplog.at_level(logging.INFO, logger=_STARTUP_LOGGER):
        mcp_main(["serve", "--transport", "http", "--port", str(port)], deps=deps)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_process_started_event_carries_all_required_fields(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """``mcp_process_started`` carries pid, host, port, python_version,
    kairix_version, and previous_warm_age_s.

    Sabotage-proof: drop ``"pid"`` from the dict passed to
    ``_format_event`` in ``_emit_process_started`` — this assertion's
    ``"pid=" in msg`` check fires. Executed; restored.
    """
    deps = _build_deps(
        warm_result={"ready": True, "elapsed_ms": 42},
        warm_flag_path=tmp_path / "warm.flag",
    )
    _drive_serve_http(caplog, deps, monkeypatch, port=18091)

    process_records = _records_with_event(caplog.records, "mcp_process_started")
    assert len(process_records) == 1, (
        f"expected exactly one mcp_process_started event; got {len(process_records)} "
        f"out of {len(caplog.records)} total records on {_STARTUP_LOGGER}"
    )
    msg = process_records[0].getMessage()
    for field_name in ("pid", "host", "port", "python_version", "kairix_version", "previous_warm_age_s"):
        assert f"{field_name}=" in msg, f"mcp_process_started missing {field_name!r}: {msg!r}"
    # pid is the real process id (no monkey-patching of os.getpid; just verify it's there)
    assert f"pid={os.getpid()}" in msg, f"expected real pid={os.getpid()} in {msg!r}"
    assert "host=127.0.0.1" in msg
    assert "port=18091" in msg
    # First start with no prior warm flag: previous_warm_age_s should be null.
    assert "previous_warm_age_s=null" in msg, f"first-start should report previous_warm_age_s=null; got {msg!r}"


def test_warm_started_event_emits_when_warm_ready(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """When warm_retrieval_stack_fn returns ready=True, the
    ``mcp_warm_started`` event fires with pid + elapsed_ms.

    Sabotage-proof: change the ``_emit_warm_outcome`` ready check from
    ``is True`` to ``is False`` — this test's expected-event assertion
    fires (no mcp_warm_started records). Executed; restored.
    """
    deps = _build_deps(
        warm_result={"ready": True, "elapsed_ms": 1337},
        warm_flag_path=tmp_path / "warm.flag",
    )
    _drive_serve_http(caplog, deps, monkeypatch, port=18092)

    warm_records = _records_with_event(caplog.records, "mcp_warm_started")
    assert len(warm_records) == 1, f"expected one mcp_warm_started event; got {len(warm_records)}"
    msg = warm_records[0].getMessage()
    assert f"pid={os.getpid()}" in msg, f"missing pid: {msg!r}"
    assert "elapsed_ms=1337" in msg, f"missing elapsed_ms=1337: {msg!r}"

    # ``mcp_warm_failed`` must NOT fire on the ready path.
    failed_records = _records_with_event(caplog.records, "mcp_warm_failed")
    assert failed_records == [], f"unexpected mcp_warm_failed on ready path: {failed_records}"


def _build_deps_sync_warm(*, warm_result: dict[str, Any], warm_flag_path: Any) -> McpCliDeps:
    """Like :func:`_build_deps` but runs the warm body synchronously so
    tests can assert on side effects of the warm thread immediately on
    return (rather than racing the daemon thread the production default
    spawns).
    """

    def _fake_build_server(**_kwargs: Any) -> _FakeMcpServer:
        return _FakeMcpServer()

    return McpCliDeps(
        build_server_factory=lambda: _fake_build_server,
        uvicorn_runner_factory=lambda: _FakeUvicornRunner(),
        warm_retrieval_stack_fn=lambda: warm_result,
        warm_flag_path_fn=lambda: warm_flag_path,
        warm_runner=lambda warm_body: warm_body(),  # sync, not daemon thread
    )


def test_warm_ready_writes_cross_process_flag_for_docker_healthcheck(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """GH #355 — when warm_retrieval_stack_fn returns ready=True, the MCP
    server MUST write the cross-process warm-flag file at
    ``deps.warm_flag_path_fn()`` so the docker healthcheck
    (``kairix onboard ready``, read by ``docker compose up --wait`` and
    by the in-image ``HEALTHCHECK`` directive) flips to healthy.

    Without this, the in-process readiness gate flips but the on-disk
    flag never appears, so the container shows ``unhealthy`` for the
    process lifetime even after warm-up actually completes in the
    application logs.

    Sabotage-proof: in ``_warm_and_mark_ready``, drop the
    ``mark_warm(flag_path=deps.warm_flag_path_fn())`` call — this test's
    ``flag_path.exists()`` assertion fires (the flag never gets
    written). Executed; restored.
    """
    flag_path = tmp_path / "warm.flag"
    assert not flag_path.exists(), "precondition: flag file must not exist before warm"

    deps = _build_deps_sync_warm(
        warm_result={"ready": True, "elapsed_ms": 42},
        warm_flag_path=flag_path,
    )
    _drive_serve_http(caplog, deps, monkeypatch, port=18094)

    assert flag_path.exists(), (
        f"GH #355 — mark_warm(flag_path=deps.warm_flag_path_fn()) must write the "
        f"cross-process warm-flag file so the docker healthcheck reads it; "
        f"flag_path={flag_path} did not appear after warm completed"
    )


def test_warm_not_ready_skips_cross_process_flag_write(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """GH #355 inverse — when warm_retrieval_stack_fn returns ready=False,
    the cross-process flag must NOT be written. Otherwise the docker
    healthcheck would flip to healthy on a broken warm-up.

    Sabotage-proof: move the mark_warm() call outside the
    ``if warm_result.get('ready') is True:`` branch — this test's
    ``not flag_path.exists()`` assertion fires. Executed; restored.
    """
    flag_path = tmp_path / "warm.flag"

    deps = _build_deps_sync_warm(
        warm_result={"ready": False, "elapsed_ms": 100, "status": "error"},
        warm_flag_path=flag_path,
    )
    _drive_serve_http(caplog, deps, monkeypatch, port=18095)

    assert not flag_path.exists(), (
        f"GH #355 — warm-flag must NOT be written when warm_result.ready=False; "
        f"flag_path={flag_path} appeared anyway, which would falsely flip the "
        f"docker healthcheck to healthy"
    )


def test_warm_failed_event_emits_when_warm_not_ready(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """When warm_retrieval_stack_fn returns ready=False, the
    ``mcp_warm_failed`` event fires with pid + warm_result envelope.

    Sabotage-proof: change ``_emit_warm_outcome``'s failed branch to
    skip the structured log call (early-return) — this test's
    ``len(failed_records) == 1`` assertion fires. Executed; restored.
    """
    failure_envelope = {
        "ready": False,
        "status": "error",
        "elapsed_ms": 250,
        "steps": [{"name": "build_search_pipeline", "ok": False, "error": "FakeError: simulated"}],
    }
    deps = _build_deps(
        warm_result=failure_envelope,
        warm_flag_path=tmp_path / "warm.flag",
    )
    _drive_serve_http(caplog, deps, monkeypatch, port=18093)

    failed_records = _records_with_event(caplog.records, "mcp_warm_failed")
    assert len(failed_records) == 1, f"expected one mcp_warm_failed event; got {len(failed_records)}"
    msg = failed_records[0].getMessage()
    assert f"pid={os.getpid()}" in msg, f"missing pid: {msg!r}"
    assert "warm_result=" in msg, f"missing warm_result envelope: {msg!r}"
    # warm_result is JSON-encoded since it's a dict — confirm the failure
    # marker reaches the log line so log analytics can pivot on it.
    assert "build_search_pipeline" in msg, f"warm_result envelope missing step detail: {msg!r}"

    # ``mcp_warm_started`` must NOT fire on the failed path.
    started_records = _records_with_event(caplog.records, "mcp_warm_started")
    assert started_records == [], f"unexpected mcp_warm_started on failed path: {started_records}"


def test_previous_warm_age_populated_from_existing_flag(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """When a warm-flag file already exists, ``previous_warm_age_s`` is
    populated with a numeric age — so operators can tell whether the
    just-killed previous process was warm at death.

    Sabotage-proof: change ``_previous_warm_age_seconds`` to always
    return ``None`` — this test's ``previous_warm_age_s=null`` rejection
    assertion fires. Executed; restored.
    """
    flag_path = tmp_path / "warm.flag"
    flag_path.write_text("")  # simulate a previous warm process
    deps = _build_deps(
        warm_result={"ready": True, "elapsed_ms": 1},
        warm_flag_path=flag_path,
    )
    _drive_serve_http(caplog, deps, monkeypatch, port=18094)

    process_records = _records_with_event(caplog.records, "mcp_process_started")
    assert len(process_records) == 1
    msg = process_records[0].getMessage()
    assert "previous_warm_age_s=null" not in msg, (
        f"previous_warm_age_s should be a number when flag exists; got {msg!r}"
    )
    # Numeric: the field value is a float >= 0; we just confirm shape.
    field_segment = next(part for part in msg.split() if part.startswith("previous_warm_age_s="))
    value = field_segment.split("=", 1)[1]
    assert float(value) >= 0.0, f"previous_warm_age_s value not parseable as float: {field_segment!r}"
