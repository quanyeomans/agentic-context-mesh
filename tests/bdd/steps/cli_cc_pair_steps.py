"""Step definitions for cli_cc_pair.feature (Wave D — operator cc_pair CLI).

Drives the ``kairix cc-pair`` CLI subcommand through its public
adapter ``kairix.core.connectors.cc_pair_cli.main`` — F46-compliant
(call-graph depth ≤ 2 from a CLI entry point).

F1-clean: no @patch on kairix internals.
F2-clean: no env-var manipulation.
F4-clean: the db_provider DI seam means the CLI never reads
KAIRIX_DB_PATH inside the step impls.
"""

from __future__ import annotations

import io
import sqlite3
from contextlib import closing, redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.core.connectors.cc_pair import create_cc_pair, transition_cc_pair
from kairix.core.connectors.cc_pair_cli import main as cc_pair_main
from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.bdd


@dataclass
class _CCPairCtx:
    """Per-scenario state — no module-level globals (F2 hygiene)."""

    db_path: Path | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    connector_id: int | None = None
    cc_pair_id: int | None = None
    extra: dict[str, str] = field(default_factory=dict)


@pytest.fixture
def cc_pair_ctx(tmp_path: Path) -> _CCPairCtx:
    return _CCPairCtx(db_path=tmp_path / "kairix.sqlite")


def _open_db(ctx: _CCPairCtx) -> sqlite3.Connection:
    assert ctx.db_path is not None
    return sqlite3.connect(str(ctx.db_path))


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


@given("a fresh kairix sqlite database with the topology schema applied")
def _fresh_db(cc_pair_ctx: _CCPairCtx) -> None:
    with closing(_open_db(cc_pair_ctx)) as db:
        create_schema(db, dims=4)


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse('the operator has registered the connector "{connector_name}"'))
def _register_connector(cc_pair_ctx: _CCPairCtx, connector_name: str) -> None:
    with closing(_open_db(cc_pair_ctx)) as db:
        cur = db.execute(
            "INSERT INTO topology_connectors "
            "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
            "VALUES ('obsidian', ?, '{}', 'internal', '2026-05-23T00:00:00Z', '2026-05-23T00:00:00Z')",
            (connector_name,),
        )
        db.commit()
        assert cur.lastrowid is not None
        cc_pair_ctx.connector_id = int(cur.lastrowid)


@given(parsers.parse('the operator created a cc_pair "{name}" at status SCHEDULED'))
def _create_cc_pair_scheduled(cc_pair_ctx: _CCPairCtx, name: str) -> None:
    assert cc_pair_ctx.connector_id is not None, "Given the connector step must run first"
    with closing(_open_db(cc_pair_ctx)) as db:
        pair = create_cc_pair(db, connector_id=cc_pair_ctx.connector_id, credential_id=None, name=name)
        db.commit()
        cc_pair_ctx.cc_pair_id = pair.id


@given("the operator advanced that cc_pair through INITIAL_INDEXING and ACTIVE and PAUSED")
def _advance_to_paused(cc_pair_ctx: _CCPairCtx) -> None:
    assert cc_pair_ctx.cc_pair_id is not None
    with closing(_open_db(cc_pair_ctx)) as db:
        transition_cc_pair(db, cc_pair_ctx.cc_pair_id, "INITIAL_INDEXING")
        transition_cc_pair(db, cc_pair_ctx.cc_pair_id, "ACTIVE")
        transition_cc_pair(db, cc_pair_ctx.cc_pair_id, "PAUSED")
        db.commit()


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


def _run_cli(cc_pair_ctx: _CCPairCtx, argv: list[str]) -> None:
    """Invoke ``cc_pair_cli.main`` with the test ctx's db path injected
    through the ``db_provider`` DI seam — no env-var monkeypatching.
    """
    out, err = io.StringIO(), io.StringIO()

    def _provider(_explicit: Path | None) -> sqlite3.Connection:
        return _open_db(cc_pair_ctx)

    with redirect_stdout(out), redirect_stderr(err):
        cc_pair_ctx.exit_code = cc_pair_main(argv, db_provider=_provider)
    cc_pair_ctx.stdout = out.getvalue()
    cc_pair_ctx.stderr = err.getvalue()


@when("the operator runs the kairix cc-pair list command")
def _run_list(cc_pair_ctx: _CCPairCtx) -> None:
    _run_cli(cc_pair_ctx, ["list"])


@when(parsers.parse('the operator runs the kairix cc-pair create command with name "{name}"'))
def _run_create(cc_pair_ctx: _CCPairCtx, name: str) -> None:
    assert cc_pair_ctx.connector_id is not None
    _run_cli(
        cc_pair_ctx,
        ["create", "--connector-id", str(cc_pair_ctx.connector_id), "--name", name],
    )


@when("the operator runs the kairix cc-pair pause command for that cc_pair")
def _run_pause(cc_pair_ctx: _CCPairCtx) -> None:
    assert cc_pair_ctx.cc_pair_id is not None
    _run_cli(cc_pair_ctx, ["pause", "--id", str(cc_pair_ctx.cc_pair_id)])


@when("the operator runs the kairix cc-pair resume command for that cc_pair")
def _run_resume(cc_pair_ctx: _CCPairCtx) -> None:
    assert cc_pair_ctx.cc_pair_id is not None
    _run_cli(cc_pair_ctx, ["resume", "--id", str(cc_pair_ctx.cc_pair_id)])


@when("the operator runs the kairix cc-pair delete command for that cc_pair")
def _run_delete(cc_pair_ctx: _CCPairCtx) -> None:
    assert cc_pair_ctx.cc_pair_id is not None
    _run_cli(cc_pair_ctx, ["delete", "--id", str(cc_pair_ctx.cc_pair_id)])


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then(parsers.parse('the cc-pair output contains "{needle}"'))
def _stdout_contains(cc_pair_ctx: _CCPairCtx, needle: str) -> None:
    assert needle in cc_pair_ctx.stdout, f"expected {needle!r} in stdout; got: {cc_pair_ctx.stdout!r}"


@then(parsers.parse('the cc-pair stderr contains "{needle}"'))
def _stderr_contains(cc_pair_ctx: _CCPairCtx, needle: str) -> None:
    assert needle in cc_pair_ctx.stderr, f"expected {needle!r} in stderr; got: {cc_pair_ctx.stderr!r}"


@then("the cc-pair command exits with code 0")
def _exit_zero(cc_pair_ctx: _CCPairCtx) -> None:
    assert cc_pair_ctx.exit_code == 0, f"expected exit 0; got {cc_pair_ctx.exit_code}, stderr={cc_pair_ctx.stderr!r}"


@then("the cc-pair command exits with a non-zero code")
def _exit_nonzero(cc_pair_ctx: _CCPairCtx) -> None:
    assert cc_pair_ctx.exit_code != 0, f"expected non-zero exit; got 0 (stdout={cc_pair_ctx.stdout!r})"
