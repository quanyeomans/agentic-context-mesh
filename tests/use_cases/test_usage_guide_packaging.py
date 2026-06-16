"""Packaging-limb tests for the bundled usage guide (#466).

#466 root cause: the usage-guide markdown shipped only at the repo root
under ``docs/`` — never inside the ``kairix`` package — so it was absent
from the wheel and the Docker image. Every source checkout still had the
file on disk, so a pure source-tree test passed while production
returned ``UsageGuideNotFound``.

These tests assert the PACKAGING contract directly: the ``.md`` files are
discoverable as package data via :func:`importlib.resources.files`, the
same mechanism the production resolver uses. They do NOT touch
``docs/`` — so dropping the ``package-data`` glob in ``pyproject.toml``
(or removing the bundled files) makes them go red even though the
``docs/`` source tree is untouched.

Sabotage proof (PACKAGING limb, executed locally): remove the
``"kairix.agents.usage_guide" = ["data/*.md"]`` row from
``[tool.setuptools.package-data]`` and reinstall — an installed wheel no
longer ships the file, so
``resources.files("kairix.agents.usage_guide").joinpath("data/...")``
resolves to a path whose ``.is_file()`` is False and these assertions
fail. (In an editable/source install the file is found via the in-tree
package dir; the wheel-contents leg of the publish workflow is the
install-time guard. The import-resources assertion here is the
shadow-immune contract that the resolver depends on.)
"""

from __future__ import annotations

from importlib import resources

import pytest

pytestmark = pytest.mark.unit


_DATA_PACKAGE = "kairix.agents.usage_guide"
_MAIN_GUIDE = "data/agent-usage-guide.md"
_LATENCY_DOC = "data/MCP-LATENCY-EXPECTATIONS.md"


def test_main_guide_is_bundled_package_data() -> None:
    """The agent usage guide ships as package data under the kairix package.

    This is the shadow-immune location: it lives under site-packages in
    the image, never under the ``/var/lib/kairix`` named volume. The
    production resolver reads it here first.
    """
    resource = resources.files(_DATA_PACKAGE).joinpath(_MAIN_GUIDE)
    assert resource.is_file(), (
        f"{_DATA_PACKAGE}/{_MAIN_GUIDE} not discoverable as package data — "
        "the wheel/image will return UsageGuideNotFound (#466). "
        "Check [tool.setuptools.package-data] ships data/*.md."
    )
    text = resource.read_text(encoding="utf-8")
    assert text.strip(), "bundled guide is empty"


def test_latency_doc_is_bundled_package_data() -> None:
    """The dedicated mcp-latency doc also ships as package data.

    ``usage_guide('mcp-latency')`` routes to this file; it was equally
    missing from the image before #466.
    """
    resource = resources.files(_DATA_PACKAGE).joinpath(_LATENCY_DOC)
    assert resource.is_file(), (
        f"{_DATA_PACKAGE}/{_LATENCY_DOC} not discoverable as package data — "
        "usage_guide('mcp-latency') will return UsageGuideNotFound (#466)."
    )
    text = resource.read_text(encoding="utf-8")
    assert "p99" in text, "latency doc missing its per-tool p99 table"
