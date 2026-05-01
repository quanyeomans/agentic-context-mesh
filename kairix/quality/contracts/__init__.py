"""kairix.quality.contracts — Domain Protocol definitions.

Each Protocol represents the agreed interface between bounded contexts.
Import these in tests with @pytest.mark.contract to verify conformance.
"""

from kairix.quality.contracts.briefing import BriefingSourceProtocol
from kairix.quality.contracts.embed import EmbedderProtocol
from kairix.quality.contracts.entities import EntityResolverProtocol
from kairix.quality.contracts.search import SearchBackendProtocol, SearchResultProtocol

__all__ = [
    "BriefingSourceProtocol",
    "EmbedderProtocol",
    "EntityResolverProtocol",
    "SearchBackendProtocol",
    "SearchResultProtocol",
]
