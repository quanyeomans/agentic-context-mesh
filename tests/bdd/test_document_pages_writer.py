"""pytest-bdd binding for document_pages_writer.feature (GH #338 / ADR-024 F70 paydown).

Steps live in :mod:`tests.bdd.steps.document_pages_writer_steps`.

The scenarios exercise the real :class:`kairix.core.connectors.pipeline.ConnectorPipeline`
through :func:`kairix.core.factory.build_connector_pipeline` (F46 /
F47 compliant) and assert directly against the ``document_pages``
table after one (or two, for re-ingest) batch(es).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

from tests.bdd.steps import document_pages_writer_steps  # noqa: F401  # registers step impls

FEATURE = str(Path(__file__).parent / "features" / "document_pages_writer.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
