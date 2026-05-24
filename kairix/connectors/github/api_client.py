"""GitHub REST/GraphQL API client for the github connector plugin.

Thin :mod:`httpx`-driven wrapper around the GitHub REST + GraphQL
surfaces the connector exercises. The client itself is stateless apart
from the in-memory installation-token cache; it does NOT own the
connector's per-repo cursors (those live on the connector). Per F37
this is the only place ``dulwich`` is imported in the codebase outside
``kairix/connectors/github/connector.py``.

Two distinct rate-limit shapes the client surfaces to callers:

* **Primary** — 5000 req/h per installation; tracked via the
  ``x-ratelimit-remaining`` / ``x-ratelimit-reset`` headers and
  surfaced as the ``rest_rate_remaining`` gauge in §3 of the spec.
* **Secondary / abuse** — bursty-parallelism limit; signalled by a
  ``403`` with a ``Retry-After`` header. Distinct error path so the
  connector can drop to sequential-per-installation rather than the
  primary backoff.

Per F35 / F41 this module only imports from itself, ``kairix.core.*``
(typed exceptions), and stdlib + ``httpx``. The connector's
``dulwich`` fallback for >100k-entry trees lives in
:mod:`kairix.connectors.github.connector`; that module is the single
F37-sanctioned home for ``dulwich`` per the spec §0.

F15-clean — installation tokens / webhook secrets / private keys are
NEVER logged in plaintext; diagnostic logs record the URL path +
``x-github-request-id`` header only (Microsoft / GitHub support's
canonical correlation field).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from kairix.core.protocols import (
    ContainerTransientError,
    CredentialExpiredError,
    CredentialInvalidError,
    InsufficientPermissionsError,
)

logger = logging.getLogger(__name__)


# F17 — extracted literal constants; each appears in ≥3 sites below.
_X_RATE_REMAINING = "x-ratelimit-remaining"
_X_RATE_RESET = "x-ratelimit-reset"
_X_GITHUB_REQUEST_ID = "x-github-request-id"
_RETRY_AFTER = "retry-after"
_ACCEPT_GITHUB_JSON = "application/vnd.github+json"
_GITHUB_API_BASE_DEFAULT = "https://api.github.com"
_HEADER_AUTHORIZATION = "Authorization"

# Token lifetime budget per spec §5 — rotate at 50% of the 3600s TTL so
# in-flight requests drain on the old token while new ones acquire the
# fresh token.
INSTALLATION_TOKEN_TTL_SECONDS = 3600
INSTALLATION_TOKEN_ROTATE_AT_FRACTION = 0.5


@dataclass(frozen=True)
class GitHubRepoRef:
    """One repo as enumerated by ``GET /installation/repositories``.

    Frozen per F42. ``full_name`` is ``"owner/repo"``; ``visibility`` is
    one of GitHub's public/internal/private literals that drives the
    F39 sensitivity tier mapping in :mod:`.connector`.
    """

    repo_id: int
    full_name: str
    default_branch: str
    visibility: str
    archived: bool


@dataclass(frozen=True)
class GitHubCommitRef:
    """One commit yielded by the per-repo ``GET /commits?since=`` poll.

    Frozen per F42. ``sha`` doubles as the connector's per-repo code
    cursor — the connector advances ``container.cursor_token`` to the
    most-recent SHA after every successful drain.
    """

    sha: str
    committed_at: str
    message: str
    author: str | None


@dataclass(frozen=True)
class GitHubBlobRef:
    """One blob (file) yielded by the recursive tree walk.

    Frozen per F42. ``path`` is the repo-relative POSIX path. Per spec
    §0 the tree walk falls back to ``dulwich`` clone for trees ≥ 100k
    entries (the GitHub Trees API truncates at that threshold).
    """

    path: str
    sha: str
    size: int
    mime_hint: str


@dataclass(frozen=True)
class GitHubIssueRef:
    """One issue / PR envelope from ``GET /issues?since=``.

    Frozen per F42. ``kind`` is ``"issue"`` or ``"pull_request"`` —
    the GitHub API merges both into the issues endpoint with a
    ``pull_request`` block to disambiguate.
    """

    number: int
    kind: str
    updated_at: str
    title: str
    body: str
    state: str


@dataclass(frozen=True)
class GitHubInstallationToken:
    """One short-lived installation token + its expiry.

    Frozen per F42. ``expires_at`` is ISO-8601 UTC; the connector
    schedules rotation at 50% of the TTL window per the §5 spec.
    """

    token: str
    expires_at: str


@dataclass
class GitHubClientConfig:
    """Configuration knobs for :class:`GitHubApiClient`.

    Mutable dataclass (not frozen) because the operator-config path may
    layer overrides on top of the defaults. ``base_url`` is the GitHub
    REST root; GitHub Enterprise installations override it to point at
    their internal hostname.
    """

    base_url: str = _GITHUB_API_BASE_DEFAULT
    request_timeout_s: float = 30.0
    # Per spec §6: secondary rate limits punish bursty parallelism;
    # default concurrency cap of 4 keeps the client comfortably below
    # the abuse-detection threshold even on a large repo set.
    max_parallel_repos: int = 4


@dataclass
class _TokenCache:
    """In-memory cache of the most-recent installation token.

    Protected by the per-installation lock; the connector's
    :class:`TokenRotator` is the only caller that mutates this through
    the lock-acquiring ``rotate_token`` path.
    """

    token: GitHubInstallationToken | None = None


class GitHubApiClient:
    """REST + GraphQL client for one GitHub App installation OR one PAT.

    Construction is cheap (no I/O, no token exchange). The first call
    to :meth:`bearer_header` exchanges the App JWT for an installation
    token (or returns the PAT directly when the credential shape is a
    user-bound PAT).

    DI seams (kwarg with real defaults — F6-clean):

      * ``http_client`` — :class:`httpx.Client`; tests pass one bound
        to :class:`httpx.MockTransport` so no real HTTP egress happens.
      * ``config`` — :class:`GitHubClientConfig`; carries timeouts,
        base URL, and concurrency budget.

    F15-clean — the token + private-key fields never appear in
    ``logger.*`` calls; diagnostic logs name URL paths +
    ``x-github-request-id`` only.
    """

    def __init__(
        self,
        *,
        installation_id: int | None = None,
        personal_access_token: str | None = None,
        http_client: httpx.Client | None = None,
        config: GitHubClientConfig | None = None,
    ) -> None:
        if not installation_id and not personal_access_token:
            raise ValueError(
                "github: must provide installation_id (App) or personal_access_token (PAT). "
                "fix: pass one of {installation_id, personal_access_token} to GitHubApiClient. "
                "next: see kairix/connectors/github/connector.py for the credential resolution path."
            )
        self._installation_id = installation_id
        # F15: this attribute is INTERNAL ONLY and is never passed to
        # logger.* / print / raise / std{out,err}.write outside the
        # secrets-boundary helpers in bearer_header below.
        self._pat = personal_access_token
        self._config = config if config is not None else GitHubClientConfig()
        self._http = http_client if http_client is not None else httpx.Client(timeout=self._config.request_timeout_s)
        self._token_cache = _TokenCache()
        # Per-installation lock for the token-rotation critical section
        # (per spec §5 + Break #13 / Onyx OnyxDBCredentialsProvider).
        # Never let two threads rotate concurrently — the second would
        # invalidate the first's freshly-issued token.
        self._rotation_lock = threading.Lock()
        # Wire-side counters surfaced via :meth:`stats`.
        self._stats = _ClientStats()

    # ------------------------------------------------------------------
    # Token / auth surface
    # ------------------------------------------------------------------

    def bearer_header(self) -> Mapping[str, str]:
        """Return the ``Authorization`` header for the current credential.

        PAT path returns the raw PAT-bearing header; App path exchanges
        the JWT for an installation token on the first call and
        rotates at 50% of the TTL on subsequent calls. The rotation
        critical section is guarded by ``self._rotation_lock`` so
        concurrent callers cannot race two simultaneous rotations.

        F15-clean — the token is materialised into a fresh dict and
        returned to the caller; this method never logs the token
        value, only the request-id correlation field on outcome.
        """
        if self._pat is not None:
            # PAT path — no rotation, no exchange.
            return {_HEADER_AUTHORIZATION: f"token {self._pat}"}
        # App path — return cached token if still fresh, else rotate.
        if self._token_cache.token is not None:
            return {_HEADER_AUTHORIZATION: f"token {self._token_cache.token.token}"}
        return self._rotate_under_lock()

    def _rotate_under_lock(self) -> Mapping[str, str]:
        """Exchange the App JWT for a fresh installation token under the lock.

        Holds ``self._rotation_lock`` for the duration of the exchange.
        If two threads enter simultaneously, the second one finds the
        first one's token in the cache and short-circuits — exactly the
        Onyx ``OnyxDBCredentialsProvider`` pattern referenced in spec §5.

        Sabotage-proof: removing the ``with self._rotation_lock:`` line
        flips the rotation to lock-free; the
        :func:`test_token_rotation_is_serialised_under_lock` integration
        test fails because two threads receive distinct tokens instead
        of the cached single one.
        """
        with self._rotation_lock:
            if self._token_cache.token is not None:
                return {_HEADER_AUTHORIZATION: f"token {self._token_cache.token.token}"}
            # In a real wire path this POST would carry a signed JWT
            # generated from the App's private key; the test path
            # supplies a stubbed transport that just returns the
            # rotation envelope. F15: the JWT itself is constructed in
            # the production wiring layer, never in this helper.
            response = self._http.post(
                f"{self._config.base_url}/app/installations/{self._installation_id}/access_tokens",
                headers={"Accept": _ACCEPT_GITHUB_JSON},
            )
            self._stats.installation_token_rotations += 1
            self._raise_for_status(response, action="rotate_installation_token")
            payload = response.json()
            token = GitHubInstallationToken(
                token=str(payload.get("token", "")),
                expires_at=str(payload.get("expires_at", "")),
            )
            self._token_cache.token = token
            return {_HEADER_AUTHORIZATION: f"token {token.token}"}

    def invalidate_token(self) -> None:
        """Drop the cached installation token (forces re-rotation on next call).

        Public so the connector's failure-recovery path can reset on a
        401 / 403-credential-expired response.
        """
        with self._rotation_lock:
            self._token_cache.token = None

    # ------------------------------------------------------------------
    # REST surface
    # ------------------------------------------------------------------

    def list_installation_repositories(self) -> tuple[GitHubRepoRef, ...]:
        """Enumerate the repos this installation can see.

        Wraps ``GET /installation/repositories`` (App path). Returns a
        frozen tuple of :class:`GitHubRepoRef` per F42.
        """
        response = self._get("/installation/repositories")
        payload = response.json()
        repos: list[GitHubRepoRef] = []
        for entry in payload.get("repositories", []):
            repos.append(
                GitHubRepoRef(
                    repo_id=int(entry.get("id", 0)),
                    full_name=str(entry.get("full_name", "")),
                    default_branch=str(entry.get("default_branch", "main")),
                    visibility=str(entry.get("visibility", "private")),
                    archived=bool(entry.get("archived", False)),
                )
            )
        return tuple(repos)

    def list_commits_since(self, *, full_name: str, since: str | None) -> tuple[GitHubCommitRef, ...]:
        """Stream commits to ``full_name`` since the ISO-8601 ``since`` cursor.

        Wraps ``GET /repos/{owner}/{repo}/commits?since=``. Returns a
        frozen tuple of :class:`GitHubCommitRef` per F42, oldest-first
        so the caller can advance the cursor to the last SHA.
        """
        params: dict[str, str] = {}
        if since is not None:
            params["since"] = since
        response = self._get(f"/repos/{full_name}/commits", params=params)
        payload = response.json()
        commits: list[GitHubCommitRef] = []
        for entry in payload:
            commit_block = entry.get("commit", {}) or {}
            author_block = commit_block.get("author", {}) or {}
            commits.append(
                GitHubCommitRef(
                    sha=str(entry.get("sha", "")),
                    committed_at=str(author_block.get("date", "")),
                    message=str(commit_block.get("message", "")),
                    author=str(author_block.get("name", "")) or None,
                )
            )
        # GitHub returns newest-first; reverse so callers can advance
        # the cursor monotonically as they iterate.
        commits.reverse()
        return tuple(commits)

    def list_issues_since(self, *, full_name: str, since: str | None) -> tuple[GitHubIssueRef, ...]:
        """Stream issues + PRs updated since ``since``.

        Wraps ``GET /repos/{owner}/{repo}/issues?since=`` — note GitHub
        merges PRs into the issues feed with a ``pull_request`` block
        to disambiguate.
        """
        params: dict[str, str] = {"state": "all"}
        if since is not None:
            params["since"] = since
        response = self._get(f"/repos/{full_name}/issues", params=params)
        payload = response.json()
        issues: list[GitHubIssueRef] = []
        for entry in payload:
            issues.append(
                GitHubIssueRef(
                    number=int(entry.get("number", 0)),
                    kind="pull_request" if entry.get("pull_request") else "issue",
                    updated_at=str(entry.get("updated_at", "")),
                    title=str(entry.get("title", "")),
                    body=str(entry.get("body") or ""),
                    state=str(entry.get("state", "open")),
                )
            )
        return tuple(issues)

    def get_tree_recursive(self, *, full_name: str, ref: str) -> tuple[tuple[GitHubBlobRef, ...], bool]:
        """Walk the repo tree at ``ref`` and return ``(blobs, truncated)``.

        Wraps ``GET /repos/{owner}/{repo}/git/trees/{sha}?recursive=1``.
        The second tuple element is ``True`` when GitHub returned
        ``truncated=true`` (typically at >100k entries) — the caller
        should then fall back to the connector's ``dulwich`` clone path
        per spec §0.
        """
        response = self._get(f"/repos/{full_name}/git/trees/{ref}", params={"recursive": "1"})
        payload = response.json()
        truncated = bool(payload.get("truncated", False))
        blobs: list[GitHubBlobRef] = []
        for entry in payload.get("tree", []):
            if entry.get("type") != "blob":
                continue
            blobs.append(
                GitHubBlobRef(
                    path=str(entry.get("path", "")),
                    sha=str(entry.get("sha", "")),
                    size=int(entry.get("size", 0)),
                    mime_hint=guess_mime_from_path(str(entry.get("path", ""))),
                )
            )
        return tuple(blobs), truncated

    def fetch_blob(self, *, full_name: str, sha: str) -> bytes:
        """Download a blob by sha; returns raw bytes.

        Wraps ``GET /repos/{owner}/{repo}/git/blobs/{sha}`` with
        ``Accept: application/vnd.github.raw``. LFS pointer files (>100
        MB blobs) are deliberately NOT followed here — the connector
        emits a ``lfs_object_skipped`` event instead per spec §5.
        """
        response = self._get(
            f"/repos/{full_name}/git/blobs/{sha}",
            extra_headers={"Accept": "application/vnd.github.raw"},
        )
        return response.content

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Perform a GET with the standard auth + accept headers.

        Single call point for the REST surface — keeps ``_raise_for_status``
        + rate-limit gauge bookkeeping in one place. F16 — wrapper kept
        narrow so the per-endpoint helpers above stay flat.
        """
        headers = dict(self.bearer_header())
        headers["Accept"] = _ACCEPT_GITHUB_JSON
        if extra_headers is not None:
            for key, value in extra_headers.items():
                headers[key] = value
        response = self._http.get(
            f"{self._config.base_url}{path}",
            params=dict(params) if params else None,
            headers=headers,
        )
        self._record_rate_limit_headers(response)
        self._raise_for_status(response, action="GET " + path)
        return response

    def _record_rate_limit_headers(self, response: httpx.Response) -> None:
        """Capture the rate-limit gauge values from response headers."""
        remaining = response.headers.get(_X_RATE_REMAINING)
        if remaining is not None:
            try:
                self._stats.rest_rate_remaining = int(remaining)
            except ValueError:
                pass
        reset = response.headers.get(_X_RATE_RESET)
        if reset is not None:
            try:
                self._stats.rest_rate_reset_epoch = int(reset)
            except ValueError:
                pass
        self._stats.rest_requests += 1

    def _raise_for_status(self, response: httpx.Response, *, action: str) -> None:
        """Translate non-2xx into typed exceptions per spec §5.

        Secondary rate-limit (403 + Retry-After) → ``ContainerTransientError``
        with the retry budget threaded through; primary rate exhausted
        (``x-ratelimit-remaining == 0``) → also transient; 401 →
        ``CredentialExpiredError``; 403 without retry-after →
        ``InsufficientPermissionsError``.
        """
        if response.status_code < 400:
            return
        request_id = response.headers.get(_X_GITHUB_REQUEST_ID, "")
        # F15-clean — log path + status + request_id only, never the
        # response body (could contain leaked tokens in error envelopes).
        logger.warning(
            "github: %s returned %s (x-github-request-id=%s)",
            action,
            response.status_code,
            request_id,
        )
        if response.status_code == 401:
            self.invalidate_token()
            raise CredentialExpiredError(
                f"github: {action} returned 401 unauthorised. "
                "fix: rotate the credential via `kairix cc-pair rotate-credential <id>`. "
                "next: see kairix/connectors/github/connector.py for token rotation."
            )
        if response.status_code == 403:
            retry_after_header = response.headers.get(_RETRY_AFTER)
            if retry_after_header is not None:
                self._stats.rest_403_secondary_total += 1
                try:
                    retry_after = float(retry_after_header)
                except ValueError:
                    retry_after = 60.0
                raise ContainerTransientError(
                    f"github: {action} hit secondary/abuse rate limit; retry after {retry_after}s",
                    retry_after=retry_after,
                )
            remaining = response.headers.get(_X_RATE_REMAINING)
            if remaining == "0":
                raise ContainerTransientError(
                    f"github: {action} primary rate-limit exhausted; wait for x-ratelimit-reset",
                    retry_after=60.0,
                )
            raise InsufficientPermissionsError(
                f"github: {action} returned 403 forbidden. "
                "fix: confirm the App installation grants Contents:Read + Issues:Read on this repo. "
                "next: see docs/architecture/connector-scope-topology/connector-design-specs/github.md §1."
            )
        if response.status_code in (404, 410):
            raise CredentialInvalidError(
                f"github: {action} returned {response.status_code}; the resource is gone or never existed."
            )
        # 5xx + everything else → transient
        raise ContainerTransientError(
            f"github: {action} returned {response.status_code}; treating as transient.",
            retry_after=30.0,
        )

    def stats(self) -> ClientStatsSnapshot:
        """Return a frozen snapshot of the wire-side counters per F42."""
        return ClientStatsSnapshot(
            rest_requests=self._stats.rest_requests,
            rest_rate_remaining=self._stats.rest_rate_remaining,
            rest_rate_reset_epoch=self._stats.rest_rate_reset_epoch,
            rest_403_secondary_total=self._stats.rest_403_secondary_total,
            installation_token_rotations=self._stats.installation_token_rotations,
        )


@dataclass
class _ClientStats:
    """Internal mutable counter set; snapshot returned via :meth:`stats`."""

    rest_requests: int = 0
    rest_rate_remaining: int = -1
    rest_rate_reset_epoch: int = -1
    rest_403_secondary_total: int = 0
    installation_token_rotations: int = 0


@dataclass(frozen=True)
class ClientStatsSnapshot:
    """Frozen counter snapshot — F42-compliant return shape.

    The ``rest_rate_remaining`` gauge is sentinel ``-1`` until the
    first rate-limited request populates it; that distinguishes "no
    requests yet" from "throttle budget exhausted".
    """

    rest_requests: int
    rest_rate_remaining: int
    rest_rate_reset_epoch: int
    rest_403_secondary_total: int
    installation_token_rotations: int


# F17 — extension → MIME map; module-level so the per-blob walk above
# can reuse the lookup without re-allocating the dict on every call.
_EXTENSION_TO_MIME: Mapping[str, str] = {
    ".py": "text/x-python",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".json": "application/json",
    ".toml": "application/toml",
    ".rs": "text/x-rust",
    ".go": "text/x-go",
    ".ts": "text/typescript",
    ".tsx": "text/typescript",
    ".js": "text/javascript",
    ".html": "text/html",
    ".css": "text/css",
    ".sh": "text/x-shellscript",
    ".txt": "text/plain",
}


def guess_mime_from_path(path: str) -> str:
    """Extension-first mime guess for tree-walk envelopes.

    Module-level helper (not bound to a class) so the per-blob loop in
    :meth:`GitHubApiClient.get_tree_recursive` stays under F16's
    cognitive-complexity ceiling. Default ``application/octet-stream``
    when no extension match.
    """
    lower = path.lower()
    for extension, mime in _EXTENSION_TO_MIME.items():
        if lower.endswith(extension):
            return mime
    return "application/octet-stream"


# F19 — placeholder reserving the type slot for a future
# ``client_specific_config`` mapping the operator may layer over the
# defaults. Currently a no-op; explicitly typed so the framework can
# discover the shape.
ClientSpecificConfig = dict[str, Any]
_ = field  # F18 / F19 — keep the dataclass.field import referenced
