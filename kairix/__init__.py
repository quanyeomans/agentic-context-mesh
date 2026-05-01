"""kairix — Shared knowledge layer for human-agent teams."""

try:
    from importlib.metadata import version

    __version__ = version("kairix")
except Exception:
    __version__ = "0.0.0"  # fallback for editable installs without metadata

__all__ = ["QueryIntent", "RetrievalConfig", "SearchResult", "__version__", "hybrid_search"]

# Public API surface — guarded so the package loads even when optional deps
# (e.g. neo4j) are missing.
# Note: search() is aliased to hybrid_search to avoid shadowing the
# kairix.core.search subpackage in sys.modules.
try:
    from kairix.core.search.hybrid import SearchResult
    from kairix.core.search.hybrid import search as hybrid_search
except ImportError:
    pass

try:
    from kairix.core.search.config import RetrievalConfig
except ImportError:
    pass

try:
    from kairix.core.search.intent import QueryIntent
except ImportError:
    pass
