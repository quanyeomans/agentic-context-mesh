"""F64 rate-limit contract for the obsidian connector.

Unlike SharePoint / M365 / GitHub / Slack / Notion / Google, the
obsidian connector is **filesystem-bound**: it scans a local vault
directory and emits the markdown contents directly. There is no
external HTTP service for it to be throttled by, and therefore no
429 / Retry-After path to test.

This module exists to satisfy F64 (every plugin importing an HTTP
client must ship a rate-limit test). The obsidian connector imports
``urllib.parse.quote`` only to URL-encode an ``obsidian://open?...``
deep-link for operator-facing logs — no socket is opened, no remote
call is made.

The contract this module pins:

  1. The obsidian connector's runtime entry points (``connector.py``,
     ``reconciler.py``, ``watcher.py``, ``fs.py``) do not import any
     HTTP transport library beyond the ``urllib.parse`` URL-encoder.
  2. The connector's ``urllib`` usage is restricted to ``quote(...)``
     (a pure-Python URL escape) — never to ``urlopen`` /
     ``urllib.request`` / ``urllib.error`` / ``urllib.robotparser``
     which would open a network socket.

Sabotage-proof: change connector.py to ``import urllib.request as
_req; _req.urlopen(...)`` → assertion (2) below fails on the
``urllib_request_imports`` check.

F-rule discipline:
  - F8: ``pytestmark = pytest.mark.integration``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_OBSIDIAN_ROOT = Path(__file__).resolve().parents[2] / "kairix" / "connectors" / "obsidian"


def _collect_imported_modules(src: Path) -> set[str]:
    """Return the set of fully-qualified module names imported by ``src``."""
    tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _collect_imported_names(src: Path) -> set[str]:
    """Return the set of names brought into the module's namespace via ``from X import Y``."""
    tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.name)
    return names


# ---------------------------------------------------------------------------
# (1) obsidian connector never imports a network HTTP client
# ---------------------------------------------------------------------------

_BANNED_HTTP_MODULES = frozenset(
    {
        "httpx",
        "requests",
        "aiohttp",
        "msgraph",
        "msgraph_core",
        "notion_client",
        "slack_sdk",
        "openai",
        "urllib.request",
        "urllib.error",
        "urllib.robotparser",
    }
)


def test_obsidian_connector_imports_no_http_transport() -> None:
    """Walk every .py under kairix/connectors/obsidian/ and confirm no
    network-HTTP module is imported. ``urllib.parse`` (URL encoding) is
    explicitly allowed — it never opens a socket.

    Sabotage-proof: add ``import httpx`` to connector.py → this test
    fails with the file path + module name in the assertion message.
    """
    violations: list[tuple[Path, str]] = []
    for py in _OBSIDIAN_ROOT.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        imported = _collect_imported_modules(py)
        for mod in imported:
            if mod in _BANNED_HTTP_MODULES:
                violations.append((py, mod))
    assert not violations, (
        f"obsidian connector imported a banned HTTP module — the connector is "
        f"filesystem-bound and must not open sockets. violations: {violations}"
    )


# ---------------------------------------------------------------------------
# (2) obsidian's urllib usage is restricted to the URL-encoder
# ---------------------------------------------------------------------------


def test_obsidian_urllib_usage_is_url_encoding_only() -> None:
    """The only ``urllib`` symbol the obsidian connector imports is
    ``quote`` (from ``urllib.parse``). ``urlopen`` / ``Request`` /
    ``HTTPError`` / etc. are forbidden — they'd open a socket.

    Sabotage-proof: add ``from urllib.request import urlopen`` to
    connector.py → this test fails with the disallowed name listed.
    """
    allowed_urllib_names = {"quote", "quote_plus", "unquote", "urlencode", "urlparse", "urlunparse"}
    violations: list[tuple[Path, str]] = []
    for py in _OBSIDIAN_ROOT.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("urllib"):
                for alias in node.names:
                    if alias.name not in allowed_urllib_names:
                        violations.append((py, f"{node.module}.{alias.name}"))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("urllib") and alias.name != "urllib.parse":
                        violations.append((py, alias.name))
    assert not violations, (
        f"obsidian connector imports a non-encoder urllib symbol — only the URL "
        f"encoder family is permitted. violations: {violations}"
    )


# ---------------------------------------------------------------------------
# (3) The connector module imports the URL encoder we expect
# (sanity floor — if this breaks, the connector restructured and the F64
# rationale needs revisiting)
# ---------------------------------------------------------------------------


def test_obsidian_connector_url_encoder_is_imported() -> None:
    """The connector imports ``quote`` from ``urllib.parse`` for
    ``obsidian://open?...`` deep-link construction.

    Sabotage-proof: rename the import in connector.py → this test fails
    on the membership assertion, signalling the F64 baseline rationale
    needs to be re-justified (the connector may no longer need urllib).
    """
    connector_py = _OBSIDIAN_ROOT / "connector.py"
    names = _collect_imported_names(connector_py)
    assert "quote" in names, (
        f"obsidian connector.py no longer imports ``quote`` from urllib.parse; "
        f"the F64 baseline rationale (URL encoding only) needs revisiting. "
        f"imports: {sorted(names)}"
    )
