"""Outcome tests for ``kairix benchmark install-corpus`` (#450, F30).

The reference-library corpus is NOT bundled in the wheel — ``install-corpus``
fetches it on demand, sha256-verified fail-closed. These tests inject a
downloader via ``BenchmarkCLIDeps`` so the real fetch → verify → extract
pipeline runs offline. No monkeypatch, no env mutation, no network
(F1/F2 clean): the injected downloader redirects extraction into a
``tmp_path`` tree so the live cache dir is never written.

Coverage:
- Direct ``cmd_install_corpus`` happy path → exit 0, corpus materialises.
- Corrupt download → exit 1 (sha256 fails closed), nothing extracted.
- ``--force`` / skip-when-present semantics through the production pipeline.
- The subprocess CLI surface resolves the subcommand (F30 presence) and
  fails closed when it has to hit the real (unreachable) network.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

from kairix.quality.benchmark.cli import BenchmarkCLIDeps, cmd_install_corpus
from kairix.quality.benchmark.corpus import (
    CorpusInstallError,
    FetchedCorpus,
    install_corpus,
    verify_corpus_sha256,
)
from tests.fakes import FakeCorpusDownloader, FakeCorpusSource


class _TmpRedirectDownloader:
    """Wraps ``FakeCorpusDownloader`` but extracts into a fixed ``tmp`` dir.

    The CLI computes the install dir from ``reference_corpus_install_dir()``
    (the real cache dir); redirecting into ``target`` keeps the test
    hermetic (writes only under ``tmp_path``) while still exercising the
    real verify/extract pipeline and recording the dir the CLI asked for.
    """

    def __init__(self, *, target: Path, corrupt: bool = False) -> None:
        self._inner = FakeCorpusDownloader(corrupt=corrupt)
        self._target = target
        self.requested_install_dir: Path | None = None
        self.requested_version: str | None = None
        self.requested_force: bool | None = None

    def __call__(self, *, install_dir: Path, version: str, url: str | None = None, force: bool = False) -> Path:
        self.requested_install_dir = install_dir
        self.requested_version = version
        self.requested_force = force
        return self._inner(install_dir=self._target, version=version, url=url, force=force)


def _args(*, version: str | None = None, url: str | None = None, force: bool = False) -> argparse.Namespace:
    return argparse.Namespace(version=version, url=url, force=force)


@pytest.mark.unit
def test_install_corpus_happy_path_materialises_corpus(tmp_path) -> None:
    """A clean install extracts the corpus and exits 0, landing it where the
    resolver reads (``install_dir == reference_corpus_install_dir()``)."""
    from kairix.paths import reference_corpus_install_dir

    target = tmp_path / "fetched"
    downloader = _TmpRedirectDownloader(target=target)
    deps = BenchmarkCLIDeps(download_corpus=downloader)

    rc = cmd_install_corpus(_args(version="2026.7.0"), deps=deps)

    assert rc == 0
    assert (target / "reference-library" / "CATALOGUE.md").is_file()
    # The CLI wired the requested version through, and aimed the install at
    # the dir the resolver will later read from — install/resolve coherence.
    assert downloader.requested_version == "2026.7.0"
    assert downloader.requested_install_dir == reference_corpus_install_dir()


@pytest.mark.unit
def test_install_corpus_corrupt_download_fails_closed(tmp_path) -> None:
    """A sha256 mismatch must exit non-zero and leave nothing extracted."""
    target = tmp_path / "fetched"
    downloader = _TmpRedirectDownloader(target=target, corrupt=True)
    deps = BenchmarkCLIDeps(download_corpus=downloader)

    rc = cmd_install_corpus(_args(version="2026.7.0"), deps=deps)

    assert rc == 1
    # Fail-closed: verification raises before extraction, so no corpus tree.
    assert not (target / "reference-library" / "CATALOGUE.md").exists()


@pytest.mark.unit
def test_install_corpus_force_passes_through(tmp_path) -> None:
    """``--force`` is threaded through to the downloader seam."""
    target = tmp_path / "fetched"
    downloader = _TmpRedirectDownloader(target=target)
    deps = BenchmarkCLIDeps(download_corpus=downloader)

    rc = cmd_install_corpus(_args(version="2026.7.0", force=True), deps=deps)

    assert rc == 0
    assert downloader.requested_force is True
    assert (target / "reference-library" / "CATALOGUE.md").is_file()


@pytest.mark.unit
def test_install_corpus_force_defaults_false_when_arg_absent(tmp_path) -> None:
    """When ``args`` carries no ``force`` attribute, ``cmd_install_corpus``
    defaults it to False (not True) via getattr — pins the getattr default
    that a True-flip mutation would break."""
    target = tmp_path / "fetched"
    downloader = _TmpRedirectDownloader(target=target)
    deps = BenchmarkCLIDeps(download_corpus=downloader)
    # Namespace intentionally OMITS ``force`` so the getattr default is used.
    args = argparse.Namespace(version="2026.7.0", url=None)

    rc = cmd_install_corpus(args, deps=deps)

    assert rc == 0
    assert downloader.requested_force is False


@pytest.mark.unit
def test_install_corpus_skips_when_present_without_force(tmp_path) -> None:
    """An already-populated corpus dir is left untouched without ``--force``."""
    target = tmp_path / "fetched"
    target.mkdir()
    (target / "existing.md").write_text("present")

    downloader = _TmpRedirectDownloader(target=target)
    rc = cmd_install_corpus(_args(version="2026.7.0"), deps=BenchmarkCLIDeps(download_corpus=downloader))

    assert rc == 0
    # The production install_corpus short-circuits when the dir has content
    # and force is False — the inner fake source was never asked for a version.
    assert downloader._inner.requested_version is None


@pytest.mark.unit
def test_verify_corpus_sha256_raises_on_mismatch() -> None:
    """The production fail-closed verify raises on a wrong hash."""
    with pytest.raises(CorpusInstallError, match="sha256 mismatch"):
        verify_corpus_sha256(b"some bytes", "0" * 64)


@pytest.mark.unit
def test_verify_corpus_sha256_passes_on_match() -> None:
    """Honest hash passes verification (no raise)."""
    import hashlib

    data = b"some bytes"
    verify_corpus_sha256(data, hashlib.sha256(data).hexdigest())  # must not raise


@pytest.mark.unit
def test_install_corpus_rejects_path_traversal_member(tmp_path) -> None:
    """A tarball with a ``..`` member is rejected even if the sha256 matches."""
    import hashlib
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        raw = b"evil"
        info = tarfile.TarInfo(name="../escape.md")
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
    data = buf.getvalue()

    class _TraversalSource:
        def fetch(self, *, version: str, url: str | None) -> FetchedCorpus:
            return FetchedCorpus(data=data, sha256=hashlib.sha256(data).hexdigest(), version=version, url="x")

    with pytest.raises(CorpusInstallError, match="unsafe path"):
        install_corpus(_TraversalSource(), install_dir=tmp_path / "c", version="1")


@pytest.mark.unit
def test_install_corpus_source_records_request() -> None:
    """The fake source records the version/url the pipeline asked for."""
    source = FakeCorpusSource()
    fetched = source.fetch(version="2026.7.0", url=None)
    assert source.requested_version == "2026.7.0"
    assert isinstance(fetched, FetchedCorpus)


@pytest.mark.unit
def test_install_corpus_default_force_extracts_into_fresh_dir(tmp_path) -> None:
    """``install_corpus`` with the DEFAULT ``force`` (omitted) extracts into a
    not-yet-created dir — pins the ``force=False`` default + the dir-creating
    extract path (mutation: ``force`` default flipped True would skip the
    present-dir short-circuit; ``exist_ok`` flipped False would crash here)."""
    source = FakeCorpusSource()
    dest = tmp_path / "fresh" / "corpus"  # parent does NOT exist yet

    result = install_corpus(source, install_dir=dest, version="2026.7.0")

    assert result == dest
    assert (dest / "reference-library" / "CATALOGUE.md").is_file()


@pytest.mark.unit
def test_install_corpus_default_force_skips_populated_dir(tmp_path) -> None:
    """With the DEFAULT ``force`` (omitted) and a populated dir, install
    short-circuits and never fetches — pins the ``and not force`` guard and
    the ``force=False`` default (a True-flipped default would re-fetch)."""
    source = FakeCorpusSource()
    dest = tmp_path / "corpus"
    dest.mkdir()
    (dest / "already.md").write_text("present")

    result = install_corpus(source, install_dir=dest, version="2026.7.0")

    assert result == dest
    # Short-circuited: the source was never asked for a version.
    assert source.requested_version is None


@pytest.mark.unit
def test_install_corpus_force_reextracts_into_existing_populated_dir(tmp_path) -> None:
    """``force=True`` over a populated dir re-fetches AND extracts into the
    existing dir — pins ``exist_ok=True`` (a False flip would raise
    FileExistsError on the already-present install dir)."""
    source = FakeCorpusSource()
    dest = tmp_path / "corpus"
    dest.mkdir()
    (dest / "stale.md").write_text("old")

    result = install_corpus(source, install_dir=dest, version="2026.7.0", force=True)

    assert result == dest
    assert source.requested_version == "2026.7.0"
    assert (dest / "reference-library" / "CATALOGUE.md").is_file()


@pytest.mark.unit
def test_corpus_asset_url_explicit_override_wins() -> None:
    """An explicit ``url`` is returned verbatim (no per-version template)."""
    from kairix.quality.benchmark.corpus import corpus_asset_url

    assert corpus_asset_url("2026.7.0", "https://mirror.example/corpus.tar.gz") == (
        "https://mirror.example/corpus.tar.gz"
    )


@pytest.mark.unit
def test_corpus_asset_url_defaults_to_release_asset_for_version() -> None:
    """With no override, the per-version GitHub release asset URL is built."""
    from kairix.quality.benchmark.corpus import corpus_asset_url

    built = corpus_asset_url("2026.7.0", None)
    assert built.endswith("/v2026.7.0/reference-library-v2026.7.0.tar.gz")
    assert "github.com" in built


@pytest.mark.unit
def test_default_download_corpus_default_force_skips_populated_dir_without_network(tmp_path) -> None:
    """``default_download_corpus`` with the DEFAULT ``force`` (omitted) over a
    populated dir short-circuits and never touches the network.

    This pins the production seam's ``force=False`` default: with the real
    default the populated-dir guard returns before ``_GithubReleaseCorpusSource``
    is asked to fetch (so no network is attempted and the call succeeds
    offline). A mutation flipping the default to ``True`` would skip the
    guard and try the live download — which fails here with no network —
    so the mutant is killed by this offline-success assertion.
    """
    from kairix.quality.benchmark.corpus import default_download_corpus

    dest = tmp_path / "corpus"
    dest.mkdir()
    (dest / "already.md").write_text("present")

    # No ``force`` arg → exercises the default. Returns the dir without any
    # network call because it is already populated.
    result = default_download_corpus(install_dir=dest, version="2026.7.0")

    assert result == dest
    assert (dest / "already.md").read_text() == "present"  # untouched, not re-fetched


@pytest.mark.unit
def test_run_reflib_without_corpus_emits_affordance(tmp_path, capsys) -> None:
    """``run --suite reflib`` with the corpus absent emits the F21 affordance
    pointing at install-corpus, instead of a bare FileNotFoundError.

    The missing-corpus state is injected via the ``corpus_root`` deps seam
    (F2-clean — no ``KAIRIX_REFLIB_ROOT`` mutation): the seam returns a path
    that doesn't exist, so the reference-library presence check fires.
    """
    from kairix.quality.benchmark.cli import cmd_run

    missing = tmp_path / "no-corpus-here"
    deps = BenchmarkCLIDeps(corpus_root=lambda: missing)
    args = argparse.Namespace(
        suite="reflib",
        system="hybrid",
        agent=None,
        collection=None,
        scope=None,
        categories=None,
        metrics=None,
        gates=False,
        baseline=None,
        fusion=None,
        output=None,
        mode="legacy",
    )

    rc = cmd_run(args, deps=deps)

    assert rc == 1
    err = capsys.readouterr().err
    assert "reference corpus not installed" in err
    assert "kairix benchmark install-corpus" in err


@pytest.mark.unit
def test_run_reflib_with_corpus_present_proceeds_past_check(tmp_path) -> None:
    """When the corpus IS present, the affordance does NOT fire — the run
    proceeds to the injected runner (both-branch coverage of the check).
    """
    from kairix.quality.benchmark.cli import cmd_run
    from kairix.quality.benchmark.runner import BenchmarkResult

    present = tmp_path / "corpus"
    present.mkdir()
    captured: dict = {}

    def _fake_runner(**kwargs):
        captured.update(kwargs)
        return BenchmarkResult(
            meta={"system": "mock-reflib", "agent": None, "date": "2026-06-16"},
            summary={"weighted_total": 0.9, "category_scores": {}},
            diagnostics={},
            cases=[],
        )

    deps = BenchmarkCLIDeps(corpus_root=lambda: present, run_benchmark=_fake_runner)
    args = argparse.Namespace(
        suite="reflib",
        system="mock-reflib",
        agent=None,
        collection=None,
        scope=None,
        categories=None,
        metrics=None,
        gates=False,
        baseline=None,
        fusion=None,
        output=None,
        mode="legacy",
    )

    rc = cmd_run(args, deps=deps)

    assert rc == 0
    # The runner was reached with the reference-library collection — proof the
    # presence check passed instead of short-circuiting to the affordance.
    assert captured["collection"] == "reference-library"


@pytest.mark.unit
def test_suites_ship_as_package_data() -> None:
    """PACKAGING LIMB (#450): the suites resolve via importlib.resources from
    the ``kairix.data`` package — the in-wheel guarantee.

    A source-tree path test passes even when packaging is broken; this
    asserts the suites are reachable through the package's resource API,
    which is how a pip-installed wheel resolves them. The full
    clean-venv wheel-install proof runs in CI / the sabotage log.
    """
    from importlib.resources import files

    suites = files("kairix.data").joinpath("suites")
    assert suites.joinpath("reflib-gold-v3.yaml").is_file()
    assert suites.joinpath("contract-suite.yaml").is_file()
    assert suites.joinpath("perf/budgets.json").is_file()


@pytest.mark.unit
def test_install_corpus_subprocess_subcommand_is_wired() -> None:
    """F30 subprocess surface: the subcommand resolves and fails closed.

    The subprocess path can't inject deps, so it falls through to the real
    network source against an unreachable forced URL — proving the
    subcommand is dispatched (not an 'unknown subcommand' error) and that
    it fails closed (exit 1) rather than hanging or crashing. The deps-
    injected cases above cover the success/verify behaviour.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "benchmark",
            "install-corpus",
            "--version",
            "0.0.0-test",
            "--url",
            "file:///nonexistent/reference-library-0.0.0-test.tar.gz",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 1, proc.stderr
    assert "corpus install failed" in proc.stderr
    assert "kairix benchmark install-corpus" in proc.stderr
