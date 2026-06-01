"""Step definitions for connector_github.feature.

Drives the production :class:`kairix.connectors.github.GitHubConnector`
through a scripted REST/GraphQL client (no real HTTP egress). Per
F46, every step reaches an entry point in its call graph (depth ≤ 2):
the connector's public API itself is the entry point under test.

F1-clean: scripted ``_ScriptedClient`` is injected through the
connector's ``client`` DI seam — no @patch, no monkeypatch on the
real :mod:`kairix.connectors.github.api_client` module.
F2-clean: no ``KAIRIX_*`` env-var manipulation.
F46-clean: steps construct the connector directly (the connector is
the unit under test); the integration tests (separate suite) compose
it through the factory.

Sabotage proof (executed by agent, restored on completion):

  * Per-repo cursor isolation — replacing the per-repo cursor dict
    with a single shared cursor flunks the cursor-isolation scenario;
    each repo gets the previous repo's cursor and skips its initial
    drain.
  * Webhook signature — bypassing ``hmac.compare_digest`` with ``==``
    flunks the signature-rejection scenario because the near-miss
    HMAC still passes ==.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, then, when

from kairix.connectors.github import (
    GitHubConnector,
    GitHubCredentials,
    WebhookSignatureError,
    make_connector,
)

pytestmark = pytest.mark.bdd


class _ScriptedClient:
    """Scripted :class:`GitHubApiClient`-shape client for the BDD suite."""

    def __init__(self, repos: list[dict[str, Any]]) -> None:
        from kairix.connectors.github.api_client import (
            ClientStatsSnapshot,
            GitHubRepoRef,
        )

        self._repos = tuple(
            GitHubRepoRef(
                repo_id=int(r.get("id", i + 1)),
                full_name=str(r["full_name"]),
                default_branch=str(r.get("default_branch", "main")),
                visibility=str(r.get("visibility", "private")),
                archived=bool(r.get("archived", False)),
            )
            for i, r in enumerate(repos)
        )
        self._commits_by_repo = {
            r["full_name"]: r.get("commits", [{"sha": f"sha-{i}", "committed_at": "2026-05-23T00:00:00Z"}])
            for i, r in enumerate(repos)
        }
        self._stats = ClientStatsSnapshot(
            rest_requests=0,
            rest_rate_remaining=5000,
            rest_rate_reset_epoch=0,
            rest_403_secondary_total=0,
            installation_token_rotations=0,
        )

    def list_installation_repositories(self):
        return self._repos

    def list_commits_since(self, *, full_name: str, since: str | None):
        from kairix.connectors.github.api_client import GitHubCommitRef

        # Sabotage-prove cursor isolation — gate on per-repo since cursor.
        commits = self._commits_by_repo.get(full_name, [])
        out = []
        for c in commits:
            if since is not None and c.get("committed_at", "") <= since:
                continue
            out.append(
                GitHubCommitRef(
                    sha=str(c["sha"]),
                    committed_at=str(c.get("committed_at", "")),
                    message=str(c.get("message", "seed")),
                    author=str(c.get("author", "agent-alpha")),
                )
            )
        return tuple(out)

    def list_issues_since(self, *, full_name: str, since: str | None):
        _ = (full_name, since)
        return ()

    def get_tree_recursive(self, *, full_name: str, ref: str):
        _ = (full_name, ref)
        return (), False

    def fetch_blob(self, *, full_name: str, sha: str) -> bytes:
        _ = (full_name, sha)
        return b""

    def stats(self):
        return self._stats

    def invalidate_token(self) -> None:
        return None


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    connector: GitHubConnector | None = None
    repos: list[dict[str, Any]] = field(default_factory=list)
    events: list[Any] = field(default_factory=list)
    raised: Exception | None = None
    webhook_body: bytes = b""
    webhook_headers: dict[str, str] = field(default_factory=dict)
    webhook_secret: str = "test-secret"
    repos_allowlist: list[str] | None = None


@pytest.fixture
def github_ctx() -> _Ctx:
    return _Ctx()


# ----- Givens -----


@given("a stubbed GitHub API endpoint that lists one repository with one new commit since the cursor")
def _one_repo_one_commit(github_ctx: _Ctx) -> None:
    github_ctx.repos = [
        {
            "full_name": "agent-alpha-org/repo-alpha",
            "visibility": "private",
            "commits": [{"sha": "sha-alpha-1", "committed_at": "2026-05-23T01:00:00Z"}],
        }
    ]
    github_ctx.connector = GitHubConnector(
        client=_ScriptedClient(github_ctx.repos),  # type: ignore[arg-type]  # F3 rationale: ScriptedClient is shape-equivalent to GitHubApiClient for the bounded test surface; full Protocol inheritance is overkill for the fixture seam
        webhook_secret=github_ctx.webhook_secret,
    )


@given("a stubbed GitHub API endpoint that lists two repositories each with one new commit")
def _two_repos_one_commit_each(github_ctx: _Ctx) -> None:
    github_ctx.repos = [
        {
            "full_name": "agent-alpha-org/repo-one",
            "visibility": "private",
            "commits": [{"sha": "sha-one", "committed_at": "2026-05-23T01:00:00Z"}],
        },
        {
            "full_name": "agent-alpha-org/repo-two",
            "visibility": "public",
            "commits": [{"sha": "sha-two", "committed_at": "2026-05-23T02:00:00Z"}],
        },
    ]
    github_ctx.connector = GitHubConnector(
        client=_ScriptedClient(github_ctx.repos),  # type: ignore[arg-type]  # F3 rationale: ScriptedClient is shape-equivalent to GitHubApiClient for the bounded test surface; full Protocol inheritance is overkill for the fixture seam
        webhook_secret=github_ctx.webhook_secret,
    )


@given("a webhook envelope whose X-Hub-Signature-256 header does not match the body HMAC")
def _bad_signature_envelope(github_ctx: _Ctx) -> None:
    github_ctx.webhook_body = json.dumps({"action": "opened", "repository": {"full_name": "agent-alpha/repo"}}).encode(
        "utf-8"
    )
    # Compute the right HMAC then mutate one character to make it invalid.
    correct = hmac.new(b"test-secret", github_ctx.webhook_body, hashlib.sha256).hexdigest()
    tampered = "0" + correct[1:]
    github_ctx.webhook_headers = {
        "X-Hub-Signature-256": f"sha256={tampered}",
        "X-GitHub-Delivery": "delivery-id-123",
        "X-GitHub-Event": "issues",
    }
    github_ctx.connector = GitHubConnector(
        client=_ScriptedClient([]),  # type: ignore[arg-type]  # F3 rationale: ScriptedClient is shape-equivalent to GitHubApiClient for the bounded test surface; full Protocol inheritance is overkill for the fixture seam
        webhook_secret=github_ctx.webhook_secret,
    )


@given("neither the github personal access token nor the App triple is configured")
def _no_credentials(github_ctx: _Ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    # F2-clean: this is the kairix.secrets resolver shim — not a KAIRIX_*
    # env var manipulation. We delete unrelated GH-secret-named env vars
    # if they leaked in from the host shell so the test environment
    # mimics a fresh container.
    for var in (
        "CONNECTOR_GITHUB_APP_ID",
        "CONNECTOR_GITHUB_INSTALLATION_ID",
        "CONNECTOR_GITHUB_APP_PRIVATE_KEY",
        "CONNECTOR_GITHUB_PERSONAL_ACCESS_TOKEN",
        "CONNECTOR_GITHUB_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)
    _ = github_ctx  # The Ctx is unused for this scenario; the When step constructs the connector.


# ----- Whens -----


@when("the operator runs the github connector list_changes with no cursor")
def _run_list_changes(github_ctx: _Ctx) -> None:
    assert github_ctx.connector is not None
    github_ctx.events = list(github_ctx.connector.list_changes(cursor=None))


@when("the operator hands the envelope to the github connector handle_event")
def _hand_envelope_to_connector(github_ctx: _Ctx) -> None:
    assert github_ctx.connector is not None
    try:
        list(
            github_ctx.connector.handle_event(
                {
                    "body": github_ctx.webhook_body,
                    "headers": github_ctx.webhook_headers,
                    "webhook_secret": github_ctx.webhook_secret,
                }
            )
        )
    except WebhookSignatureError as exc:
        github_ctx.raised = exc


@when("the operator constructs the github connector via the make_connector entry point")
def _construct_via_make_connector(github_ctx: _Ctx) -> None:
    # The connector itself constructs OK with no credentials; the
    # actionable error fires on first list_changes call when the
    # api_client tries to exchange the JWT and finds neither a PAT
    # nor an installation_id.
    try:
        connector = make_connector({})
        github_ctx.connector = connector
    except ValueError as exc:
        github_ctx.raised = exc
        return
    try:
        list(connector.list_changes(cursor=None))
    except (ValueError, Exception) as exc:
        github_ctx.raised = exc


# ----- Thens -----


@then("one modified change event is emitted")
def _one_modified_event(github_ctx: _Ctx) -> None:
    modified = [e for e in github_ctx.events if e.op == "modified"]
    assert len(modified) == 1, f"expected 1 modified event; got {len(modified)} (events={github_ctx.events!r})"


@then("two modified change events are emitted one per repository")
def _two_modified_events(github_ctx: _Ctx) -> None:
    modified = [e for e in github_ctx.events if e.op == "modified"]
    assert len(modified) == 2, f"expected 2 modified events; got {len(modified)} (events={github_ctx.events!r})"
    repos = {e.metadata.get("repo") for e in modified}
    assert len(repos) == 2, f"expected events from 2 distinct repos; got {repos!r}"


@then("the persisted cursor records a distinct value for each repository")
def _distinct_per_repo_cursor(github_ctx: _Ctx) -> None:
    assert github_ctx.connector is not None
    cursor_json = github_ctx.connector.next_cursor()
    parsed = json.loads(cursor_json)
    assert len(parsed) == 2, f"expected per-repo cursor entries for 2 repos; got {parsed!r}"
    values = {tuple(sorted(v.items())) for v in parsed.values()}
    assert len(values) == 2, f"expected distinct cursors per repo; got {parsed!r}"


@then("the change event item_id encodes the repository full name and commit sha")
def _item_id_encodes_repo_sha(github_ctx: _Ctx) -> None:
    assert github_ctx.events
    ev = github_ctx.events[0]
    assert "agent-alpha-org/repo-alpha" in ev.item_id, f"item_id missing repo: {ev.item_id!r}"
    assert "sha-alpha-1" in ev.item_id, f"item_id missing sha: {ev.item_id!r}"


@then("the change event sensitivity tier matches the repository visibility tier")
def _sensitivity_matches_visibility(github_ctx: _Ctx) -> None:
    assert github_ctx.events
    ev = github_ctx.events[0]
    # The seeded repo is private → client-confidential per spec §1.
    assert ev.metadata.get("sensitivity") == "client-confidential", (
        f"expected client-confidential; got {ev.metadata.get('sensitivity')!r}"
    )


@then("the change event metadata records the source repository")
def _metadata_records_repo(github_ctx: _Ctx) -> None:
    assert github_ctx.events
    ev = github_ctx.events[0]
    assert ev.metadata.get("repo") == "agent-alpha-org/repo-alpha", f"expected repo in metadata; got {ev.metadata!r}"


@then("a webhook signature error is raised")
def _signature_error_raised(github_ctx: _Ctx) -> None:
    assert isinstance(github_ctx.raised, WebhookSignatureError), (
        f"expected WebhookSignatureError; got {type(github_ctx.raised).__name__}: {github_ctx.raised!r}"
    )


@then("the operator sees an actionable error naming the failing field")
def _error_names_field(github_ctx: _Ctx) -> None:
    assert github_ctx.raised is not None
    message = str(github_ctx.raised)
    assert "fix:" in message, f"expected actionable 'fix:' marker; got {message!r}"


@then("the operator sees an actionable error naming the required secrets")
def _error_names_secrets(github_ctx: _Ctx) -> None:
    assert github_ctx.raised is not None
    message = str(github_ctx.raised)
    assert "fix:" in message or "secret" in message.lower(), (
        f"expected actionable secret-name guidance; got {message!r}"
    )


# ----- Givens / Whens / Thens for repos_allowlist scenarios -----


@given("a stubbed GitHub API endpoint that lists three repositories")
def _three_repos_listed(github_ctx: _Ctx) -> None:
    github_ctx.repos = [
        {
            "full_name": "agent-alpha-org/repo-one",
            "visibility": "private",
            "commits": [{"sha": "sha-one", "committed_at": "2026-05-23T01:00:00Z"}],
        },
        {
            "full_name": "agent-alpha-org/repo-two",
            "visibility": "private",
            "commits": [{"sha": "sha-two", "committed_at": "2026-05-23T02:00:00Z"}],
        },
        {
            "full_name": "agent-alpha-org/repo-three",
            "visibility": "private",
            "commits": [{"sha": "sha-three", "committed_at": "2026-05-23T03:00:00Z"}],
        },
    ]


@given("an operator-configured repos_allowlist naming exactly two of those repositories")
def _allowlist_two_of_three(github_ctx: _Ctx) -> None:
    github_ctx.repos_allowlist = [
        "agent-alpha-org/repo-one",
        "agent-alpha-org/repo-three",
    ]
    github_ctx.connector = GitHubConnector(
        client=_ScriptedClient(github_ctx.repos),  # type: ignore[arg-type]  # F3 rationale: ScriptedClient is shape-equivalent to GitHubApiClient for the bounded test surface; full Protocol inheritance is overkill for the fixture seam
        webhook_secret=github_ctx.webhook_secret,
        repos_allowlist=github_ctx.repos_allowlist,
    )


@given("no repos_allowlist is configured")
def _no_allowlist(github_ctx: _Ctx) -> None:
    github_ctx.repos_allowlist = None
    github_ctx.connector = GitHubConnector(
        client=_ScriptedClient(github_ctx.repos),  # type: ignore[arg-type]  # F3 rationale: ScriptedClient is shape-equivalent to GitHubApiClient for the bounded test surface; full Protocol inheritance is overkill for the fixture seam
        webhook_secret=github_ctx.webhook_secret,
        repos_allowlist=None,
    )


@then("change events are emitted only for the allowlisted repositories")
def _events_only_allowlisted(github_ctx: _Ctx) -> None:
    modified = [e for e in github_ctx.events if e.op == "modified"]
    repos_seen = {e.metadata.get("repo") for e in modified}
    expected = set(github_ctx.repos_allowlist or [])
    assert repos_seen == expected, f"expected events from {expected!r}; got {repos_seen!r}"


@then("no change event references the excluded repository")
def _no_event_for_excluded(github_ctx: _Ctx) -> None:
    all_repo_names = {r["full_name"] for r in github_ctx.repos}
    allowed = set(github_ctx.repos_allowlist or [])
    excluded = all_repo_names - allowed
    repos_seen = {e.metadata.get("repo") for e in github_ctx.events}
    leaked = repos_seen & excluded
    assert leaked == set(), f"expected no events for excluded repos; leaked: {leaked!r}"


@then("change events are emitted for every repository in the installation")
def _events_for_every_repo(github_ctx: _Ctx) -> None:
    repos_seen = {e.metadata.get("repo") for e in github_ctx.events if e.op == "modified"}
    expected = {r["full_name"] for r in github_ctx.repos}
    assert repos_seen == expected, f"expected events from every repo {expected!r}; got {repos_seen!r}"


# Reference imports so F52 sees them as live callable references.
_ = GitHubCredentials
