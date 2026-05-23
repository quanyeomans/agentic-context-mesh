"""``kairix cc-pair`` CLI — operator surface over the Wave C lifecycle service.

Wraps :mod:`kairix.core.connectors.cc_pair` (the F57-centralised
lifecycle owner) with five verbs operators run against the
``topology_cc_pairs`` table:

* ``kairix cc-pair list``   — show every cc_pair (optionally JSON).
* ``kairix cc-pair create`` — INSERT a fresh row (``status=SCHEDULED``).
* ``kairix cc-pair pause``  — transition ``ACTIVE → PAUSED``.
* ``kairix cc-pair resume`` — transition ``PAUSED → ACTIVE``.
* ``kairix cc-pair delete`` — transition to ``DELETING`` (terminal).

State transitions go through :func:`transition_cc_pair` — this module
NEVER writes ``topology_cc_pairs.status`` directly, so F57 stays clean.

Thin adapter: all business logic lives in the Wave C lifecycle module.
``main`` only parses argv, opens the SQLite connection (via the
:func:`db_provider` DI seam), dispatches, and renders.

Per F30 + F45 + F46:

* F30 outcome tests exercise each verb via
  ``subprocess.run([python, -m, kairix.cli, cc-pair, ...])`` and assert
  on stdout/stderr content (see ``tests/integration/test_outcome_cc_pair_cli.py``).
* F45 ships ``tests/bdd/features/cli_cc_pair.feature`` in the same
  commit.
* F46 BDD step impls invoke :func:`main` directly, not a Pipeline
  constructor.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from kairix.core.connectors.cc_pair import (
    cc_pair_lifecycle_audit_blob,
    create_cc_pair,
    get_cc_pair,
    list_cc_pairs,
    transition_cc_pair,
)
from kairix.core.protocols import CCPairStatus, CCPairTransitionError, ConnectorCredentialPair

# F17 — repeated literal across multiple subparsers' help text + envelopes.
_HELP_DB_PATH = "Path to the kairix SQLite database (default: KAIRIX_DB_PATH / config / platform default)."
_FLAG_JSON = "--json"
_OK_KEY = "ok"
_HELP_EMIT_JSON = "Emit a JSON envelope on stdout."
_ACTION_STORE_TRUE = "store_true"
# F17 — f-string literal segment "cc_pair id=" duplicated across create / transition / delete rendering paths.
_CC_PAIR_ID_PREFIX = "cc_pair id="

# DI seam — production callers leave it default; tests pass a custom
# provider that opens an in-memory or tmp-path db without
# monkey-patching kairix.paths.
DbProvider = Callable[[Path | None], sqlite3.Connection]


def default_db_provider(explicit_path: Path | None) -> sqlite3.Connection:
    """Production DB provider — opens the path or the platform default.

    Wrapper so the CLI never reads ``KAIRIX_DB_PATH`` directly (F4
    boundary stays on :mod:`kairix.paths`).
    """
    if explicit_path is not None:
        return sqlite3.connect(str(explicit_path))
    from kairix.paths import db_path

    return sqlite3.connect(str(db_path()))


# ---------------------------------------------------------------------------
# Renderers — pure functions; one shape per verb / mode.
# ---------------------------------------------------------------------------


def _render_pair_row(pair: ConnectorCredentialPair) -> str:
    """One-line text-mode row for ``cc-pair list``."""
    return (
        f"{pair.id:<5} {pair.name:<40} {pair.status:<18} {pair.access_type:<8} "
        f"docs={pair.total_docs_indexed:<6} updated={pair.updated_at}"
    )


def _render_list_human(pairs: tuple[ConnectorCredentialPair, ...]) -> str:
    """Render ``cc-pair list`` in operator-friendly text mode."""
    if not pairs:
        return "No cc_pairs declared. fix: run `kairix cc-pair create` to register one."
    header = f"{'ID':<5} {'NAME':<40} {'STATUS':<18} {'ACCESS':<8} {'DOCS':<10} UPDATED"
    rows = [header] + [_render_pair_row(p) for p in pairs]
    return "\n".join(rows)


def _render_pair_json(pair: ConnectorCredentialPair) -> dict[str, Any]:
    """One JSON-mode dict per cc_pair (sorted-key friendly)."""
    parsed: dict[str, Any] = json.loads(cc_pair_lifecycle_audit_blob(pair))
    return parsed


def _render_list_json(pairs: tuple[ConnectorCredentialPair, ...]) -> str:
    """Render ``cc-pair list --json`` as a stable envelope."""
    return json.dumps(
        {"cc_pairs": [_render_pair_json(p) for p in pairs], "count": len(pairs)},
        sort_keys=True,
        indent=2,
    )


# ---------------------------------------------------------------------------
# Verb impls — return (exit_code, stdout_text). Stdout is what F30 asserts on.
# ---------------------------------------------------------------------------


def _verb_list(db: sqlite3.Connection, *, status: CCPairStatus | None, emit_json: bool) -> tuple[int, str]:
    """List every cc_pair (or filtered by status)."""
    pairs = list_cc_pairs(db, status=status)
    if emit_json:
        return 0, _render_list_json(pairs)
    return 0, _render_list_human(pairs)


def _verb_create(
    db: sqlite3.Connection,
    *,
    connector_id: int,
    credential_id: int | None,
    name: str,
    access_type: str,
    emit_json: bool,
) -> tuple[int, str]:
    """INSERT a fresh cc_pair row at ``status=SCHEDULED``.

    F3-rationale: access_type comes from argparse choices ⇒ closed-set
    but typed str; cast at the call site so mypy sees a CCPairAccessType.
    """
    if access_type not in ("PUBLIC", "PRIVATE", "SYNC"):
        return 2, f"invalid access_type={access_type!r}. fix: use PUBLIC, PRIVATE, or SYNC."
    # F3-rationale: closed-set guard above; mypy still narrows to str only.
    pair = create_cc_pair(
        db,
        connector_id=connector_id,
        credential_id=credential_id,
        name=name,
        access_type=access_type,  # type: ignore[arg-type]  # F3-rationale: argparse choices closed-set; mypy doesn't narrow str to CCPairAccessType Literal.
    )
    db.commit()
    if emit_json:
        return 0, json.dumps({_OK_KEY: True, "id": pair.id, "status": pair.status, "name": pair.name}, sort_keys=True)
    return 0, f"created {_CC_PAIR_ID_PREFIX}{pair.id} name={pair.name!r} status={pair.status}"


def _apply_transition(
    db: sqlite3.Connection,
    *,
    cc_pair_id: int,
    target_status: CCPairStatus,
    reason: str | None,
    emit_json: bool,
) -> tuple[int, str]:
    """Helper — wrap :func:`transition_cc_pair` with operator-friendly errors."""
    try:
        pair = transition_cc_pair(db, cc_pair_id, target_status, reason=reason)
    except CCPairTransitionError as exc:
        if emit_json:
            return 1, json.dumps(
                {_OK_KEY: False, "error": str(exc), "current": exc.current, "target": exc.target},
                sort_keys=True,
            )
        return 1, (
            f"illegal transition: {exc}. fix: choose a valid target per the cc_pair state machine "
            "(SCHEDULED → INITIAL_INDEXING → ACTIVE ↔ PAUSED / DELETING / INVALID)."
        )
    db.commit()
    if emit_json:
        return 0, json.dumps(
            {_OK_KEY: True, "id": pair.id, "status": pair.status, "name": pair.name},
            sort_keys=True,
        )
    return 0, f"{_CC_PAIR_ID_PREFIX}{pair.id} name={pair.name!r} → {pair.status}"


def _verb_pause(db: sqlite3.Connection, *, cc_pair_id: int, emit_json: bool) -> tuple[int, str]:
    """Transition ``ACTIVE → PAUSED``."""
    return _apply_transition(
        db,
        cc_pair_id=cc_pair_id,
        target_status="PAUSED",
        reason="operator pause via `kairix cc-pair pause`",
        emit_json=emit_json,
    )


def _verb_resume(db: sqlite3.Connection, *, cc_pair_id: int, emit_json: bool) -> tuple[int, str]:
    """Transition ``PAUSED → ACTIVE``."""
    return _apply_transition(
        db,
        cc_pair_id=cc_pair_id,
        target_status="ACTIVE",
        reason="operator resume via `kairix cc-pair resume`",
        emit_json=emit_json,
    )


def _verb_delete(db: sqlite3.Connection, *, cc_pair_id: int, emit_json: bool) -> tuple[int, str]:
    """Transition to ``DELETING`` (terminal).

    Operator-facing: the SQL row stays for audit but the worker stops
    syncing it; Wave G eventually drops the row from the schema.
    """
    pair = get_cc_pair(db, cc_pair_id)
    if pair is None:
        if emit_json:
            return 1, json.dumps(
                {_OK_KEY: False, "error": f"{_CC_PAIR_ID_PREFIX}{cc_pair_id} not found"}, sort_keys=True
            )
        return 1, f"{_CC_PAIR_ID_PREFIX}{cc_pair_id} not found. fix: `kairix cc-pair list` to see registered ids."
    return _apply_transition(
        db,
        cc_pair_id=cc_pair_id,
        target_status="DELETING",
        reason="operator delete via `kairix cc-pair delete`",
        emit_json=emit_json,
    )


# ---------------------------------------------------------------------------
# Parser + dispatch
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct the ``kairix cc-pair`` argparse tree."""
    parser = argparse.ArgumentParser(
        prog="kairix cc-pair",
        description="Operate on the topology_cc_pairs lifecycle (Wave D).",
    )
    parser.add_argument("--db-path", type=str, default=None, help=_HELP_DB_PATH)
    sub = parser.add_subparsers(dest="verb", required=True, metavar="VERB")

    list_p = sub.add_parser("list", help="Show every cc_pair (or filtered by --status).")
    list_p.add_argument(_FLAG_JSON, action=_ACTION_STORE_TRUE, help=_HELP_EMIT_JSON)
    list_p.add_argument(
        "--status",
        choices=("SCHEDULED", "INITIAL_INDEXING", "ACTIVE", "PAUSED", "DELETING", "INVALID"),
        default=None,
        help="Filter to one status.",
    )

    create_p = sub.add_parser("create", help="INSERT a fresh cc_pair row at status=SCHEDULED.")
    create_p.add_argument(_FLAG_JSON, action=_ACTION_STORE_TRUE, help=_HELP_EMIT_JSON)
    create_p.add_argument("--connector-id", type=int, required=True, help="topology_connectors.id of the source.")
    create_p.add_argument(
        "--credential-id",
        type=int,
        default=None,
        help="topology_credentials.id (or omit for credential-less connectors).",
    )
    create_p.add_argument("--name", type=str, required=True, help="Unique operator-facing name.")
    create_p.add_argument(
        "--access-type",
        choices=("PUBLIC", "PRIVATE", "SYNC"),
        default="PRIVATE",
        help="Per-cc_pair access mode (default PRIVATE).",
    )

    for verb in ("pause", "resume", "delete"):
        p = sub.add_parser(verb, help=f"Transition the cc_pair via the lifecycle service ({verb}).")
        p.add_argument(_FLAG_JSON, action=_ACTION_STORE_TRUE, help=_HELP_EMIT_JSON)
        p.add_argument("--id", type=int, required=True, dest="cc_pair_id", help="topology_cc_pairs.id to transition.")

    return parser


_VERB_DISPATCH: dict[str, Callable[..., tuple[int, str]]] = {
    "list": _verb_list,
    "create": _verb_create,
    "pause": _verb_pause,
    "resume": _verb_resume,
    "delete": _verb_delete,
}


def _dispatch(args: argparse.Namespace, db: sqlite3.Connection) -> tuple[int, str]:
    """Pick the verb-impl based on ``args.verb`` and call it with the parsed kwargs."""
    verb = args.verb
    emit_json = bool(getattr(args, "emit_json", False) or getattr(args, "json", False))
    if verb == "list":
        return _verb_list(db, status=args.status, emit_json=emit_json)
    if verb == "create":
        return _verb_create(
            db,
            connector_id=args.connector_id,
            credential_id=args.credential_id,
            name=args.name,
            access_type=args.access_type,
            emit_json=emit_json,
        )
    if verb in ("pause", "resume", "delete"):
        return _VERB_DISPATCH[verb](db, cc_pair_id=args.cc_pair_id, emit_json=emit_json)
    # Should never reach: argparse `required=True` rejects unknown verbs.
    return 2, f"unknown verb={verb!r}. fix: run `kairix cc-pair --help`."


def main(
    argv: list[str] | None = None,
    *,
    db_provider: DbProvider = default_db_provider,
) -> int:
    """Entry point for ``kairix cc-pair``.

    ``db_provider`` is the public DI seam: production callers leave it
    default; tests inject a provider that opens an in-memory sqlite or
    a tmp-path file so the F30 subprocess can carry a ``--db-path`` flag
    without env-var monkeypatching (F2-clean).
    """
    args = build_parser().parse_args(argv if argv is not None else sys.argv[2:])
    explicit_path = Path(args.db_path) if args.db_path else None
    db = db_provider(explicit_path)
    with closing(db):
        exit_code, payload = _dispatch(args, db)
    if exit_code == 0:
        sys.stdout.write(payload + "\n")
    else:
        sys.stderr.write(payload + "\n")
    return exit_code
