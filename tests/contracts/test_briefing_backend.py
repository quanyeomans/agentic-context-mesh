"""Contract: BriefingSourceProtocol — verify briefing pipeline conformance.

Checks that:
  - BriefingSourceProtocol defines the expected interface
  - kairix.agents.briefing.pipeline.generate_briefing exists and is callable
  - A minimal stub implementing BriefingSourceProtocol satisfies the protocol
"""

import inspect

import pytest

from kairix.quality.contracts.briefing import BriefingSourceProtocol


@pytest.mark.contract
def test_briefing_source_protocol_has_fetch():
    """BriefingSourceProtocol defines a 'fetch' method."""
    assert hasattr(BriefingSourceProtocol, "fetch")


@pytest.mark.contract
def test_briefing_source_protocol_fetch_signature():
    """BriefingSourceProtocol.fetch has expected parameters: agent, limit."""
    sig = inspect.signature(BriefingSourceProtocol.fetch)
    param_names = list(sig.parameters.keys())
    assert "self" in param_names
    assert "agent" in param_names
    assert "limit" in param_names


@pytest.mark.contract
def test_briefing_source_protocol_fetch_limit_default():
    """BriefingSourceProtocol.fetch 'limit' defaults to 10."""
    sig = inspect.signature(BriefingSourceProtocol.fetch)
    assert sig.parameters["limit"].default == 10


@pytest.mark.contract
def test_briefing_source_protocol_is_runtime_checkable():
    """BriefingSourceProtocol is @runtime_checkable."""

    class StubSource:
        def fetch(self, agent: str, limit: int = 10) -> list[dict]:
            return [{"title": "Test", "body": "content"}]

    assert isinstance(StubSource(), BriefingSourceProtocol)


@pytest.mark.contract
def test_non_conforming_class_fails_protocol():
    """A class without fetch() does not satisfy BriefingSourceProtocol."""

    class BadSource:
        pass

    assert not isinstance(BadSource(), BriefingSourceProtocol)


@pytest.mark.contract
def test_generate_briefing_is_callable():
    """kairix.agents.briefing.pipeline.generate_briefing exists and is callable."""
    from kairix.agents.briefing.pipeline import generate_briefing

    assert callable(generate_briefing)


@pytest.mark.contract
def test_generate_briefing_accepts_agent_param():
    """generate_briefing accepts an 'agent' parameter."""
    from kairix.agents.briefing.pipeline import generate_briefing

    sig = inspect.signature(generate_briefing)
    assert "agent" in sig.parameters
