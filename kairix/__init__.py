"""kairix — Self-contained knowledge retrieval engine."""

__version__ = "2026.4.24a3"

__all__ = ["QueryIntent", "RetrievalConfig", "SearchResult", "__version__", "hybrid_search"]

# Public API surface — guarded so the package loads even when optional deps
# (e.g. sqlite-vec, neo4j) are missing.
# Note: search() is aliased to hybrid_search to avoid shadowing the
# kairix.search subpackage in sys.modules.
try:
    from kairix.search.hybrid import SearchResult
    from kairix.search.hybrid import search as hybrid_search
except ImportError:
    pass

try:
    from kairix.search.config import RetrievalConfig
except ImportError:
    pass

try:
    from kairix.search.intent import QueryIntent
except ImportError:
    pass
