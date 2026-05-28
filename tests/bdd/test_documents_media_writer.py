"""pytest-bdd binding for documents_media_writer.feature (GH #336 / ADR-024 Bundle B).

Steps live in :mod:`tests.bdd.steps.documents_media_writer_steps`.

The scenarios exercise the real :class:`kairix.core.connectors.pipeline.ConnectorPipeline`
through :func:`kairix.core.factory.build_connector_pipeline` (F46 /
F47 compliant). Three branches:

* happy_path  — extractor returns + quality_ok True  -> status='ok'
* failure     — extractor raises                     -> status='failed' AND dead-letter
* unsupported — extractor returns + quality_ok False -> status='unsupported'
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

from tests.bdd.steps import documents_media_writer_steps  # noqa: F401  # registers step impls with pytest-bdd

FEATURE = str(Path(__file__).parent / "features" / "documents_media_writer.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
