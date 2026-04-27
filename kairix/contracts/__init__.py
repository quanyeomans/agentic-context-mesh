"""kairix.contracts — Domain Protocol definitions.

Each Protocol represents the agreed interface between bounded contexts.
Import these in tests with @pytest.mark.contract to verify conformance.
"""

from kairix.contracts.briefing import BriefingSourceProtocol
from kairix.contracts.embed import EmbedderProtocol
from kairix.contracts.entities import EntityResolverProtocol
from kairix.contracts.search import SearchBackendProtocol, SearchResultProtocol

__all__ = [
    "BriefingSourceProtocol",
    "EmbedderProtocol",
    "EntityResolverProtocol",
    "SearchBackendProtocol",
    "SearchResultProtocol",
]
