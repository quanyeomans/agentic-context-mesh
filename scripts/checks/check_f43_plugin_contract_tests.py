"""F43: every plugin has a contract test that exercises the canonical
fake AND the real implementation against the same Protocol assertions.

Plugins under ``kairix/connectors/<name>/``, ``kairix/extractors/<name>/``,
and ``kairix/providers/<name>/`` are independently shippable. F30 locks
the operator-outcome layer at the CLI / MCP edge; F43 mirrors that
discipline one layer down: every plugin must carry a contract test
that proves the canonical fake from ``tests/fakes.py`` AND the real
implementation under ``kairix/<tree>/<name>/`` satisfy the same
Protocol-shaped assertions.

The required file is ``tests/contracts/test_<name>_protocol.py``. It
must:

  1. Import the canonical fake from ``tests.fakes`` (e.g.
     ``from tests.fakes import FakeOpenAIProvider``), AND
  2. Import the real implementation from
     ``kairix.<tree>.<name>`` (e.g.
     ``from kairix.providers.openai import OpenAIProvider``).

The contract assertions themselves (Protocol isinstance checks, method
signatures, behavioural equivalence under shared inputs) live inside
the test module; this gate verifies the file exists and carries both
imports — the same shape F30 uses for its CLI subprocess + MCP tool
assertions.

Plugin discovery: every immediate subdirectory of
``kairix/{connectors,extractors,providers}/`` whose name is not
``_``-prefixed and is not in the small allow-list (``__pycache__``)
is a plugin. Files at the trees' roots are NOT plugins.

Violations are reported by the plugin directory path
(``kairix/<tree>/<name>``), one entry per plugin missing the contract
test, and grandfathered through ``.architecture/baseline/f43-files.txt``.

If none of the plugin trees exist on disk, the check passes trivially.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _arch_lib import REPO_ROOT, gate

_PLUGIN_TREES_REL: tuple[Path, ...] = (
    Path("kairix") / "connectors",
    Path("kairix") / "extractors",
    Path("kairix") / "providers",
)

_NON_PLUGIN_NAMES: frozenset[str] = frozenset({"__pycache__"})

_CONTRACTS_DIR_REL = Path("tests") / "contracts"

REMEDIATION = """F43: plugin is missing
``tests/contracts/test_<name>_protocol.py``, OR the file exists but
doesn't import BOTH the canonical fake from ``tests/fakes.py`` AND
the real implementation from ``kairix/<tree>/<name>/``.

The contract layer is where the Protocol shape between domain and
plugin is proved. Without a test that runs the canonical fake AND
the real impl against the same assertions, the fake can drift away
from the real wire (or vice versa) and the production path silently
diverges from what BDD / unit tests measure.

fix: create ``tests/contracts/test_<name>_protocol.py`` that imports
both the canonical fake (``from tests.fakes import Fake<Name>``) and
the real implementation (``from kairix.<tree>.<name> import <Class>``),
then runs a shared assertion (e.g. ``@pytest.mark.parametrize``
across both implementations) that proves they satisfy the same
Protocol shape under realistic inputs.
next: re-run python3 scripts/checks/check_f43_plugin_contract_tests.py
to confirm the gate goes green.
run: bash scripts/safe-commit.sh \"test(contracts): add contract test for <plugin>\"

Pass example (tests/contracts/test_openai_protocol.py):
  import pytest
  from kairix.core.protocols import ChatBackend
  from kairix.providers.openai import OpenAIProvider
  from tests.fakes import FakeOpenAIProvider

  @pytest.mark.contract
  @pytest.mark.parametrize(\"factory\", [FakeOpenAIProvider, lambda: OpenAIProvider(api_key=\"x\")])
  def test_chat_backend_protocol_shape(factory):
      backend = factory()
      assert isinstance(backend, ChatBackend)

Forbidden example:
  kairix/providers/openai/ exists, but no
  tests/contracts/test_openai_protocol.py — fake and real are never
  proved equivalent at the Protocol boundary.

Why: F30 proves the CLI / MCP entry points compose against real code;
F43 proves the plugin layer composes against its canonical fake.
Together they remove the LoCoMo-class blind spot where unit + BDD
tests all pass against fakes that drift away from real behaviour."""


def _discover_plugins(tree_root: Path) -> list[Path]:
    """List plugin directories under ``tree_root``.

    Skips ``_``-prefixed names and the cache allow-list. Files at
    the tree root are never plugins. Empty list if the root doesn't
    exist.
    """
    if not tree_root.exists():
        return []
    out: list[Path] = []
    for child in sorted(tree_root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("_"):
            continue
        if child.name in _NON_PLUGIN_NAMES:
            continue
        out.append(child)
    return out


def _file_imports_both(contract_file: Path, plugin_dotted: str) -> bool:
    """True if ``contract_file`` imports from both ``tests.fakes`` AND
    from the real plugin module path (``plugin_dotted``, e.g.
    ``kairix.providers.openai``).

    Recognises every common import shape:

      * ``from tests.fakes import X``
      * ``from tests import fakes``
      * ``import tests.fakes``
      * ``from kairix.providers.openai import OpenAIProvider``
      * ``import kairix.providers.openai``
      * ``from kairix.providers.openai.provider import OpenAIProvider``
    """
    try:
        tree = ast.parse(contract_file.read_text(encoding="utf-8"), filename=str(contract_file))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False

    imports_fakes = False
    imports_real = False

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "tests.fakes" or module.startswith("tests.fakes."):
                imports_fakes = True
            elif module == "tests" and any(alias.name == "fakes" for alias in node.names):
                imports_fakes = True
            if module == plugin_dotted or module.startswith(plugin_dotted + "."):
                imports_real = True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name == "tests.fakes" or name.startswith("tests.fakes."):
                    imports_fakes = True
                if name == plugin_dotted or name.startswith(plugin_dotted + "."):
                    imports_real = True

    return imports_fakes and imports_real


def _plugin_violates(plugin_dir: Path, repo_root: Path) -> bool:
    """True if the plugin lacks
    ``tests/contracts/test_<name>_protocol.py`` OR the file exists but
    doesn't carry both the fake and real imports.
    """
    name = plugin_dir.name
    contract_file = repo_root / _CONTRACTS_DIR_REL / f"test_{name}_protocol.py"
    if not contract_file.is_file():
        return True
    # Build the dotted production module path: kairix.<tree>.<name>
    # (e.g. kairix.providers.openai). The plugin_dir is
    # ``<repo_root>/kairix/<tree>/<name>``; resolving relative to
    # ``repo_root`` avoids the worktree-bug where the filesystem path
    # itself contains ``kairix`` as a parent directory (e.g.
    # ``/work/kairix/kairix/kairix/extractors/<name>``) — using
    # ``parts.index('kairix')`` on the absolute path picks the wrong
    # occurrence and the dotted lookup mismatches every legitimate
    # contract-test import.
    try:
        rel_parts = plugin_dir.resolve().relative_to(repo_root.resolve()).parts
    except ValueError:
        return True
    if not rel_parts or rel_parts[0] != "kairix":
        return True
    plugin_dotted = ".".join(rel_parts)
    return not _file_imports_both(contract_file, plugin_dotted)


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Walk every plugin tree under ``<repo_root>/kairix/`` and return
    repo-relative plugin paths missing F43-conformant contract tests.

    Empty set if no plugin trees / no plugins exist.
    """
    violations: set[Path] = set()
    for tree_rel in _PLUGIN_TREES_REL:
        tree_root = repo_root / tree_rel
        for plugin_dir in _discover_plugins(tree_root):
            if _plugin_violates(plugin_dir, repo_root):
                try:
                    violations.add(plugin_dir.resolve().relative_to(repo_root))
                except ValueError:
                    violations.add(tree_rel / plugin_dir.name)
    return violations


def main() -> int:
    violations = collect_violations()
    return gate("f43", violations, REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())
