"""Step definitions for search_intents.feature."""

from pytest_bdd import given, parsers, then, when

from kairix.core.search.intent import QueryIntent, classify

# Module-level state (simple, test-scoped)
_state: dict = {}


@given("the kairix intent classifier is available")
def intent_classifier_available():
    """The intent classifier is a pure function — always available."""
    pass


@when(parsers.re(r'I classify the query "(?P<query>.*)"'))
def classify_query(query):
    _state["exception"] = None
    _state["result"] = None
    try:
        _state["result"] = classify(query)
    except Exception as exc:
        _state["exception"] = exc


@then(parsers.parse('the intent is "{intent}"'))
def check_intent(intent):
    assert _state["exception"] is None, f"classify raised: {_state['exception']}"
    result = _state["result"]
    assert result is not None
    result_str = result.value if hasattr(result, "value") else str(result)
    assert result_str == intent, f"Expected intent {intent!r}, got {result_str!r}"


@then("no exception is raised")
def no_exception_raised():
    assert _state["exception"] is None, f"classify raised: {_state['exception']}"


@then("the intent is a valid QueryIntent")
def intent_is_valid():
    result = _state["result"]
    assert isinstance(result, QueryIntent), f"Expected QueryIntent, got {type(result)}"
