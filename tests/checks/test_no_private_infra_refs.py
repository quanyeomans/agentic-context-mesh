"""F73 detector tests — no private infrastructure references in committed source.

The F73 detector (``scripts/checks/check_no_private_infra_refs.py``)
flags specific Azure resource names (``vm-openclaw``, ``kv-tc-agents``,
``stagents001``, ``RG-AGENTS-CORE``, ``datadisk-vm-openclaw``),
internal hostnames (``*.threecubes.{ai,io}``), and private sibling-repo
references (``kairix-pro-platform``, ``tc-agent-zone``).

Generic placeholders (``<your-vm-name>``, ``<your-key-vault-name>``,
``example.com``, ``alice@example.com``) must not false-positive.

Sabotage proof: drop one ``vm-openclaw`` literal into ``test_pass_lines``
input and re-run — the relevant test flips red. Restore. Executed
during F73 landing: mutate -> red -> restore -> green.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECKS_DIR = _REPO_ROOT / "scripts" / "checks"
if str(_CHECKS_DIR) not in sys.path:
    sys.path.insert(0, str(_CHECKS_DIR))

# Import depends on the sys.path mutation above — the F73 detector lives
# outside the kairix package by design (repo-fitness script, not app code).
from check_no_private_infra_refs import (  # noqa: E402
    _PATTERNS,
    _is_in_scope,
    _scan_file,
)

pytestmark = pytest.mark.unit


def test_azure_vm_pattern_matches_only_known_name() -> None:
    pattern = dict(_PATTERNS)["azure-vm-name"]
    assert pattern.search("the vm-openclaw VM is running") is not None
    assert pattern.search("vm-other-name is configured") is None
    assert pattern.search("<your-vm-name>") is None


def test_azure_kv_pattern_matches_only_known_name() -> None:
    pattern = dict(_PATTERNS)["azure-kv-name"]
    assert pattern.search("KAIRIX_KV_NAME=kv-tc-agents") is not None
    assert pattern.search("KAIRIX_KV_NAME=<your-key-vault-name>") is None
    assert pattern.search("KAIRIX_KV_NAME=kv-example") is None  # pragma: allowlist secret


def test_internal_hostname_pattern_matches_threecubes_subdomain() -> None:
    pattern = dict(_PATTERNS)["internal-hostname"]
    assert pattern.search("ssh.threecubes.ai is reachable via Cloudflare") is not None
    assert pattern.search("dan@threecubes.io is a member") is not None
    assert pattern.search("example.com is the public placeholder") is None


def test_private_sibling_repo_pattern() -> None:
    pattern = dict(_PATTERNS)["private-sibling-repo"]
    assert pattern.search("see kairix-pro-platform ADR-017") is not None
    assert pattern.search("cross-pollinated from tc-agent-zone") is not None
    assert pattern.search("the two-scope architecture splits state") is None


def test_scan_file_returns_one_hit_per_match(tmp_path: Path) -> None:
    sample = tmp_path / "kairix" / "sample.py"
    sample.parent.mkdir(parents=True)
    sample.write_text(
        "# pretend kairix module\nimport os\nNAME = 'vm-openclaw'\nKV = 'kv-tc-agents'\n",
        encoding="utf-8",
    )
    hits = _scan_file(sample, "kairix/sample.py")
    assert len(hits) == 2
    assert any("vm-openclaw" in h for h in hits)
    assert any("kv-tc-agents" in h for h in hits)


def test_scan_file_skips_generic_placeholders(tmp_path: Path) -> None:
    sample = tmp_path / "kairix" / "sample.py"
    sample.parent.mkdir(parents=True)
    sample.write_text(
        "NAME = '<your-vm-name>'\nKV = '<your-key-vault-name>'\nHOST = 'example.com'\n",
        encoding="utf-8",
    )
    assert _scan_file(sample, "kairix/sample.py") == []


def test_in_scope_includes_production_code() -> None:
    assert _is_in_scope("kairix/worker.py")
    assert _is_in_scope("scripts/checks/check_anything.py")
    assert _is_in_scope("tests/unit/test_thing.py")
    assert _is_in_scope("docs/architecture/some-adr.md")
    assert _is_in_scope("CLAUDE.md")


def test_in_scope_excludes_vendored_and_unrelated_trees() -> None:
    assert not _is_in_scope("reference-library/some/citation.md")
    assert not _is_in_scope("benchmark-results/history/run-001.json")
    assert not _is_in_scope("kairix.egg-info/PKG-INFO")
