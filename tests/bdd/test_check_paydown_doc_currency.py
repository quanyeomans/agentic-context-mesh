"""pytest-bdd test module for check_paydown_doc_currency.feature.

KFEAT-018 — pins the snapshot-currency gate so accidental removal or
refactor trips the suite.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

from tests.bdd.steps import check_paydown_doc_currency_steps  # noqa: F401

FEATURE = str(Path(__file__).parent / "features" / "check_paydown_doc_currency.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
