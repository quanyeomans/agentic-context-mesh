"""F68 failure-injection contract test for :class:`CorpusSource` (#450).

  * ``fetch`` → ``raises`` (the release asset is unreachable / the network
    layer errors — a ``urllib.error.URLError`` in production).

The fake source (``tests/fakes.FakeCorpusSource``) injects the failure via
``raise_on_fetch``; the production ``install_corpus`` pipeline must let that
exception propagate rather than swallow it (a swallowed fetch error would
silently leave the corpus uninstalled). The CLI layer (``cmd_install_corpus``)
catches it and renders the F21 affordance — that mapping is covered in
``tests/benchmark/test_install_corpus_cli.py``.
"""

from __future__ import annotations

import pytest

from kairix.quality.benchmark.corpus import install_corpus
from tests.fakes import FakeCorpusSource

pytestmark = pytest.mark.contract


# F43-single-impl: the real CorpusSource (_GithubReleaseCorpusSource) fetches
# the release asset over the network via urllib, so it cannot run in a
# contract/unit body without a live download. The fake injects the same
# network-layer failure (raise_on_fetch); there is no offline real-impl
# analogue to co-assert against, so this raises-shape probe is genuinely
# fake-only. The real fetch path is exercised by the CI install proof.
def test_fetch_raises_propagates_to_install_caller(tmp_path) -> None:
    """``fetch`` raising a network error propagates out of ``install_corpus``.

    The pipeline does not swallow a fetch failure — the caller learns the
    corpus was not installed instead of getting a false success.
    """
    boom = OSError("release asset unreachable")
    source = FakeCorpusSource(raise_on_fetch=boom)

    with pytest.raises(OSError, match="release asset unreachable"):
        install_corpus(source, install_dir=tmp_path / "corpus", version="2026.7.0")

    # Nothing was extracted — the install dir holds no corpus tree.
    assert not (tmp_path / "corpus" / "reference-library").exists()
