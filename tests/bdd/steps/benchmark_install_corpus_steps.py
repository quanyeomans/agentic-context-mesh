"""Step definitions for benchmark_install_corpus.feature (#450, F45).

Composes through the public benchmark CLI ``main(argv=..., deps=...)``
(F46) with a corpus downloader injected via ``BenchmarkCLIDeps`` (F1/F2 —
no monkeypatch, no env mutation, no network). The injected downloader
runs the REAL fetch → verify → extract pipeline over an in-memory source
(tests/fakes.FakeCorpusDownloader), redirected into a ``tmp_path`` tree so
the scenario stays hermetic and never writes the live cache dir.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pytest_bdd import given, then, when

from kairix.quality.benchmark.cli import BenchmarkCLIDeps, main
from tests.fakes import FakeCorpusDownloader

_state: dict[str, Any] = {}


class _TmpRedirectDownloader:
    """Wraps ``FakeCorpusDownloader`` but extracts into a fixed tmp dir.

    The CLI computes the install dir from ``reference_corpus_install_dir()``
    (the real cache dir); to keep the BDD scenario hermetic we redirect the
    extraction into ``target`` while still exercising the real
    verify/extract pipeline and recording the dir the CLI asked for so the
    'install lands where resolution reads' coherence can be asserted.
    """

    def __init__(self, *, target: Path, corrupt: bool = False) -> None:
        self._inner = FakeCorpusDownloader(corrupt=corrupt)
        self._target = target
        self.requested_install_dir: Path | None = None

    def __call__(self, *, install_dir: Path, version: str, url: str | None = None, force: bool = False) -> Path:
        self.requested_install_dir = install_dir
        return self._inner(install_dir=self._target, version=version, url=url, force=force)


@given("a pip-installed kairix with no reference corpus")
def no_reference_corpus(tmp_path: Path) -> None:
    _state.clear()
    _state["target"] = tmp_path / "fetched-corpus"


@when("the operator runs kairix benchmark install-corpus")
def run_install_corpus(capsys) -> None:
    downloader = _TmpRedirectDownloader(target=_state["target"])
    _state["downloader"] = downloader
    _state["rc"] = main(["install-corpus"], deps=BenchmarkCLIDeps(download_corpus=downloader))
    captured = capsys.readouterr()
    _state["out"] = captured.out
    _state["err"] = captured.err


@when("the operator runs kairix benchmark install-corpus with a corrupt download")
def run_install_corpus_corrupt(capsys) -> None:
    downloader = _TmpRedirectDownloader(target=_state["target"], corrupt=True)
    _state["downloader"] = downloader
    _state["rc"] = main(["install-corpus"], deps=BenchmarkCLIDeps(download_corpus=downloader))
    captured = capsys.readouterr()
    _state["out"] = captured.out
    _state["err"] = captured.err


@then("the corpus is fetched and verified")
def corpus_fetched_and_verified() -> None:
    target: Path = _state["target"]
    assert (target / "reference-library" / "CATALOGUE.md").is_file()


@then("the install command reports success")
def install_reports_success() -> None:
    assert _state["rc"] == 0
    assert "reference corpus installed" in _state["out"]


@then("the reflib suite can resolve the installed corpus")
def reflib_resolves_corpus() -> None:
    from kairix.paths import reference_corpus_install_dir

    # The CLI asked the downloader to install at exactly the dir the
    # resolver reads from — install lands where the next run looks.
    assert _state["downloader"].requested_install_dir == reference_corpus_install_dir()


@then("the install command fails closed")
def install_fails_closed() -> None:
    assert _state["rc"] == 1
    assert "corpus install failed" in _state["err"]


@then("no corpus is left installed")
def no_corpus_left() -> None:
    target: Path = _state["target"]
    assert not (target / "reference-library" / "CATALOGUE.md").exists()
