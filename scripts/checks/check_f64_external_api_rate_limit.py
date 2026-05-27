"""F64: every external HTTP client plugin has a rate-limit / Retry-After test.

Scope: every directory under ``kairix/connectors/<name>/`` and
``kairix/providers/<name>/``. When any ``.py`` in the directory tree
imports ``httpx``, ``requests``, ``urllib.request``, ``aiohttp``,
``msgraph``, ``msgraph_core``, ``notion_client``, ``slack_sdk``, or
``openai`` (HTTP-bearing clients), the plugin must ship one of:
  * ``tests/integration/test_<name>_rate_limit.py``
  * ``tests/bdd/features/<name>_rate_limit.feature``

Rationale: the v2026.5.28a1 production incident included SharePoint
Graph requests with no Retry-After / 429 handling — every throttled
tick dead-lettered every item on the throttled drive. F64 forces
every new plugin that talks to a rate-limited external service to
prove it degrades gracefully under throttle.

Spec: ``docs/architecture/fitness-functions.md`` §F64.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _arch_lib import REPO_ROOT, gate, repo_relative

REMEDIATION = """F64: plugin <name> talks to an external HTTP service but ships no rate-limit test.

fix: add tests/integration/test_<name>_rate_limit.py asserting:
  (a) the client honours HTTP 429 + Retry-After by sleeping the requested
      number of seconds before retrying, and
  (b) the client honours HTTP 503 + Retry-After equivalently, and
  (c) after exhausting max-retries it raises (does NOT silently
      dead-letter every in-flight item).
See tests/integration/test_sharepoint_rate_limit.py for the canonical
shape.
next: see docs/architecture/fitness-functions.md §F64 for the full
specification.
run: python3 scripts/checks/check_f64_external_api_rate_limit.py

Pass example (test file present):

    # tests/integration/test_sharepoint_rate_limit.py
    @pytest.mark.integration
    def test_429_with_retry_after_honoured():
        fake_transport = FakeGraphTransport(
            responses=[(429, {"Retry-After": "2"}), (200, ...)]
        )
        client = SharePointGraphClient(transport=fake_transport, sleep_fn=fake_sleep)
        result = client.get(...)
        assert fake_sleep.calls == [2]
        assert result.status_code == 200

Forbidden example (current state before F64 for a new plugin):

    # kairix/connectors/newthing/client.py imports httpx, fires requests,
    # never handles 429. No tests/integration/test_newthing_rate_limit.py
    # exists. F64 fails the commit.
"""

HTTP_LIBS = frozenset(
    {
        "httpx",
        "requests",
        "urllib",
        "urllib.request",
        "aiohttp",
        "msgraph",
        "msgraph_core",
        "notion_client",
        "slack_sdk",
        "openai",
    }
)
PLUGIN_ROOTS = ("kairix/connectors", "kairix/providers")
TEST_DIRS = (
    "tests/integration",
    "tests/bdd/features",
)


def _imports_http_lib(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in HTTP_LIBS or alias.name in HTTP_LIBS:
                    return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            top = module.split(".")[0] if module else ""
            if top in HTTP_LIBS or module in HTTP_LIBS:
                return True
    return False


def _plugin_uses_http(plugin_root: Path) -> bool:
    """True iff any .py under plugin_root imports a known HTTP client."""
    for path in plugin_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        if _imports_http_lib(tree):
            return True
    return False


def _has_rate_limit_test(repo_root: Path, plugin_name: str) -> bool:
    expected_py = repo_root / "tests" / "integration" / f"test_{plugin_name}_rate_limit.py"
    expected_feature = repo_root / "tests" / "bdd" / "features" / f"{plugin_name}_rate_limit.feature"
    return expected_py.is_file() or expected_feature.is_file()


def main() -> int:
    violations: set[Path] = set()
    for plugin_root_rel in PLUGIN_ROOTS:
        plugin_root = REPO_ROOT / plugin_root_rel
        if not plugin_root.is_dir():
            continue
        for entry in sorted(plugin_root.iterdir()):
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            if not _plugin_uses_http(entry):
                continue
            if _has_rate_limit_test(REPO_ROOT, entry.name):
                continue
            violations.add(repo_relative(entry))
    return gate("f64-external-api-rate-limit", violations, REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())
