"""Unit tests for kairix.knowledge.summaries.cli.

The BDD suite covers ``--status`` + the empty-vault rejection paths. This
module drives the remaining branches:

  - ``_get_cred`` (delegates to secrets.get_secret).
  - ``_run_generate`` happy path (generate_summaries + write_summary).
  - ``main()`` --all / --stale / --path with credential resolution.
  - ``main()`` credential-failure branch.
  - ``main()`` default ``document_root`` / ``db_path`` resolution (when
    the test omits them).
  - ``__main__`` guard.
"""

from __future__ import annotations

import io
import runpy
import sqlite3
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

import pytest

import kairix.knowledge.summaries.cli as sum_cli

pytestmark = pytest.mark.unit


def _drive(args: list[str], **kw: Any) -> tuple[str, str, int]:
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            sum_cli.main(args, **kw)
    except SystemExit as e:
        code = int(e.code) if e.code is not None else 0
    return out.getvalue(), err.getvalue(), code


def _init_db(path: Path) -> None:
    from kairix.knowledge.summaries.staleness import init_summaries_db

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    init_summaries_db(conn)
    conn.close()


# ---------------------------------------------------------------------------
# run_generate path — drives the same behaviour through the public ``main()``
# surface (``--all`` → discovers vault → fetches creds → runs generate →
# persists each result). The previously-private ``_run_generate`` helper is
# exercised indirectly: per-result persistence + the "Done: N / N succeeded"
# summary land in stdout. F5-clean — no module-level private access.
# ---------------------------------------------------------------------------


def test_main_all_persists_each_result_and_prints_done_summary(monkeypatch, tmp_path: Path) -> None:
    """Drive the public surface: ``main(['--all'], ...)`` discovers two
    vault docs, fetches credentials, generates summaries, and persists
    each result.

    Sabotage: drop the ``for result in results: write_summary(result, db)``
    loop in ``_run_generate`` → ``written`` stays empty + this assertion
    fires.
    """
    from types import SimpleNamespace

    import kairix.credentials as cred_mod
    import kairix.knowledge.summaries.generate as gen_mod
    import kairix.knowledge.summaries.staleness as stale_mod

    doc_root = tmp_path / "vault"
    doc_root.mkdir()
    (doc_root / "a.md").write_text("# a", encoding="utf-8")
    (doc_root / "b.md").write_text("# b", encoding="utf-8")
    db_path = tmp_path / "summaries.db"
    _init_db(db_path)

    monkeypatch.setattr(cred_mod, "get_credentials", lambda kind: SimpleNamespace(api_key="k", endpoint="https://x"))
    written: list[Any] = []
    monkeypatch.setattr(stale_mod, "write_summary", lambda r, _db: written.append(r))
    monkeypatch.setattr(
        gen_mod,
        "generate_summaries",
        lambda *, paths, **kw: [{"path": p} for p in paths],
    )

    stdout, _stderr, code = _drive(["--all", "--include-l1"], document_root=doc_root, db_path=db_path)
    assert code == 0
    assert len(written) == 2
    assert "Done: 2 / 2 succeeded" in stdout


# ---------------------------------------------------------------------------
# main() branches
# ---------------------------------------------------------------------------


def test_main_status_branch_prints_counters(tmp_path: Path) -> None:
    db_path = tmp_path / "summaries.db"
    _init_db(db_path)
    doc_root = tmp_path / "vault"
    doc_root.mkdir()
    (doc_root / "a.md").write_text("# a", encoding="utf-8")

    stdout, _stderr, code = _drive(
        ["--status"],
        document_root=doc_root,
        db_path=db_path,
    )
    assert code == 0
    assert "Vault docs:" in stdout
    assert "With L0:" in stdout


def test_main_all_branch_generates_summaries(monkeypatch, tmp_path: Path) -> None:
    """--all enumerates vault, fetches creds, calls _run_generate."""
    from types import SimpleNamespace

    import kairix.credentials as cred_mod
    import kairix.knowledge.summaries.generate as gen_mod
    import kairix.knowledge.summaries.staleness as stale_mod

    doc_root = tmp_path / "vault"
    doc_root.mkdir()
    (doc_root / "a.md").write_text("# a", encoding="utf-8")
    db_path = tmp_path / "summaries.db"
    _init_db(db_path)

    monkeypatch.setattr(cred_mod, "get_credentials", lambda kind: SimpleNamespace(api_key="k", endpoint="e"))
    written: list[Any] = []
    monkeypatch.setattr(stale_mod, "write_summary", lambda r, _db: written.append(r))
    monkeypatch.setattr(
        gen_mod,
        "generate_summaries",
        lambda *, paths, **kw: [{"path": p} for p in paths],
    )

    stdout, _stderr, code = _drive(["--all"], document_root=doc_root, db_path=db_path)
    assert code == 0
    assert len(written) == 1
    assert "Done: 1 / 1" in stdout


def test_main_all_exits_1_when_vault_empty(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    import kairix.credentials as cred_mod

    monkeypatch.setattr(cred_mod, "get_credentials", lambda kind: SimpleNamespace(api_key="k", endpoint="e"))

    doc_root = tmp_path / "empty"
    doc_root.mkdir()
    db_path = tmp_path / "s.db"
    _init_db(db_path)
    _stdout, stderr, code = _drive(["--all"], document_root=doc_root, db_path=db_path)
    assert code == 1
    assert "No vault docs found" in stderr


def test_main_stale_branch_when_nothing_stale_returns_clean(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    import kairix.credentials as cred_mod
    import kairix.knowledge.summaries.staleness as stale_mod

    doc_root = tmp_path / "vault"
    doc_root.mkdir()
    (doc_root / "a.md").write_text("# a", encoding="utf-8")
    db_path = tmp_path / "s.db"
    _init_db(db_path)

    monkeypatch.setattr(cred_mod, "get_credentials", lambda kind: SimpleNamespace(api_key="k", endpoint="e"))
    monkeypatch.setattr(stale_mod, "get_stale_paths", lambda all_paths, db: [])

    stdout, _stderr, code = _drive(["--stale"], document_root=doc_root, db_path=db_path)
    assert code == 0
    assert "Stale/missing: 0 of 1" in stdout
    assert "Nothing to do" in stdout


def test_main_stale_branch_generates_when_stale_paths_exist(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    import kairix.credentials as cred_mod
    import kairix.knowledge.summaries.generate as gen_mod
    import kairix.knowledge.summaries.staleness as stale_mod

    doc_root = tmp_path / "vault"
    doc_root.mkdir()
    (doc_root / "a.md").write_text("# a", encoding="utf-8")
    (doc_root / "b.md").write_text("# b", encoding="utf-8")
    db_path = tmp_path / "s.db"
    _init_db(db_path)

    monkeypatch.setattr(cred_mod, "get_credentials", lambda kind: SimpleNamespace(api_key="k", endpoint="e"))
    monkeypatch.setattr(stale_mod, "get_stale_paths", lambda all_paths, db: list(all_paths))

    written: list[Any] = []
    monkeypatch.setattr(stale_mod, "write_summary", lambda r, _db: written.append(r))
    monkeypatch.setattr(gen_mod, "generate_summaries", lambda *, paths, **kw: [{"path": p} for p in paths])
    stdout, _stderr, code = _drive(["--stale"], document_root=doc_root, db_path=db_path)
    assert code == 0
    assert len(written) == 2
    assert "Stale/missing: 2 of 2" in stdout


def test_main_path_branch_for_existing_file(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    import kairix.credentials as cred_mod
    import kairix.knowledge.summaries.generate as gen_mod
    import kairix.knowledge.summaries.staleness as stale_mod

    doc_root = tmp_path / "vault"
    doc_root.mkdir()
    target = doc_root / "one.md"
    target.write_text("# one", encoding="utf-8")

    db_path = tmp_path / "s.db"
    _init_db(db_path)

    monkeypatch.setattr(cred_mod, "get_credentials", lambda kind: SimpleNamespace(api_key="k", endpoint="e"))
    monkeypatch.setattr(stale_mod, "write_summary", lambda r, _db: None)
    monkeypatch.setattr(gen_mod, "generate_summaries", lambda *, paths, **kw: [{"p": p} for p in paths])

    _stdout, _stderr, code = _drive(["--path", str(target)], document_root=doc_root, db_path=db_path)
    assert code == 0


def test_main_path_exits_1_when_file_missing(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    import kairix.credentials as cred_mod

    monkeypatch.setattr(cred_mod, "get_credentials", lambda kind: SimpleNamespace(api_key="k", endpoint="e"))

    doc_root = tmp_path / "vault"
    doc_root.mkdir()
    db_path = tmp_path / "s.db"
    _init_db(db_path)

    _stdout, stderr, code = _drive(["--path", str(tmp_path / "absent.md")], document_root=doc_root, db_path=db_path)
    assert code == 1
    assert "File not found" in stderr


def test_main_credential_failure_exits_1(monkeypatch, tmp_path: Path) -> None:
    """When get_credentials raises, the CLI prints the error and exits 1."""
    import kairix.credentials as cred_mod

    def _raises(_kind: str):
        raise RuntimeError("no Azure key")

    monkeypatch.setattr(cred_mod, "get_credentials", _raises)

    doc_root = tmp_path / "vault"
    doc_root.mkdir()
    (doc_root / "a.md").write_text("# a", encoding="utf-8")
    db_path = tmp_path / "s.db"
    _init_db(db_path)

    _stdout, stderr, code = _drive(["--all"], document_root=doc_root, db_path=db_path)
    assert code == 1
    assert "Error: no Azure key" in stderr


def test_main_resolves_defaults_when_neither_kwarg_provided(monkeypatch, tmp_path: Path) -> None:
    """When document_root and db_path are None, kairix.paths is consulted."""
    import kairix.paths as paths_mod

    doc_root = tmp_path / "vault"
    doc_root.mkdir()
    (doc_root / "a.md").write_text("# a", encoding="utf-8")
    db_path = tmp_path / "s.db"
    _init_db(db_path)

    monkeypatch.setattr(paths_mod, "document_root", lambda: doc_root)
    monkeypatch.setattr(paths_mod, "summaries_db_path", lambda: db_path)

    _stdout, _stderr, code = _drive(["--status"])
    assert code == 0


def test_module_main_guard() -> None:
    """Drive the ``__main__`` guard at the bottom of the file."""
    old_argv = sys.argv
    try:
        sys.argv = ["kairix-summarise", "--status"]
        # No document_root override — runpy will hit the default resolution.
        # We catch SystemExit no matter what — the guard executed if we got here.
        with pytest.raises(SystemExit):
            runpy.run_module("kairix.knowledge.summaries.cli", run_name="__main__")
    except SystemExit:
        # The guard ran main(), which may itself raise SystemExit. Either
        # outcome means the guard was executed.
        pass
    finally:
        sys.argv = old_argv
