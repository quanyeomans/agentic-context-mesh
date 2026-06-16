"""Reference-library corpus fetch + verify + extract (#450).

The reference-library corpus (~50 MB, mixed-license) is too large and
too license-encumbered to bundle inside the wheel. ``kairix benchmark
install-corpus`` fetches it on demand from the GitHub release asset for
the installed kairix version, verifies it against the published sha256
(fail-closed on mismatch), and extracts it under
:func:`kairix.paths.reference_corpus_install_dir`.

The network fetch is isolated behind the ``CorpusSource`` protocol so
tests inject a fake (``tests.fakes.FakeCorpusDownloader``) that returns
crafted bytes + sha256 without hitting the network — the sha256
verification below is the production code the fail-closed test exercises
(it must raise when the fetched bytes don't match the advertised hash).
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class CorpusInstallError(RuntimeError):
    """Raised when corpus install fails closed (sha256 mismatch, bad tarball, …)."""


@dataclass(frozen=True)
class FetchedCorpus:
    """One corpus fetch result — the raw tarball bytes + its advertised sha256.

    ``data`` is the gzip-compressed tar payload. ``sha256`` is the hash
    the *source* advertises for that payload (from the published
    ``<asset>.sha256`` sidecar). :func:`install_corpus` recomputes the
    hash over ``data`` and fails closed if the two disagree — so a
    truncated download or a tampered asset never extracts.
    """

    data: bytes
    sha256: str
    version: str
    url: str


class CorpusSource(Protocol):
    """Network seam for the corpus fetch — fetches the tarball + its sha256.

    Production wiring (:func:`default_download_corpus`) talks to the
    GitHub release asset; tests inject ``FakeCorpusDownloader`` so the
    extract + verify pipeline runs without a live download. Implementations
    return a :class:`FetchedCorpus`; they do NOT verify the hash
    themselves — :func:`install_corpus` owns the fail-closed check.
    """

    def fetch(self, *, version: str, url: str | None) -> FetchedCorpus:
        """Return the tarball bytes + advertised sha256 for ``version``."""
        ...


def verify_corpus_sha256(data: bytes, expected_sha256: str) -> None:
    """Fail closed when ``data``'s sha256 doesn't match ``expected_sha256``.

    Raises :class:`CorpusInstallError` on mismatch — the caller never
    extracts an unverified tarball. Pure function so the corrupt-download
    test drives it without any network or filesystem.
    """
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise CorpusInstallError(
            "corpus sha256 mismatch — refusing to extract. "
            f"expected {expected_sha256}, computed {actual}. "
            "fix: re-run with a clean download (the asset may be truncated or tampered). "
            "next: kairix benchmark install-corpus --force. "
            "run: verify the published <asset>.sha256 sidecar on the GitHub release."
        )


def _extract_tarball(data: bytes, install_dir: Path) -> None:
    """Extract the verified gzip tarball into ``install_dir`` (created if absent).

    Guards against path-traversal entries (``..`` / absolute paths) — a
    malicious tar must not write outside ``install_dir`` even though the
    sha256 already gates tampering.
    """
    install_dir.mkdir(parents=True, exist_ok=True)
    resolved_root = install_dir.resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            target = (install_dir / member.name).resolve()
            if not str(target).startswith(str(resolved_root)):
                raise CorpusInstallError(
                    f"corpus tarball contains an unsafe path {member.name!r}; "
                    "refusing to extract outside the install dir."
                )
        # filter="data" (PEP 706) strips unsafe members (absolute paths,
        # traversal, device files) — defence in depth on top of the explicit
        # member check above. Required on 3.14+ where the default is deprecated.
        tar.extractall(install_dir, filter="data")


def install_corpus(
    source: CorpusSource,
    *,
    install_dir: Path,
    version: str,
    url: str | None = None,
    force: bool = False,
) -> Path:
    """Fetch → verify (fail-closed) → extract the reference corpus.

    Returns the ``install_dir`` on success. When the corpus is already
    present and ``force`` is False, returns early without re-fetching.

    Raises :class:`CorpusInstallError` on a sha256 mismatch or an unsafe
    tarball — the install fails closed and ``install_dir`` is left without
    a partial extraction (extraction only happens after verification).
    """
    if install_dir.is_dir() and any(install_dir.iterdir()) and not force:
        return install_dir
    fetched = source.fetch(version=version, url=url)
    verify_corpus_sha256(fetched.data, fetched.sha256)
    _extract_tarball(fetched.data, install_dir)
    return install_dir


# ---------------------------------------------------------------------------
# Production network source — the _default_* seam (#450). Kept thin + visible
# to the coverage floor (F86: no ``# pragma: no cover``); the heavy network
# call is the only line tests don't drive, exercised by the CI install proof.
# ---------------------------------------------------------------------------


_RELEASE_ASSET_URL = (
    "https://github.com/three-cubes/kairix/releases/download/v{version}/reference-library-v{version}.tar.gz"
)


def corpus_asset_url(version: str, url: str | None) -> str:
    """Resolve the corpus tarball URL: the explicit ``url`` override wins,
    else the per-version GitHub release asset.

    Pure (no network) so the override-vs-default precedence is unit-testable
    without a live download. An explicit ``url`` is returned verbatim — the
    operator pointing at a mirror or a pre-staged asset must not have the
    per-version template applied on top of it.
    """
    if url:
        return url
    return _RELEASE_ASSET_URL.format(version=version)


class _GithubReleaseCorpusSource:
    """Production :class:`CorpusSource` — downloads the GitHub release asset.

    Fetches both the ``.tar.gz`` and its ``.sha256`` sidecar via
    ``urllib`` (no extra dependency). Isolated in this class so the
    network call is the only untested line; the URL resolution
    (:func:`corpus_asset_url`) + the verify + extract logic in
    :func:`install_corpus` are fully driven by the fake-injected tests.
    """

    # pragma rationale (F3): live network path — urllib download of the
    # GitHub release asset. Exercised by the CI clean-install proof, not
    # unit tests; the URL resolution (corpus_asset_url) + verify + extract
    # logic it delegates to is fully unit-covered.
    def fetch(self, *, version: str, url: str | None) -> FetchedCorpus:  # pragma: no cover (F3 rationale above)
        import urllib.request

        asset_url = corpus_asset_url(version, url)
        sha_url = f"{asset_url}.sha256"
        with urllib.request.urlopen(asset_url) as resp:  # noqa: S310 — fixed https GitHub release host
            data = resp.read()
        with urllib.request.urlopen(sha_url) as resp:  # noqa: S310 — fixed https GitHub release host
            sha_text = resp.read().decode("utf-8").strip().split()[0]
        return FetchedCorpus(data=data, sha256=sha_text, version=version, url=asset_url)


def default_download_corpus(
    *,
    install_dir: Path,
    version: str,
    url: str | None = None,
    force: bool = False,
) -> Path:
    """Production corpus installer seam (#450) — fetch from the GitHub release.

    Wires the production :class:`_GithubReleaseCorpusSource` into the
    shared :func:`install_corpus` pipeline. This is the ``_default_*``
    seam ``BenchmarkCLIDeps.download_corpus`` defaults to; tests inject
    ``FakeCorpusDownloader`` instead so the verify + extract path runs
    offline. Stays visible to the coverage floor (F86 — no pragma).
    """
    return install_corpus(
        _GithubReleaseCorpusSource(),
        install_dir=install_dir,
        version=version,
        url=url,
        force=force,
    )
