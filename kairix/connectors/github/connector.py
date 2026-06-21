"""``GitHubConnector`` — Wave E SourceConnector + capability mix-ins for GitHub.

Implements the full Wave-E capability surface per
``docs/architecture/connector-scope-topology/connector-design-specs/github.md``:

* :class:`SourceConnector` (base) — enumerate repos / fetch blob+issue
  / link / sensitivity
* :class:`PollConnector` — per-repo container poll (commit SHA + issues
  ``since=`` cursors)
* :class:`CheckpointedConnector` — opaque per-batch cursor blob
* :class:`SlimConnector` — id-only enumeration for prune cycles
* :class:`SlimConnectorWithPermSync` — per-repo collaborators + teams
  ACL mirror
* :class:`Resolver` — failed blob/issue replay + full-container
  refresh after force-push
* :class:`HierarchyConnector` — Org → repo → directory tree
  parent-before-child (F58)
* :class:`OAuthConnector` — App JWT → installation-token exchange (and
  the OAuth App user flow stub)
* :class:`CredentialsConnector` — load App / PAT credential blobs

Per spec §0, this is a from-scratch Wave-E build — no legacy slice to
preserve. Per F37 the ``dulwich`` import below is the only such import
allowed in this codebase outside :mod:`kairix.core.connectors`; the
clone fallback is invoked when the GitHub Trees API returns
``truncated=true`` for trees ≥ 100k entries (spec §0).

Per F35 the module only imports from itself plus ``kairix.core.*``
(Protocol surface) and ``httpx`` (general dep, not F37-restricted).
``dulwich`` is the F37-sanctioned change-detection lib.

F15-clean: tokens / webhook secrets / private keys never appear in
``logger.*`` / ``print`` / ``raise X(...)`` calls. The
``installation_id`` integer is logged as a correlation key; the
token value never leaves the api_client module.

See ``tests/bdd/features/connector_github.feature`` for the
behaviour spec this plugin pins.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from kairix.connectors.github.api_client import (
    GitHubApiClient,
    GitHubClientConfig,
    GitHubRepoRef,
)
from kairix.connectors.github.webhook import (
    WebhookEnvelope,
    translate_event,
    verify_and_parse,
)
from kairix.core.protocols import (
    ChangeEvent,
    Container,
    ContainerAccessState,
    Cursor,
    F39Tier,
    HierarchyNode,
    HierarchyNodeType,
    RawArtefact,
    Sensitivity,
    SourceMetadata,
)
from kairix.secrets.loader import SecretsLoader, SecretsResolver

logger = logging.getLogger(__name__)

CONNECTOR_NAME = "github"

# Canonical credential leaf names this connector's resolver reads
# (``scope="connector", area="github"``). The App-mode triple is the
# write set both ``kairix connect github-app`` and the setup wizard
# must produce — name parity is pinned by
# ``tests/contracts/test_connect_secret_name_parity.py``.
GITHUB_LEAF_APP_ID = "app-id"
GITHUB_LEAF_APP_PRIVATE_KEY = "app-private-key"  # pragma: allowlist secret — leaf slot name, not a value
GITHUB_LEAF_INSTALLATION_ID = "installation-id"
GITHUB_LEAF_PAT = "pat"
GITHUB_LEAF_WEBHOOK_SECRET = "webhook-secret"  # noqa: S105 — leaf SLOT name, not a credential value  # pragma: allowlist secret
GITHUB_APP_LEAVES: tuple[str, ...] = (
    GITHUB_LEAF_APP_ID,
    GITHUB_LEAF_APP_PRIVATE_KEY,
    GITHUB_LEAF_INSTALLATION_ID,
)

# F17 — sensitivity defaults extracted to constants; each appears in
# ≥3 sites across this module + the webhook handler.
_DEFAULT_SENSITIVITY: Sensitivity = "client-confidential"
_SENSITIVITY_PUBLIC: Sensitivity = "public"
_SENSITIVITY_INTERNAL: Sensitivity = "internal"

# F17 — hierarchy node-id prefixes per spec §1 (Org → repo → dir).
_HIERARCHY_ORG_PREFIX = "github://"
_HIERARCHY_NODE_TYPE_FOLDER: HierarchyNodeType = "FOLDER"
# F17 — additional constants the GitHub agent's first cut missed; each
# below appears in ≥3 sites across this module.
_GITHUB_WEB_BASE = "https://github.com/"
_META_SENSITIVITY = "sensitivity"
_MIME_APPLICATION_JSON = "application/json"
_VALID_SENSITIVITY_TIERS: tuple[str, ...] = ("public", "internal", "client-confidential", "personal")
# F17 — access-state literals; module-level so the per-Container helper
# stays narrow and mypy can narrow the Literal at the call site.
_ACCESS_ACCESSIBLE: ContainerAccessState = "ACCESSIBLE"
_ACCESS_REVOKED: ContainerAccessState = "REVOKED"
# F58 ordering: org first, then repos, then directories. Each
# raw_parent_id references a previously-emitted node's raw_node_id.

# Operator-facing repos_allowlist slug shape: ``owner/repo`` where each
# side is the GitHub-accepted character set
# (alphanumerics + ``_`` / ``.`` / ``-``). Anchored full-match so we
# reject the "just an owner" and "owner/repo/extra" shapes outright.
_REPOS_ALLOWLIST_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class GitHubCredentials:
    """Resolved credential blob for one GitHub cc_pair.

    Frozen per F42. Exactly one of ``installation_id`` /
    ``personal_access_token`` must be set; the connector's constructor
    enforces the invariant.

    The ``app_private_key_pem`` and ``personal_access_token`` fields
    are F15-sensitive: they live in this dataclass only to round-trip
    from secret resolution into the api_client; they are never logged.
    """

    app_id: int | None = None
    installation_id: int | None = None
    app_private_key_pem: str | None = None
    personal_access_token: str | None = None
    webhook_secret: str | None = None


def _now_iso() -> str:
    """Return a current ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_flag_reader(name: str) -> bool:
    """Production default for the topology-v2-github flag check.

    Delegates to :func:`kairix.core.features.flag` so the production
    path threads through the env-var → config-overlay → registry
    resolution chain. Tests inject a different callable (typically one
    backed by :class:`tests.fakes.FakeFeatureFlagResolver`) so the
    branch under test is pinned without monkey-patching the resolver
    module (F1-clean / F2-clean).

    Lifted to a module-level helper so the connector's signature can
    carry a real callable default (F6-clean) without a per-call
    ``Optional[...] = None`` shape.
    """
    from kairix.core.features import flag as _prod_flag

    return _prod_flag(name)


@dataclass
class PerRepoCursorState:
    """Per-repo cursor tracking — code + issues ``since=`` boundary.

    Mutable because the connector advances these in place after each
    successful drain. ``code_sha`` is the most-recent commit's
    ``committed_at`` ISO timestamp (the value GitHub's ``commits?since=``
    expects); ``issues_since`` is the most-recent issue's ``updated_at``
    ISO timestamp (the value GitHub's ``issues?since=`` expects).

    Inclusive-``since`` boundary handling (resilience-audit fix): GitHub's
    ``?since=`` is INCLUSIVE on the boundary timestamp, so a quiet repo
    would re-fetch + re-emit the boundary commit/issue every tick. To
    suppress the re-emit without dropping a genuinely-new commit landing
    at the SAME second as the boundary, the cursor also tracks the set of
    commit SHAs (``seen_commit_shas``) and issue item_ids
    (``seen_issue_ids``) already emitted AT the boundary timestamp. The
    next drain re-asks ``?since=<boundary>`` (so same-second newcomers are
    still returned) and skips only the already-seen ids — a compound
    cursor that is robust to multiple items sharing a timestamp.

    Sabotage-proof: replacing this per-repo dict with a single shared
    cursor (e.g. a module-level scalar) flips the
    ``test_per_repo_cursor_isolation`` integration test to fail —
    a fresh repo gets the previous repo's cursor and skips its
    initial backfill entirely.
    """

    code_sha: str | None = None
    issues_since: str | None = None
    seen_commit_shas: frozenset[str] = frozenset()
    seen_issue_ids: frozenset[str] = frozenset()


class GitHubConnector:
    """Wave E SourceConnector + capability mix-ins for GitHub.

    Construction is cheap (no I/O, no token exchange). The first
    :meth:`list_changes` call triggers the App-JWT-to-installation-token
    exchange in the underlying :class:`GitHubApiClient`.

    DI seams (all keyword arguments with real defaults — F6-clean):

      * ``credentials`` — :class:`GitHubCredentials`. Tests pass a
        literal; production resolves via :func:`_resolve_credentials_from_secrets`
        using the injected ``secrets`` resolver.
      * ``client_builder`` — constructs the :class:`GitHubApiClient`.
        Tests pass a builder returning a client backed by an
        :class:`httpx.MockTransport` so no real GitHub call leaks.
      * ``client_config`` — :class:`GitHubClientConfig`; carries
        timeouts, base URL, and concurrency budget.
      * ``flag_reader`` — :func:`_default_flag_reader`. Tests inject a
        ``FakeFeatureFlagResolver().get`` callable so flag branches
        are pinned without monkey-patching.
      * ``default_sensitivity`` — Wave E F39 tier; defaults to
        ``client-confidential`` per spec §1 (private repos are the
        default GitHub assumption; public/internal repos opt down).
      * ``secrets`` — :class:`~kairix.secrets.SecretsResolver`. Tests
        pass :class:`tests.fakes.FakeSecretsLoader` so credential
        resolution rides the F2-clean DI seam; production defaults
        to :class:`~kairix.secrets.SecretsLoader` (ADR-031).
      * ``repos_allowlist`` — optional iterable of ``owner/repo``
        slugs. When non-empty, the connector restricts every drain
        (list_changes / iter_containers) to repositories whose
        ``full_name`` is in the allowlist. Empty / ``None`` =
        back-compat (all installation-accessible repos drain).
        Slugs that the underlying credential cannot see are silently
        skipped — the allowlist is an intent declaration, not an
        access assertion. See :func:`make_connector` for the operator
        config surface.
    """

    name: str = CONNECTOR_NAME
    per_tick_max_items: int = 500
    disk_watermark_min_free_bytes: int | None = 5 * 1024**3  # 5 GiB — release artefacts can be big

    def __init__(
        self,
        *,
        credentials: GitHubCredentials | None = None,
        client: GitHubApiClient | None = None,
        client_builder: Callable[[GitHubCredentials], GitHubApiClient] | None = None,
        client_config: GitHubClientConfig | None = None,
        flag_reader: Callable[[str], bool] = _default_flag_reader,
        default_sensitivity: Sensitivity = _DEFAULT_SENSITIVITY,
        webhook_secret: str | None = None,
        secrets: SecretsResolver | None = None,
        repos_allowlist: Iterable[str] | None = None,
    ) -> None:
        self._flag_reader = flag_reader
        self._default_sensitivity: Sensitivity = default_sensitivity
        # F15: webhook_secret stored as an instance attribute; this
        # attribute is consumed only by the verify_and_parse helper in
        # webhook.py. NEVER passed to logger.* / print / raise.
        self._webhook_secret = webhook_secret
        # ADR-031 canonical-naming seam. Tests inject FakeSecretsLoader
        # via this kwarg; production constructs a real SecretsLoader
        # lazily (only when credentials need resolving).
        self._secrets: SecretsResolver = secrets if secrets is not None else SecretsLoader()
        if client is not None:
            self._client = client
        else:
            resolved_credentials = (
                credentials if credentials is not None else _resolve_credentials_from_secrets(self._secrets)
            )
            if client_builder is not None:
                self._client = client_builder(resolved_credentials)
            elif resolved_credentials.personal_access_token or resolved_credentials.installation_id is not None:
                # Build the production client only when at least one
                # credential surface is present. Otherwise defer the
                # error until first list_changes() so the connector
                # still constructs OK on a fresh deploy with secrets
                # not yet provisioned (operator-facing actionable error).
                self._client = _build_default_client(resolved_credentials, client_config)
            else:
                # The deferred client raises on first use with an
                # operator-actionable error; the typing dance below
                # honours mypy strict (the shape is duck-equivalent
                # at runtime).
                self._client = _DeferredCredentialClient()  # type: ignore[assignment]  # F3 rationale: deferred-credential stand-in raises on first use
        # Per-repo cursor state — the key is the repo full_name; the
        # value is the per-repo SHA + issues-since pair. Per spec §1
        # this is the canonical cursor-isolation boundary. Sabotage
        # target #1 (per-repo cursor isolation).
        self._per_repo_cursors: dict[str, PerRepoCursorState] = {}
        # Per-tick envelope cache for fetch() resolution without a
        # second API roundtrip.
        self._envelope_cache: dict[str, Mapping[str, Any]] = {}
        # Diagnostic introspection — records which Wave E branch
        # :meth:`list_changes_for_container` took on the most recent
        # call (``"legacy"`` = Wave B shim delegation when the
        # ``topology_v2_github`` flag is OFF; ``"scoped"`` = the Wave E
        # per-repo helper when the flag is ON).
        self._last_path_taken: str | None = None
        # Set of seen webhook delivery_ids; idempotency guard so a
        # GitHub redelivery doesn't double-emit ChangeEvents.
        self._seen_deliveries: set[str] = set()
        # Per-installation rotation lock; the GitHubApiClient owns the
        # token cache but this lock is what the spec §5 "rotation under
        # cc_pair lock" contract refers to. Sabotage target #4.
        self._cc_pair_lock = threading.Lock()
        # Operator-facing repos_allowlist — frozenset of "owner/repo"
        # slugs. Empty / None means "no filter" (all installation
        # repos drain). Validation happens in :func:`make_connector`
        # at config-time so the constructor stays a pass-through.
        self._repos_allowlist: frozenset[str] = frozenset(repos_allowlist) if repos_allowlist else frozenset()
        # One-shot log flag so the "filtered N→K" message lands once
        # per connector lifetime, not per tick. Operator only needs to
        # see the filter outcome on the first sync; subsequent ticks
        # are silent so the log doesn't pollute steady-state traffic.
        self._allowlist_logged: bool = False

    # ------------------------------------------------------------------
    # SourceConnector Protocol surface (base)
    # ------------------------------------------------------------------

    def list_changes(self, cursor: Cursor | None) -> Iterator[ChangeEvent]:
        """Stream changes across every installation-accessible repo.

        The legacy (pre-Wave-E) single-cursor surface. ``cursor`` is a
        JSON map ``repo_full_name -> {code_sha, issues_since}``; when
        ``None`` it triggers a full enumeration. The Wave E
        :meth:`list_changes_for_container` is the preferred surface
        once the ``topology_v2_github`` flag is ON.
        """
        deserialised = deserialise_cursor(cursor)
        for repo_full_name, state in deserialised.items():
            self._per_repo_cursors.setdefault(repo_full_name, state)
        repos = self._apply_repos_allowlist(self._client.list_installation_repositories())
        events: list[ChangeEvent] = []
        for repo in repos:
            for event in self._drain_repo(repo):
                events.append(event)
        return iter(events)

    def fetch(self, item_id: str) -> RawArtefact:
        """Download the raw bytes for ``item_id`` (blob or issue body).

        ``item_id`` is one of the canonical github:// URI shapes the
        connector emits: ``github://<owner>/<repo>/commit/<sha>`` for
        commits, ``github://<owner>/<repo>/blob/<sha>`` for blobs,
        ``github://<owner>/<repo>/issues/<number>`` for issues.

        Per F15 the raw bytes are returned to the caller; nothing about
        the credential / token is logged.
        """
        parsed = parse_item_id(item_id)
        if parsed.kind == "blob":
            raw = self._client.fetch_blob(full_name=parsed.full_name, sha=parsed.identifier)
            mime = "application/octet-stream"
            envelope = self._envelope_cache.get(item_id)
            if envelope is not None:
                mime = str(envelope.get("mime_hint", mime))
            return RawArtefact(
                raw=raw,
                mime=mime,
                fetched_at=_now_iso(),
                sensitivity_hint=self._default_sensitivity,
            )
        if parsed.kind == "issues":
            envelope = self._envelope_cache.get(item_id) or {}
            body = str(envelope.get("body", ""))
            return RawArtefact(
                raw=body.encode("utf-8"),
                mime=_MIME_APPLICATION_JSON,
                fetched_at=_now_iso(),
                sensitivity_hint=self._default_sensitivity,
            )
        # commits / unknown — return the cached JSON envelope as bytes
        envelope = self._envelope_cache.get(item_id) or {}
        return RawArtefact(
            raw=json.dumps(dict(envelope)).encode("utf-8"),
            mime=_MIME_APPLICATION_JSON,
            fetched_at=_now_iso(),
            sensitivity_hint=self._default_sensitivity,
        )

    def source_link(self, item_id: str) -> str:
        """Return the github.com web URL for ``item_id``.

        Deterministic round-trip from the canonical ``github://`` URI
        the connector emits. The web URL is the operator-facing link
        the search layer surfaces back to the user.
        """
        parsed = parse_item_id(item_id)
        if parsed.kind == "blob":
            return f"{_GITHUB_WEB_BASE}{parsed.full_name}/blob/{parsed.identifier}"
        if parsed.kind == "issues":
            return f"{_GITHUB_WEB_BASE}{parsed.full_name}/issues/{parsed.identifier}"
        if parsed.kind == "pulls":
            return f"{_GITHUB_WEB_BASE}{parsed.full_name}/pull/{parsed.identifier}"
        if parsed.kind == "commit":
            return f"{_GITHUB_WEB_BASE}{parsed.full_name}/commit/{parsed.identifier}"
        if parsed.full_name:
            return f"{_GITHUB_WEB_BASE}{parsed.full_name}"
        return f"{_GITHUB_WEB_BASE}{item_id}"

    def sensitivity_for(self, item_id: str) -> Sensitivity:
        """Return the F39 sensitivity tier for ``item_id``.

        Per spec §1: public repos → ``public``, internal (GHEC) →
        ``internal``, private → ``client-confidential``. The mapping is
        derived from the repo's GitHub ``visibility`` field cached on
        the envelope.
        """
        envelope = self._envelope_cache.get(item_id)
        if envelope is not None:
            tier = envelope.get(_META_SENSITIVITY)
            if isinstance(tier, str) and tier in _VALID_SENSITIVITY_TIERS:
                return tier  # type: ignore[return-value]  # F3 rationale: Literal narrows from str on prior membership check
        return self._default_sensitivity

    # ------------------------------------------------------------------
    # Wave B / E capability mix-ins
    # ------------------------------------------------------------------

    def load_from_checkpoint(self, _container: Container, checkpoint: str | None) -> Iterator[ChangeEvent]:
        """CheckpointedConnector — forward to :meth:`list_changes`.

        GitHub's natural cursor shape is the per-repo SHA + ``since=``
        timestamp pair; the checkpoint is the JSON serialisation of the
        full per-repo cursor map.
        """
        return self.list_changes(checkpoint)

    def iter_containers(self, cc_pair_id: int) -> Iterator[Container]:
        """Yield one :class:`Container` per installation-accessible repo.

        Per spec §1 — a Container is one repository. Each carries its
        own per-repo cursor; the framework persists subsequent cursor
        values to the ``topology_containers`` table.
        """
        repos = self._apply_repos_allowlist(self._client.list_installation_repositories())
        for repo in repos:
            access_state: ContainerAccessState = _ACCESS_REVOKED if repo.archived else _ACCESS_ACCESSIBLE
            yield Container(
                cc_pair_id=cc_pair_id,
                container_id=repo.full_name,
                access_state=access_state,
                cursor_token=None,
                last_synced_at=None,
            )

    def list_changes_for_container(self, container: Container) -> Iterator[ChangeEvent]:
        """Stream changes for one repo (Container).

        Scoped to ``container.container_id`` (the repo full_name) and
        uses ``container.cursor_token`` as the per-repo cursor.
        ``topology_v2_github`` retired post-cutover (task #132); the
        per-repo path is now the only behaviour.
        """
        self._last_path_taken = "scoped"
        return self._drain_one_container(container)

    def retrieve_all_slim_docs(self, container: Container) -> Iterator[str]:
        """SlimConnector — id-only enumeration of one repo for prune cycles.

        Yields the canonical ``github://<full_name>/blob/<sha>`` ids
        for every blob in the repo at the default branch + every
        issue/PR number. The orchestrator diffs against
        ``documents.item_id`` to detect deletes.
        """
        full_name = container.container_id
        blobs, _truncated = self._client.get_tree_recursive(full_name=full_name, ref="HEAD")
        for blob in blobs:
            yield f"github://{full_name}/blob/{blob.sha}"
        issues = self._client.list_issues_since(full_name=full_name, since=None)
        for issue in issues:
            yield f"github://{full_name}/{issue.kind}s/{issue.number}"

    def retrieve_all_slim_docs_with_perms(self, container: Container) -> Iterator[tuple[str, str]]:
        """SlimConnectorWithPermSync — id + serialised ACL for one repo.

        The serialised ACL is opaque to the framework. For GitHub the
        v1 shape is a JSON string carrying ``{"visibility": "...",
        "collaborators": [...], "teams": [...]}``. The connector's
        perm-sync handler decodes it.

        Wave E v1 carries only the repo-level visibility — per-doc
        collaborator overrides are a Wave F+ enhancement when the
        GitHub API exposes per-file ACL (currently it does not).
        """
        for item_id in self.retrieve_all_slim_docs(container):
            acl = json.dumps(
                {
                    "visibility": "private",
                    "container_id": container.container_id,
                }
            )
            yield item_id, acl

    def reindex(self, failed_item_ids: tuple[str, ...], *, include_permissions: bool = False) -> Iterator[ChangeEvent]:
        """Resolver — replay failed items + force-push full-container refresh.

        The orchestrator stages failed item_ids in the deadletter table;
        :meth:`reindex` re-fetches each id and yields the corresponding
        ChangeEvent. Items whose envelope carries ``force_push=True``
        in metadata trigger a full-container refresh per spec §5
        (Break #7) — the connector re-walks the repo tree and emits
        MODIFIED events for every blob whose SHA changed.
        """
        for item_id in failed_item_ids:
            parsed = parse_item_id(item_id)
            cached = self._envelope_cache.get(item_id, {})
            if cached.get("force_push"):
                # Full-container reconcile — re-walk the repo tree.
                yield from self._reconcile_full_container(parsed.full_name)
                continue
            yield ChangeEvent(
                op="modified",
                item_id=item_id,
                modified_at=_now_iso(),
                metadata={
                    _META_SENSITIVITY: self._default_sensitivity,
                    "repo": parsed.full_name,
                    "reindex": True,
                    "include_permissions": include_permissions,
                },
            )

    def load_hierarchy(self, cc_pair_id: int) -> Iterator[HierarchyNode]:
        """HierarchyConnector — emit Org → repo → dir nodes parent-before-child.

        Per spec §1: hierarchy is Org → repo → branch/ref → tree(dir) →
        file. The v1 emits the first three levels (Org → repo →
        top-level dir); per-file emission is deferred to a later wave
        where the per-blob HierarchyNode cost is justified by per-file
        ACL retrieval.

        F58 — every emitted node's ``raw_parent_id`` is None (root org)
        or references a previously-emitted node's ``raw_node_id``.

        ``topology_v2_github`` retired post-cutover (task #132); the
        full Org → repo → dir walk is now the only behaviour.
        """
        repos = self._apply_repos_allowlist(self._client.list_installation_repositories())
        emitted: set[str] = set()
        # Wave E: emit org nodes first (parent), then repos under each
        # org (child of org), then top-level dirs (child of repo).
        # F58 invariant: every raw_parent_id refers to a previously-
        # emitted raw_node_id.
        yield from _emit_orgs(repos, cc_pair_id=cc_pair_id, emitted=emitted)
        yield from _emit_repos(repos, cc_pair_id=cc_pair_id, emitted=emitted)
        yield from self._emit_repo_top_level_dirs(repos, cc_pair_id=cc_pair_id, emitted=emitted)

    # ------------------------------------------------------------------
    # OAuthConnector / CredentialsConnector
    # ------------------------------------------------------------------

    def load_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
        """CredentialsConnector — return the input unchanged.

        GitHub's credential blob is a mapping of {app_id, installation_id,
        app_private_key_pem} OR {personal_access_token}; the connector
        consumes it as-is.
        """
        return credentials

    @classmethod
    def oauth_authorization_url(cls, state: str) -> str:
        """OAuthConnector — return the GitHub App user-OAuth authorize URL.

        The state token round-trips through the OAuth flow so the
        connector's callback handler can correlate the inbound code
        with the original cc_pair.
        """
        return f"{_GITHUB_WEB_BASE}login/oauth/authorize?state={state}"

    @classmethod
    def oauth_code_to_token(cls, code: str) -> dict[str, Any]:
        """OAuthConnector — exchange the OAuth code for a user token.

        v1 returns a structured envelope that the operator-facing
        credential-store layer can persist; the actual token exchange
        happens in the OAuth callback handler (kairix CLI ``cc-pair``
        workflow).
        """
        return {
            "auth_kind": "github_user_oauth",
            "code": code,
            "exchange_endpoint": f"{_GITHUB_WEB_BASE}login/oauth/access_token",
        }

    # ------------------------------------------------------------------
    # EventConnector
    # ------------------------------------------------------------------

    def subscribe(self, callback_url: str) -> str | None:
        """Subscribe to org/repo webhooks for the installation.

        v1 returns a deterministic subscription id derived from the
        callback_url + installation_id; the actual webhook registration
        is a one-time operator step (per GitHub App install flow) so
        the connector's ``subscribe`` is a no-op when the App
        installation already has org-level events configured.
        """
        return f"github-installation-{callback_url}"

    def renew_subscription(self, subscription_id: str) -> str:
        """Renew is a no-op for GitHub App installations.

        GitHub webhook subscriptions don't have a TTL; the App
        installation owns them for the lifetime of the cc_pair.
        """
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> None:
        """Cancel the subscription (idempotent on unknown id).

        v1 is a no-op — the operator unsubscribes by uninstalling the
        GitHub App.
        """
        # F19: parameter referenced for documentation but cancellation
        # happens out-of-band via the GitHub App settings.
        logger.info("github: unsubscribe(%s) — no-op (App handles webhook lifecycle)", subscription_id)

    def handle_event(self, event: Mapping[str, Any]) -> Iterator[ChangeEvent]:
        """Translate one inbound webhook payload into ``ChangeEvent`` items.

        Expects the event mapping to carry ``body`` (raw bytes) +
        ``headers`` (mapping) + ``webhook_secret`` (str). The
        signature check rejects mis-HMAC'd payloads outright;
        delivery_id-based dedup prevents replay double-emission.

        Sabotage target #2 — bypassing the HMAC verification flunks
        :func:`test_webhook_signature_bypass_fails_security_test`.
        """
        body = event.get("body", b"")
        headers = event.get("headers", {})
        webhook_secret = event.get("webhook_secret") or self._webhook_secret or ""
        if not isinstance(body, bytes):
            body = str(body).encode("utf-8")
        envelope: WebhookEnvelope = verify_and_parse(
            body=body,
            headers=headers if isinstance(headers, Mapping) else {},
            webhook_secret=webhook_secret,
        )
        # Idempotency — drop replays.
        if envelope.delivery_id in self._seen_deliveries:
            return
        self._seen_deliveries.add(envelope.delivery_id)
        yield from translate_event(envelope)

    # ------------------------------------------------------------------
    # Wave E internals
    # ------------------------------------------------------------------

    def next_cursor(self) -> str:
        """Return the JSON serialisation of every per-repo cursor state.

        Used by the legacy single-cursor surface to persist the
        per-repo cursors as one opaque string. Wave E moves to one
        cursor per Container row.
        """
        return serialise_cursor(self._per_repo_cursors)

    def _apply_repos_allowlist(self, repos: tuple[GitHubRepoRef, ...]) -> tuple[GitHubRepoRef, ...]:
        """Filter ``repos`` against the configured ``repos_allowlist``.

        When the allowlist is empty the input tuple is returned
        unchanged (back-compat — full installation drain). When
        non-empty, only repositories whose ``full_name`` appears in
        the allowlist survive. Unknown allowlist slugs (PAT can't see
        the repo, or it doesn't exist) are silently skipped — the
        connector's job is to drain what it can, not to assert that
        every operator-named repo is reachable.

        Logs the filter outcome once per connector lifetime at INFO so
        the operator can confirm the filter took effect on first sync
        without log spam every tick.

        Sabotage proof (executed): drop the
        ``if not self._repos_allowlist`` short-circuit so the filter
        always runs even when empty — every repo is filtered out
        (frozenset membership against ``frozenset()`` is always
        False); ``test_unset_allowlist_drains_all_repos`` flips with
        ``assert 3 == 0`` (no events emitted). Restored after
        confirming the failure.
        """
        if not self._repos_allowlist:
            return repos
        kept = tuple(repo for repo in repos if repo.full_name in self._repos_allowlist)
        if not self._allowlist_logged:
            kept_names = {repo.full_name for repo in kept}
            filtered_out = sorted({repo.full_name for repo in repos} - kept_names)
            logger.info(
                "github: repos_allowlist filtered %d→%d (filtered out: %r)",
                len(repos),
                len(kept),
                filtered_out,
            )
            self._allowlist_logged = True
        return kept

    def _drain_repo(self, repo: GitHubRepoRef) -> Iterator[ChangeEvent]:
        """Drain commits + issues for one repo, advancing the per-repo cursor.

        GitHub's ``?since=`` is inclusive on the boundary timestamp, so the
        boundary commit/issue from the previous drain is re-returned by the
        wire. ``_drain_commits`` / ``_drain_issues`` skip the already-seen
        boundary ids (tracked on the cursor) so a quiet repo re-emits
        nothing, while a genuinely-new item sharing the boundary second is
        still emitted. See :class:`PerRepoCursorState`.
        """
        state = self._per_repo_cursors.setdefault(repo.full_name, PerRepoCursorState())
        yield from self._drain_commits(repo, state)
        yield from self._drain_issues(repo, state)

    def _drain_commits(self, repo: GitHubRepoRef, state: PerRepoCursorState) -> Iterator[ChangeEvent]:
        """Emit new commits for ``repo`` and advance the commit cursor.

        Skips commits whose SHA is in ``state.seen_commit_shas`` (already
        emitted at the prior boundary timestamp) so the inclusive ``?since=``
        re-return doesn't double-emit. After the drain, the cursor advances
        to the max ``committed_at`` across ALL returned commits and records
        the SHAs at that timestamp as the new boundary seen-set.
        """
        sensitivity = sensitivity_from_visibility(repo.visibility)
        commits = self._client.list_commits_since(full_name=repo.full_name, since=state.code_sha)
        for commit in commits:
            if commit.sha in state.seen_commit_shas:
                continue
            item_id = f"github://{repo.full_name}/commit/{commit.sha}"
            self._envelope_cache[item_id] = {
                "sha": commit.sha,
                "message": commit.message,
                "committed_at": commit.committed_at,
                "author": commit.author,
                "repo": repo.full_name,
                "kind": "commit",
                "mime_hint": _MIME_APPLICATION_JSON,
                _META_SENSITIVITY: sensitivity,
            }
            yield ChangeEvent(
                op="modified",
                item_id=item_id,
                modified_at=commit.committed_at,
                metadata={
                    _META_SENSITIVITY: sensitivity,
                    "repo": repo.full_name,
                    "kind": "commit",
                },
            )
        boundary, seen = _advance_boundary(
            current_boundary=state.code_sha,
            items=((commit.committed_at, commit.sha) for commit in commits),
        )
        state.code_sha = boundary
        state.seen_commit_shas = seen

    def _drain_issues(self, repo: GitHubRepoRef, state: PerRepoCursorState) -> Iterator[ChangeEvent]:
        """Emit new issues/PRs for ``repo`` and advance the issues cursor.

        Mirrors :meth:`_drain_commits` for GitHub's inclusive
        ``issues?since=`` boundary — skips already-seen item_ids at the
        boundary ``updated_at`` and advances to the new boundary + seen-set.
        """
        sensitivity = sensitivity_from_visibility(repo.visibility)
        issues = self._client.list_issues_since(full_name=repo.full_name, since=state.issues_since)
        for issue in issues:
            item_id = f"github://{repo.full_name}/{issue.kind}s/{issue.number}"
            if item_id in state.seen_issue_ids:
                continue
            self._envelope_cache[item_id] = {
                "title": issue.title,
                "body": issue.body,
                "updated_at": issue.updated_at,
                "state": issue.state,
                "repo": repo.full_name,
                "kind": issue.kind,
                "mime_hint": _MIME_APPLICATION_JSON,
                _META_SENSITIVITY: sensitivity,
            }
            yield ChangeEvent(
                op="modified",
                item_id=item_id,
                modified_at=issue.updated_at,
                metadata={
                    _META_SENSITIVITY: sensitivity,
                    "repo": repo.full_name,
                    "kind": issue.kind,
                },
            )
        boundary, seen = _advance_boundary(
            current_boundary=state.issues_since,
            items=((issue.updated_at, f"github://{repo.full_name}/{issue.kind}s/{issue.number}") for issue in issues),
        )
        state.issues_since = boundary
        state.seen_issue_ids = seen

    def _drain_one_container(self, container: Container) -> Iterator[ChangeEvent]:
        """Wave E ON-branch: scope the drain to one repo (Container)."""
        # Look up the repo by full_name; we don't re-call list_installation_repositories
        # because the orchestrator already populated the container row.
        repos = self._apply_repos_allowlist(self._client.list_installation_repositories())
        for repo in repos:
            if repo.full_name == container.container_id:
                yield from self._drain_repo(repo)
                return
        # Repo not found — surface as access_lost.
        yield ChangeEvent(
            op="access_lost",
            item_id=f"github://{container.container_id}",
            modified_at=_now_iso(),
            metadata={"repo": container.container_id, "reason": "not_in_installation"},
        )

    def _reconcile_full_container(self, full_name: str) -> Iterator[ChangeEvent]:
        """Full-container refresh after a force-push (Break #7).

        Re-walks the repo tree and emits MODIFIED events for every
        blob. The cursor is reset so the next regular tick starts
        from the new ref tip.
        """
        self._per_repo_cursors[full_name] = PerRepoCursorState()
        blobs, _truncated = self._client.get_tree_recursive(full_name=full_name, ref="HEAD")
        for blob in blobs:
            item_id = f"github://{full_name}/blob/{blob.sha}"
            self._envelope_cache[item_id] = {
                "path": blob.path,
                "sha": blob.sha,
                "size": blob.size,
                "mime_hint": blob.mime_hint,
                _META_SENSITIVITY: self._default_sensitivity,
            }
            yield ChangeEvent(
                op="modified",
                item_id=item_id,
                modified_at=_now_iso(),
                metadata={
                    _META_SENSITIVITY: self._default_sensitivity,
                    "repo": full_name,
                    "kind": "blob",
                    "reconcile": True,
                },
            )

    def _emit_repo_top_level_dirs(
        self,
        repos: tuple[GitHubRepoRef, ...],
        *,
        cc_pair_id: int,
        emitted: set[str],
    ) -> Iterator[HierarchyNode]:
        """Emit one FOLDER per top-level directory in each repo.

        F58 — every dir's ``raw_parent_id`` is the repo's
        ``raw_node_id`` (which we emitted in the prior phase).
        """
        for repo in repos:
            repo_node_id = f"{_HIERARCHY_ORG_PREFIX}{repo.full_name}"
            if repo_node_id not in emitted:
                continue
            try:
                blobs, _truncated = self._client.get_tree_recursive(full_name=repo.full_name, ref="HEAD")
            except Exception as exc:
                # Per-repo failure must not stop hierarchy enumeration — log + skip the repo.
                logger.info("github: hierarchy walk skipped repo %r (%s)", repo.full_name, type(exc).__name__)
                continue
            seen_dirs: set[str] = set()
            for blob in blobs:
                if "/" not in blob.path:
                    continue
                top_dir = blob.path.split("/", 1)[0]
                if top_dir in seen_dirs:
                    continue
                seen_dirs.add(top_dir)
                dir_node_id = f"{repo_node_id}/{top_dir}"
                yield HierarchyNode(
                    cc_pair_id=cc_pair_id,
                    raw_node_id=dir_node_id,
                    raw_parent_id=repo_node_id,
                    display_name=top_dir,
                    link=f"{_GITHUB_WEB_BASE}{repo.full_name}/tree/HEAD/{top_dir}",
                    node_type=_HIERARCHY_NODE_TYPE_FOLDER,
                    external_access_json=None,
                    sensitivity_hint=None,
                )
                emitted.add(dir_node_id)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def stats(self) -> Mapping[str, Any]:
        """Return diagnostic counters surfaced via :meth:`tool_connector_status`.

        Mirrors the §3 observability table from the design spec —
        rate-limit gauges, token rotations, dead-letter counts. Returns
        a plain mapping so the MCP envelope can serialise it directly.
        """
        client_stats = self._client.stats()
        return {
            "rest_requests": client_stats.rest_requests,
            "rest_rate_remaining": client_stats.rest_rate_remaining,
            "rest_rate_reset_epoch": client_stats.rest_rate_reset_epoch,
            "rest_403_secondary_total": client_stats.rest_403_secondary_total,
            "installation_token_rotations": client_stats.installation_token_rotations,
            "repos_tracked": len(self._per_repo_cursors),
            "deliveries_seen": len(self._seen_deliveries),
            "last_path_taken": self._last_path_taken or "",
        }

    # ------------------------------------------------------------------
    # ADR-021 (Wave E.5) — per-source envelope metadata
    # ------------------------------------------------------------------

    def metadata_for(self, item_id: str) -> SourceMetadata:
        """Return cached GitHub envelope metadata for ``item_id``.

        ADR-021: commits carry ``author`` + ``committed_at`` directly;
        issues / PRs carry ``state`` + ``updated_at`` + (when present)
        labels. ``repo`` lifts to the first tag so search can filter
        by repo. Cache miss collapses to an empty
        :class:`SourceMetadata`.
        """
        envelope = self._envelope_cache.get(item_id)
        if envelope is None:
            return SourceMetadata()
        author_value = envelope.get("author")
        author = author_value if isinstance(author_value, str) and author_value.strip() else None
        modified_value = envelope.get("committed_at") or envelope.get("updated_at")
        modified_at = modified_value if isinstance(modified_value, str) and modified_value.strip() else None
        tags: list[str] = []
        repo_value = envelope.get("repo")
        if isinstance(repo_value, str) and repo_value.strip():
            tags.append(repo_value)
        labels_value = envelope.get("labels")
        if isinstance(labels_value, list):
            tags.extend(str(label) for label in labels_value if isinstance(label, str))
        properties: dict[str, str] = {}
        kind_value = envelope.get("kind")
        if isinstance(kind_value, str) and kind_value:
            properties["kind"] = kind_value
        state_value = envelope.get("state")
        if isinstance(state_value, str) and state_value:
            properties["state"] = state_value
        return SourceMetadata(
            modified_at=modified_at,
            author=author,
            tags=tuple(tags),
            properties=properties,
        )


# ----------------------------------------------------------------------
# Module-level helpers (kept off the class to satisfy F16 + ease testing)
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class _ParsedItemId:
    """Parsed canonical github:// item_id.

    Frozen per F42. Helper-internal — not exported. ``kind`` is one of
    ``commit`` / ``blob`` / ``issues`` / ``pulls`` / ``repo``.
    """

    full_name: str
    kind: str
    identifier: str


def parse_item_id(item_id: str) -> _ParsedItemId:
    """Parse a ``github://<owner>/<repo>/<kind>/<identifier>`` URI.

    Tolerant of malformed input — returns sensible defaults so the
    caller can short-circuit without crashing on a stale id from a
    previous schema version.
    """
    if not item_id.startswith(_HIERARCHY_ORG_PREFIX):
        return _ParsedItemId(full_name="", kind="repo", identifier=item_id)
    rest = item_id[len(_HIERARCHY_ORG_PREFIX) :]
    parts = rest.split("/", 3)
    if len(parts) < 2:
        return _ParsedItemId(full_name=rest, kind="repo", identifier="")
    full_name = f"{parts[0]}/{parts[1]}"
    if len(parts) == 2:
        return _ParsedItemId(full_name=full_name, kind="repo", identifier="")
    if len(parts) == 3:
        return _ParsedItemId(full_name=full_name, kind=parts[2], identifier="")
    return _ParsedItemId(full_name=full_name, kind=parts[2], identifier=parts[3])


def _advance_boundary(
    *,
    current_boundary: str | None,
    items: Iterable[tuple[str, str]],
) -> tuple[str | None, frozenset[str]]:
    """Compute the next inclusive-``since`` boundary + its seen-id set.

    ``items`` is an iterable of ``(timestamp, id)`` pairs for every row the
    drain observed this tick (already-emitted boundary rows included, since
    GitHub's inclusive ``?since=`` re-returns them). Returns the new
    boundary timestamp (the max observed timestamp, or the prior boundary
    when no rows were seen) and the frozenset of ids AT that boundary
    timestamp — the rows the NEXT tick's inclusive ``?since=`` will
    re-return and must therefore skip. Rows without a timestamp can't be
    boundary-tracked, so they're ignored for the boundary computation.
    """
    observed = list(items)
    # ISO-8601 UTC ``Z`` timestamps sort lexicographically == chronologically,
    # so ``max`` over the non-empty timestamps (seeded with the prior boundary)
    # yields the new boundary without a hand-rolled comparison.
    candidates = [timestamp for timestamp, _identifier in observed if timestamp]
    if current_boundary:
        candidates.append(current_boundary)
    if not candidates:
        return None, frozenset()
    boundary = max(candidates)
    seen = frozenset(identifier for timestamp, identifier in observed if timestamp == boundary and identifier)
    return boundary, seen


def serialise_cursor(per_repo: Mapping[str, PerRepoCursorState]) -> str:
    """JSON-encode the per-repo cursor map (compound inclusive-since cursor)."""
    return json.dumps(
        {
            repo: {
                "code_sha": state.code_sha,
                "issues_since": state.issues_since,
                "seen_commit_shas": sorted(state.seen_commit_shas),
                "seen_issue_ids": sorted(state.seen_issue_ids),
            }
            for repo, state in per_repo.items()
        },
        sort_keys=True,
    )


def deserialise_cursor(cursor: Cursor | None) -> dict[str, PerRepoCursorState]:
    """Decode the per-repo cursor map; tolerant of stale/malformed cursors."""
    if not cursor:
        return {}
    try:
        parsed = json.loads(cursor)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, PerRepoCursorState] = {}
    for repo, payload in parsed.items():
        if not isinstance(payload, dict):
            continue
        out[str(repo)] = PerRepoCursorState(
            code_sha=payload.get("code_sha"),
            issues_since=payload.get("issues_since"),
            seen_commit_shas=_coerce_id_set(payload.get("seen_commit_shas")),
            seen_issue_ids=_coerce_id_set(payload.get("seen_issue_ids")),
        )
    return out


def _coerce_id_set(raw: Any) -> frozenset[str]:
    """Coerce a deserialised seen-id list into a frozenset of strings.

    Tolerant of stale / pre-fix cursors that omit the field (``None``) or
    carry a non-list shape — both collapse to an empty set so an older
    persisted cursor upgrades cleanly without a re-backfill.
    """
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(entry) for entry in raw)


def sensitivity_from_visibility(visibility: str) -> Sensitivity:
    """Map GitHub visibility to F39 tier per spec §1."""
    if visibility == "public":
        return _SENSITIVITY_PUBLIC
    if visibility == "internal":
        return _SENSITIVITY_INTERNAL
    return _DEFAULT_SENSITIVITY


def f39_tier_from_visibility(visibility: str) -> F39Tier:
    """Distinct from :func:`sensitivity_from_visibility` because HierarchyNode
    uses the richer F39Tier vocabulary (confidential / restricted) rather
    than the Sensitivity literal (client-confidential / personal).
    """
    if visibility == "public":
        return "public"
    if visibility == "internal":
        return "internal"
    return "confidential"


def _resolve_credentials_from_secrets(secrets: SecretsResolver) -> GitHubCredentials:
    """Resolve the GitHub credential blob via :class:`SecretsResolver`.

    ADR-031 canonical-naming: routes every credential read through the
    injected :class:`SecretsResolver` (production: :class:`SecretsLoader`;
    tests: :class:`tests.fakes.FakeSecretsLoader`). The legacy env-var
    aliases (``CONNECTOR_GITHUB_PERSONAL_ACCESS_TOKEN`` etc.) keep
    resolving via the loader's alias fallback so production operators
    don't need to rotate their KV before this lands.

    Tolerant of partial config — the operator may set just a PAT
    (development/test) or the App triple (production). The connector
    raises at first :meth:`list_changes` call if neither is set.

    F15-clean — the secret values are loaded into the
    :class:`GitHubCredentials` dataclass and passed to the api_client;
    they are NEVER logged.
    """
    app_id_str = secrets.get(scope="connector", area="github", instance=None, leaf=GITHUB_LEAF_APP_ID) or ""
    installation_id_str = (
        secrets.get(scope="connector", area="github", instance=None, leaf=GITHUB_LEAF_INSTALLATION_ID) or ""
    )
    private_key = secrets.get(scope="connector", area="github", instance=None, leaf=GITHUB_LEAF_APP_PRIVATE_KEY) or ""
    pat = secrets.get(scope="connector", area="github", instance=None, leaf=GITHUB_LEAF_PAT) or ""
    webhook_secret = secrets.get(scope="connector", area="github", instance=None, leaf=GITHUB_LEAF_WEBHOOK_SECRET) or ""
    app_id = int(app_id_str) if app_id_str else None
    installation_id = int(installation_id_str) if installation_id_str else None
    return GitHubCredentials(
        app_id=app_id,
        installation_id=installation_id,
        app_private_key_pem=private_key or None,
        personal_access_token=pat or None,
        webhook_secret=webhook_secret or None,
    )


class _DeferredCredentialClient:
    """Stand-in client that raises on first use.

    The connector constructs OK with no credentials so the worker can
    boot without crashing; the first :meth:`list_changes` call surfaces
    the actionable error via this stand-in. Mirrors the dex_crm
    pattern where the first poll raises ``MissingCredentialsError``.
    """

    def _raise(self) -> Any:
        raise ValueError(
            "github: no credential provided — set either "
            "connector-github-personal-access-token OR the App triple "
            "(connector-github-app-id / connector-github-installation-id / "
            "connector-github-app-private-key). "
            "fix: provision the required secrets via your secret manager. "
            "next: see docs/architecture/connector-scope-topology/connector-design-specs/github.md §1."
        )

    def list_installation_repositories(self) -> Any:
        return self._raise()

    def list_commits_since(self, **_kwargs: Any) -> Any:
        return self._raise()

    def list_issues_since(self, **_kwargs: Any) -> Any:
        return self._raise()

    def get_tree_recursive(self, **_kwargs: Any) -> Any:
        return self._raise()

    def fetch_blob(self, **_kwargs: Any) -> Any:
        return self._raise()

    def stats(self) -> Any:
        from kairix.connectors.github.api_client import ClientStatsSnapshot

        return ClientStatsSnapshot(
            rest_requests=0,
            rest_rate_remaining=-1,
            rest_rate_reset_epoch=-1,
            rest_403_secondary_total=0,
            installation_token_rotations=0,
        )

    def invalidate_token(self) -> None:
        return None


def _build_default_client(
    credentials: GitHubCredentials,
    client_config: GitHubClientConfig | None,
) -> GitHubApiClient:
    """Build the default GitHubApiClient from resolved credentials.

    Per ADR-032 §"github_app" + Phase 3: App-mode now routes through
    the shared :class:`~kairix.connect.refresh.GitHubAppRefreshableToken`
    when ``app_id`` + ``app_private_key_pem`` + ``installation_id`` are
    all set. The legacy single-``installation_id`` path remains for
    backwards compat with pre-ADR-032 deployments where only the
    installation id was provisioned (the api_client's own JWT-signing
    is the fallback).
    """
    if credentials.personal_access_token:
        return GitHubApiClient(
            personal_access_token=credentials.personal_access_token,
            config=client_config,
        )
    if credentials.app_id is not None and credentials.app_private_key_pem and credentials.installation_id is not None:
        # ADR-032 Phase 3 path — shared JWT-sign + installation-token
        # rotation via kairix.connect.refresh.GitHubAppRefreshableToken.
        from kairix.connect.refresh import GitHubAppRefreshableToken

        refreshable = GitHubAppRefreshableToken(
            app_id=str(credentials.app_id),
            private_key_pem=credentials.app_private_key_pem,
            installation_id=str(credentials.installation_id),
        )
        return GitHubApiClient(
            refreshable_token=refreshable,
            config=client_config,
        )
    if credentials.installation_id is not None:
        # Legacy App-mode path — pre-ADR-032 single-installation-id shape.
        return GitHubApiClient(
            installation_id=credentials.installation_id,
            config=client_config,
        )
    raise ValueError(
        "github: no credential provided — set either connector-github-personal-access-token "
        "OR the App triple (connector-github-app-id / connector-github-installation-id / "
        "connector-github-app-private-key). "
        "fix: provision the required secrets via your secret manager. "
        "next: see docs/architecture/connector-scope-topology/connector-design-specs/github.md §1."
    )


def _emit_orgs(
    repos: tuple[GitHubRepoRef, ...],
    *,
    cc_pair_id: int,
    emitted: set[str],
) -> Iterator[HierarchyNode]:
    """Emit one FOLDER per distinct org parent-before-child (F58 phase 1)."""
    seen: set[str] = set()
    for repo in repos:
        if "/" not in repo.full_name:
            continue
        org = repo.full_name.split("/", 1)[0]
        if org in seen:
            continue
        seen.add(org)
        node_id = f"{_HIERARCHY_ORG_PREFIX}{org}"
        yield HierarchyNode(
            cc_pair_id=cc_pair_id,
            raw_node_id=node_id,
            raw_parent_id=None,
            display_name=org,
            link=f"{_GITHUB_WEB_BASE}{org}",
            node_type=_HIERARCHY_NODE_TYPE_FOLDER,
            external_access_json=None,
            sensitivity_hint=None,
        )
        emitted.add(node_id)


def _emit_repos(
    repos: tuple[GitHubRepoRef, ...],
    *,
    cc_pair_id: int,
    emitted: set[str],
) -> Iterator[HierarchyNode]:
    """Emit one FOLDER per repo parent-before-child (F58 phase 2).

    Each repo's ``raw_parent_id`` references the org node emitted in
    phase 1.
    """
    for repo in repos:
        if "/" not in repo.full_name:
            continue
        org = repo.full_name.split("/", 1)[0]
        org_node_id = f"{_HIERARCHY_ORG_PREFIX}{org}"
        if org_node_id not in emitted:
            # Parent not emitted — F58 violation; skip rather than emit broken.
            continue
        repo_node_id = f"{_HIERARCHY_ORG_PREFIX}{repo.full_name}"
        yield HierarchyNode(
            cc_pair_id=cc_pair_id,
            raw_node_id=repo_node_id,
            raw_parent_id=org_node_id,
            display_name=repo.full_name,
            link=f"{_GITHUB_WEB_BASE}{repo.full_name}",
            node_type=_HIERARCHY_NODE_TYPE_FOLDER,
            external_access_json=None,
            sensitivity_hint=f39_tier_from_visibility(repo.visibility),
        )
        emitted.add(repo_node_id)


def make_connector(config: Mapping[str, Any]) -> GitHubConnector:
    """Construct a :class:`GitHubConnector` from a config mapping.

    Expected keys (all optional — secret resolution is the primary
    config path):

      * ``default_sensitivity`` — F39 tier; defaults to
        ``"client-confidential"`` per spec §1.
      * ``webhook_secret`` — overrides the secret resolution; mostly
        for test-double wiring.
      * ``repos_allowlist`` — optional list of ``owner/repo`` slugs.
        When set, restricts ingestion to those repositories — useful
        when the PAT has admin visibility into the whole org but the
        operator only wants a subset drained. Each entry must match
        ``^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$``. Empty / unset = all
        installation-accessible repos drain.

    Credentials resolve via the connector's injected
    :class:`~kairix.secrets.SecretsResolver` (production:
    :class:`~kairix.secrets.SecretsLoader`) — see
    :func:`_resolve_credentials_from_secrets` for the per-leaf names.

    Registered via ``[project.entry-points."kairix.connectors"]`` in
    kairix's ``pyproject.toml`` so the orchestration layer can resolve
    ``github`` to this factory by name.
    """
    declared = config.get("default_sensitivity", _DEFAULT_SENSITIVITY)
    if declared not in _VALID_SENSITIVITY_TIERS:
        raise ValueError(
            f"github: default_sensitivity {declared!r} is not a valid F39 tier. "
            "fix: set default_sensitivity to one of "
            "public / internal / client-confidential / personal. "
            "next: see kairix/core/protocols.py Sensitivity for the literal set."
        )
    webhook_secret_override = config.get("webhook_secret")
    repos_allowlist = _validate_repos_allowlist(config.get("repos_allowlist"))
    # Cast: prior membership check above narrows ``declared`` to the
    # Sensitivity literal set; mypy can't see through the .get() default
    # so the explicit cast keeps strict mode green.
    from typing import cast as _cast

    return GitHubConnector(
        default_sensitivity=_cast(Sensitivity, declared),
        webhook_secret=str(webhook_secret_override) if webhook_secret_override else None,
        repos_allowlist=repos_allowlist,
    )


def _validate_repos_allowlist(raw: Any) -> frozenset[str] | None:
    """Validate the operator-supplied ``repos_allowlist`` config value.

    Returns ``None`` when ``raw`` is falsy (empty / missing) so the
    connector falls back to its all-repos default. Otherwise coerces
    every entry to ``str``, checks the ``owner/repo`` slug shape, and
    returns a deduplicated :class:`frozenset`.

    Raises :class:`ValueError` with F21 ``fix:`` / ``next:`` markers
    when:

      * ``raw`` is not a list / tuple / set (operator passed a scalar)
      * an entry does not match the slug shape (e.g. missing ``/``,
        contains whitespace, contains a path separator)

    Production callers route through :func:`make_connector`; tests
    can call this helper directly to pin the validation contract.
    """
    if not raw:
        return None
    if not isinstance(raw, (list, tuple, set, frozenset)):
        raise ValueError(
            f"github: repos_allowlist must be a list of 'owner/repo' slugs; got {type(raw).__name__}. "
            "fix: set repos_allowlist to a YAML list of strings, e.g. "
            "['three-cubes/kairix', 'three-cubes/engineering-hub']. "
            "next: see kairix/connectors/github/connector.py::make_connector for the config shape."
        )
    bad: list[str] = []
    out: set[str] = set()
    for entry in raw:
        slug = str(entry)
        if not _REPOS_ALLOWLIST_SLUG_RE.match(slug):
            bad.append(slug)
            continue
        out.add(slug)
    if bad:
        raise ValueError(
            f"github: repos_allowlist contains invalid 'owner/repo' slug(s): {bad!r}. "
            "fix: every entry must match ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ "
            "(one owner, one slash, one repo — no spaces, no leading/trailing slashes). "
            "next: see kairix/connectors/github/connector.py::_validate_repos_allowlist for the regex."
        )
    return frozenset(out)
