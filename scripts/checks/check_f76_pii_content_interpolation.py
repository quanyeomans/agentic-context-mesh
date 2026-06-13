"""F76: no f-string interpolation of content-like vars in log/exception/dead-letter strings.

F15 catches the named-variable case — ``logger.info("token=%s", token)``
where ``token`` is a known secret-name. F76 catches the structural
sibling F15 doesn't: ``logger.exception(f"failed for {raw_artefact}")``
where ``raw_artefact`` is the document body. Same leak class (PII in
logs), different surface (content-named vars instead of secret-named
vars), different detector shape (f-string interpolation rather than
positional-kwarg passing).

Motivation
----------
2026-05 leak audit (memory ``feedback_no_confidential_in_public_artefacts``)
found PII in commit messages, BDD scenarios, and release notes —
covered by F73 (private-infra patterns) and the manual review muscle
memory. The mirror failure mode — PII in *machine-generated* log lines
— remains uncovered. A connector that fetches a SharePoint document
and logs ``logger.exception(f"failed to parse: {item.body}")`` ships
the document content to whatever drain the worker log goes to
(stdout, journalctl, Azure Log Analytics). At production scale this
turns log retention into an unaudited content sink.

Detection (AST)
---------------
For every call to:

* ``logger.{debug,info,warning,error,critical,exception}(...)``
* ``logging.{debug,info,...}(...)``
* ``print(...)`` / ``sys.stdout.write(...)`` / ``sys.stderr.write(...)``
* ``raise <ExcClass>(...)`` (any exception constructor)
* ``dead_letter.record(<name>, <id>, <msg>)`` (the connector
  framework's dead-letter sink — its third positional arg lands
  durably in the SQLite table for operator inspection)

If any argument is an ``ast.JoinedStr`` (f-string) that interpolates
a ``ast.Name`` or ``ast.Attribute`` whose final identifier matches
one of the ``_CONTENT_HINT_NAMES``, flag the call.

Allowed: a rationale comment ``# F76-allow: <reason>`` on the same
line or the line directly above. Use for cases where the content is
provably bounded + non-sensitive (e.g. a connector name string).

Out of scope (avoid by construction)
------------------------------------
* The ``kairix/{secrets,credentials}.py`` boundary modules — same
  exemption F15 honours.
* ``tests/`` — test fixtures often log full payloads for debugging
  assertions; the leak surface is production code.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _fitness_rule import FitnessRule
from tc_fitness import python_files, repo_relative  # noqa: F401 — back-compat

# Identifier name fragments that, when interpolated into a log/exception
# string, indicate the *content* of an external artefact is being
# emitted into a log sink. Match is exact on the final identifier
# segment (not substring) — ``item.body`` matches via ``body``,
# ``content_hash`` does NOT match via ``content`` (different identifier).
_CONTENT_HINT_NAMES: frozenset[str] = frozenset(
    {
        "raw",
        "body",
        "content",
        "payload",
        "text",
        "message",
        "chunk",
        "doc",
        "document",
        "extracted",
        "markdown",
        "html",
        "xml",
        "attachment",
        "blob",
        "signal",
        "snippet",
        "passage",
    }
)

# Logging method names that route through to a sink.
_LOG_METHODS: frozenset[str] = frozenset({"debug", "info", "warning", "warn", "error", "critical", "exception", "log"})

# Modules whose .py files are exempt — same boundary as F15.
# ``kairix/secrets/`` is a package; the prefix variant in
# :func:`_file_has_violation` covers every submodule under it.
_EXEMPT_FILES: frozenset[str] = frozenset(
    {
        "kairix/secrets.py",
        "kairix/credentials.py",
    }
)

# Prefix exemptions — same boundary intent as ``_EXEMPT_FILES`` but
# for whole subtrees.
_EXEMPT_PREFIXES: tuple[str, ...] = ("kairix/secrets/",)

_EXEMPT_COMMENT = "# F76-allow:"

REMEDIATION = """F76: f-string in a log / exception / dead-letter call
interpolates a content-named variable. The interpolated value lands in
the log sink (stdout / journalctl / cloud log aggregator); at production
scale this turns log retention into an unaudited content sink.

fix: log the IDENTIFIER, not the CONTENT:
  - emit ``item_id`` / ``source_name`` / ``record.path`` (bounded
    metadata fields) instead of ``item.body`` / ``raw_artefact`` /
    ``chunk.content``.
  - if the content really IS the troubleshooting signal (size, mime
    type, hash), log those derived facts: ``len(body)``,
    ``raw.mime``, ``hashlib.sha256(payload).hexdigest()[:8]``.
  - if the content is provably bounded + non-sensitive (e.g. an
    enum name, a status code), add a rationale comment:
    ``# F76-allow: <reason>``.
next: re-run python3 scripts/checks/check_f76_pii_content_interpolation.py
  to confirm the gate goes green.
run: bash scripts/safe-commit.sh "fix(<module>): redact content from <call> log"

Pass example:
  logger.exception(
      "failed to extract item_id=%s mime=%s size=%d",
      item_id, raw.mime, len(raw.body),
  )

  # connector_framework: dead-letter the IDENTIFIER + reason class
  dead_letter.record(source_name, item_id, f"extract: {type(exc).__name__}")

Forbidden example:
  logger.exception(f"failed for {raw_artefact}")            # F76
  logger.error("body=%s", item.body)                        # F76
  dead_letter.record(source_name, item_id, f"failed: {body}")  # F76
  raise ValueError(f"could not parse {document.content}")  # F76

Allowed exemption (rare):
  # F76-allow: extractor_name is the plugin identifier (markitdown/ocr/...)
  logger.info(f"extracted via {extractor_name}")

Why: F15 catches secret-NAMED variables. F76 catches the structural
sibling — CONTENT-named variables. Both leak PII into logs; F15 covers
the kwarg-passing surface, F76 covers the f-string-interpolation
surface."""


def _is_log_or_exc_or_print_call(node: ast.Call) -> bool:
    """True if ``node`` is a call we care about: logger.*, print, sys.std*,
    dead_letter.record, or a raised exception constructor.

    Raised exceptions are NOT detected here — they show up via
    ``ast.Raise.exc`` which is processed separately.
    """
    if not isinstance(node.func, ast.Attribute):
        # print(...) is ast.Name
        return isinstance(node.func, ast.Name) and node.func.id == "print"
    attr = node.func.attr
    # logger.info(...) / logging.warning(...) / log.exception(...)
    if attr in _LOG_METHODS:
        # the receiver name typically contains "log"
        recv = node.func.value
        recv_name = recv.attr if isinstance(recv, ast.Attribute) else recv.id if isinstance(recv, ast.Name) else None
        if recv_name and ("log" in recv_name.lower()):
            return True
    # dead_letter.record(...)
    if attr == "record":
        recv = node.func.value
        recv_name = recv.attr if isinstance(recv, ast.Attribute) else recv.id if isinstance(recv, ast.Name) else None
        if recv_name and ("dead_letter" in recv_name.lower() or "deadletter" in recv_name.lower()):
            return True
    # sys.stdout.write(...) / sys.stderr.write(...)
    if attr == "write":
        recv = node.func.value
        if isinstance(recv, ast.Attribute) and recv.attr in {"stdout", "stderr"}:
            return True
    return False


def _identifier_tail(node: ast.expr) -> str | None:
    """Return the final identifier of a Name / Attribute chain.

    ``item.body`` → ``"body"``. ``raw_artefact`` → ``"raw_artefact"``.
    ``self._chunks[0]`` → ``None`` (subscript — out of scope).
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _fstring_interpolates_content_var(joined: ast.JoinedStr) -> bool:
    """True if any ``ast.FormattedValue`` in the f-string interpolates a
    variable whose final identifier matches a content-hint name.
    """
    for part in joined.values:
        if not isinstance(part, ast.FormattedValue):
            continue
        tail = _identifier_tail(part.value)
        if tail is None:
            continue
        if tail in _CONTENT_HINT_NAMES:
            return True
        # Match identifiers that END with one of the hints (e.g.
        # ``raw_artefact`` ends with ``raw`` is FALSE here — we want
        # exact tails). Use suffix match for compound names like
        # ``item_body`` → matches ``body``.
        for hint in _CONTENT_HINT_NAMES:
            if tail.endswith("_" + hint) or tail == hint:
                return True
    return False


def _line_carries_exempt(source: str, lineno: int) -> bool:
    lines = source.splitlines()
    if 1 <= lineno <= len(lines):
        if _EXEMPT_COMMENT in lines[lineno - 1]:
            return True
    if 2 <= lineno <= len(lines):
        prior = lines[lineno - 2]
        if _EXEMPT_COMMENT in prior:
            return True
    return False


def _file_has_violation(path: Path, repo_root: Path) -> bool:
    """True if any logging/exception/dead-letter call in this file
    interpolates a content-named variable via f-string.
    """
    try:
        rel = path.resolve().relative_to(repo_root)
    except ValueError:
        return False
    if str(rel) in _EXEMPT_FILES:
        return False
    if any(str(rel).startswith(prefix) for prefix in _EXEMPT_PREFIXES):
        return False

    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False

    for node in ast.walk(tree):
        # Direct call: logger.* / print / dead_letter.record / sys.std*.write
        if isinstance(node, ast.Call) and _is_log_or_exc_or_print_call(node):
            for arg in node.args:
                if isinstance(arg, ast.JoinedStr) and _fstring_interpolates_content_var(arg):
                    if not _line_carries_exempt(source, node.lineno):
                        return True
        # raise X(<f-string>) — Raise.exc is the call expression
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            for arg in node.exc.args:
                if isinstance(arg, ast.JoinedStr) and _fstring_interpolates_content_var(arg):
                    if not _line_carries_exempt(source, node.lineno):
                        return True
    return False


class F76(FitnessRule):
    """F76 as a FitnessRule subclass — see module docstring."""

    name = "f76-pii-content-interpolation"
    remediation = REMEDIATION
    roots = ("kairix",)

    def file_has_violation(self, path: Path) -> bool:
        return _file_has_violation(path, self._repo_root)


def main() -> int:
    return F76().run()


if __name__ == "__main__":
    sys.exit(main())
