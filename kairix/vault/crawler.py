"""Backwards compatibility shim — kairix.vault.crawler moved to kairix.store.crawler."""

import warnings

warnings.warn(
    "kairix.vault.crawler is deprecated, use kairix.store.crawler instead",
    DeprecationWarning,
    stacklevel=2,
)
from kairix.store.crawler import *  # noqa: E402, F403
from kairix.store.crawler import (  # noqa: E402, F401 — explicit re-exports (incl. private helpers used by tests)
    CrawlReport,
    _as_list,
    _find_people_dirs,
    _parse_frontmatter,
    _resolve_org_id,
    _to_display_name,
    _to_slug,
    crawl,
)
