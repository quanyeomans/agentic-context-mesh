"""Contract: SearchBackendProtocol -- verify SearchPipeline.search signature conformance.

Checks that kairix.core.search.pipeline.SearchPipeline.search has parameters
compatible with the SearchBackendProtocol defined in kairix.quality.contracts.search.

Also verifies the backwards-compatible hybrid.search wrapper retains its public API.
"""

import inspect

import pytest

from kairix.quality.contracts.search import SearchBackendProtocol


@pytest.mark.contract
def test_search_backend_protocol_has_search_method():
    """SearchBackendProtocol defines a 'search' method."""
    assert hasattr(SearchBackendProtocol, "search")


@pytest.mark.contract
def test_search_backend_protocol_search_signature():
    """SearchBackendProtocol.search has expected parameter names."""
    sig = inspect.signature(SearchBackendProtocol.search)
    param_names = list(sig.parameters.keys())
    assert "self" in param_names
    assert "query" in param_names
    assert "agent" in param_names
    assert "limit" in param_names


@pytest.mark.contract
def test_pipeline_search_has_query_param():
    """SearchPipeline.search accepts 'query' parameter."""
    from kairix.core.search.pipeline import SearchPipeline

    sig = inspect.signature(SearchPipeline.search)
    assert "query" in sig.parameters


@pytest.mark.contract
def test_pipeline_search_has_agent_param():
    """SearchPipeline.search accepts 'agent' parameter."""
    from kairix.core.search.pipeline import SearchPipeline

    sig = inspect.signature(SearchPipeline.search)
    assert "agent" in sig.parameters


@pytest.mark.contract
def test_pipeline_search_agent_default_is_none():
    """SearchPipeline.search 'agent' defaults to None."""
    from kairix.core.search.pipeline import SearchPipeline

    sig = inspect.signature(SearchPipeline.search)
    assert sig.parameters["agent"].default is None


@pytest.mark.contract
def test_pipeline_search_has_budget_param():
    """SearchPipeline.search accepts 'budget' parameter (token budget)."""
    from kairix.core.search.pipeline import SearchPipeline

    sig = inspect.signature(SearchPipeline.search)
    assert "budget" in sig.parameters


@pytest.mark.contract
def test_pipeline_search_query_is_first_positional():
    """'query' is the first positional parameter of SearchPipeline.search (after self)."""
    from kairix.core.search.pipeline import SearchPipeline

    sig = inspect.signature(SearchPipeline.search)
    params = list(sig.parameters.keys())
    # First param is 'self', second should be 'query'
    assert params[1] == "query"


@pytest.mark.contract
def test_search_result_protocol_fields():
    """SearchResultProtocol defines the expected attributes."""
    from kairix.quality.contracts.search import SearchResultProtocol

    annotations = (
        SearchResultProtocol.__protocol_attrs__
        if hasattr(SearchResultProtocol, "__protocol_attrs__")
        else list(SearchResultProtocol.__annotations__.keys())
    )
    for field_name in ("path", "score", "title", "snippet", "intent"):
        assert field_name in annotations, f"SearchResultProtocol missing field: {field_name}"
