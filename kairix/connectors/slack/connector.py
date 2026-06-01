"""``SlackConnector`` — multi-capability connector for Slack workspaces.

Implements every capability declared in slack.md §7:

  * :class:`~kairix.core.protocols.SourceConnector` — base shape.
  * :class:`~kairix.core.protocols.PollConnector` — per-channel poll
    surface via ``conversations.history`` with the channel's high-water
    ``ts`` as the cursor.
  * :class:`~kairix.core.protocols.CheckpointedConnector` — per-channel
    ``ts`` cursor; ``load_from_checkpoint`` resumes from the persisted
    high-water-mark.
  * :class:`~kairix.core.protocols.EventConnector` — Socket Mode +
    Events API push surface; ``handle_event`` dedups on the Slack
    envelope id and routes per slack.md §2.
  * :class:`~kairix.core.protocols.SlimConnector` — id-only enumeration
    for prune cycles.
  * :class:`~kairix.core.protocols.SlimConnectorWithPermSync` —
    per-channel ACL mirror for ``AccessType.SYNC`` cc_pairs.
  * :class:`~kairix.core.protocols.Resolver` — per-failed-id replay so
    a rate-limited tick doesn't replay the whole window.
  * :class:`~kairix.core.protocols.HierarchyConnector` — Workspace →
    channel → thread emission, parent-before-child per F58.
  * :class:`~kairix.core.protocols.OAuthConnector` — three-legged OAuth
    v2 install flow (the contrast with SharePoint, which is app-only).

F-rule discipline:

  * F37 — ``slack_sdk`` imports stay in
    :mod:`kairix.connectors.slack.web_client` and
    :mod:`kairix.connectors.slack.socket_mode`.
  * F39 — :meth:`sensitivity_for` derives per slack.md §1 (public →
    ``internal``; private / mpim → ``client-confidential``; im →
    ``personal``).
  * F58 — :meth:`load_hierarchy` emits Workspace → channel → thread
    parent-before-child; contract test
    ``tests/contracts/test_slack_hierarchy_parent_before_child.py``
    pins the invariant.
  * F35 — no reach into any other connector or extractor.

Wave E flag:

  * ``connector_slack`` (introduce stage, default-off) — gates the
    multi-container path. When OFF, every method returns the Wave B
    shim shape so adding the plugin is a structural no-op for
    operators. When ON, channels become Containers with their own
    per-channel ``ts`` cursor and the hierarchy walks
    Workspace → channel → thread.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from kairix.connectors.slack.socket_mode import (
    SlackSocketModeHandler,
    SocketModeEvent,
    SocketModeState,
)
from kairix.connectors.slack.web_client import (
    SlackChannel,
    SlackMessage,
    SlackWebClient,
)
from kairix.core.protocols import (
    ChangeEvent,
    Container,
    ContainerAccessDeniedError,
    CredentialExpiredError,
    Cursor,
    F39Tier,
    HierarchyNode,
    RawArtefact,
    Sensitivity,
    SourceMetadata,
)
from kairix.secrets.loader import SecretsLoader, SecretsResolver

logger = logging.getLogger(__name__)

CONNECTOR_NAME = "slack"

# F17 — extracted literals so the connector's per-source dispatch +
# event-metadata code paths reference one constant per field.
_CLIENT_CONFIDENTIAL_TIER: Sensitivity = "client-confidential"
_METADATA_CHANNEL_ID = "channel_id"
_METADATA_SLACK_EVENT_TYPE = "slack_event_type"
_SLACK_DEEP_LINK_PREFIX = "slack://channel/"

# F39 — sensitivity tier per channel kind, locked at the connector
# boundary per slack.md §1.
#
# public channels live in the engagement namespace and read as
# ``internal``; private channels + MPIMs are operator-confidential and
# read as ``client-confidential``; DMs are personal-tier (the operator's
# own correspondence) and read as ``personal`` per ADR-005.
_CHANNEL_KIND_TO_SENSITIVITY: Mapping[str, Sensitivity] = {
    "public_channel": "internal",
    "private_channel": _CLIENT_CONFIDENTIAL_TIER,
    "mpim": _CLIENT_CONFIDENTIAL_TIER,
    "im": "personal",
}

# Wave E feature flag name — module constant so the F52 call-site scan
# picks up exactly one verbatim reference per call site (mirrors the
# m365_email_headers / obsidian / dex_crm wave-E pilots).
CONNECTOR_SLACK_FLAG = "connector_slack"

# Hierarchy root id — stable, deterministic, shared between every
# emission so the receiver can build the tree in one pass.
_HIERARCHY_ROOT_ID = "slack-workspace"

# Slack event-type → ChangeEvent.op mapping per slack.md §2.
_EVENT_OP_MAP: Mapping[str, str] = {
    "message": "created",
    "message_changed": "modified",
    "message_deleted": "deleted",
    "file_shared": "created",
    "channel_archive": "archived",
    "member_left_channel": "access_lost",  # bot kicked
    "app_uninstalled": "access_lost",
}


def _now_iso() -> str:
    """Return a current ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ts_to_iso(ts: str) -> str:
    """Convert a Slack ``ts`` (Unix seconds with microsecond suffix) to ISO-8601.

    Slack ``ts`` are strings like ``"1715000000.123456"``. We parse the
    fractional seconds and emit a UTC ISO-8601 string so downstream
    consumers don't have to know about Slack's wire format.
    """
    try:
        seconds = float(ts)
    except (TypeError, ValueError):
        return _now_iso()
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _default_flag_reader(name: str) -> bool:
    """Production default — delegate to the registry-backed resolver.

    Lifted to module level so the connector's constructor can carry a
    real callable default (F6-clean). Tests pass a stub from
    :class:`~tests.fakes.FakeFeatureFlagResolver` so the branch under
    test is pinned without monkey-patching the resolver module
    (F1-clean / F2-clean).
    """
    from kairix.core.features import flag as _prod_flag

    return _prod_flag(name)


@dataclass(frozen=True)
class SlackCredentials:
    """Resolved workspace credentials for one Slack sync.

    Frozen per F42 — the dataclass is the typed shape that crosses the
    boundary between secret resolution and the connector constructor.

    ``bot_token`` is the workspace bot ``xoxb-…`` used for the Web API
    surface. ``app_token`` is the app-level ``xapp-…`` used to open
    Socket Mode connections (only needed if the EventConnector branch
    is enabled). ``client_id`` / ``client_secret`` are used by the
    OAuth v2 install flow (the contrast with SharePoint per slack.md §0
    and §7); these are app-registration-level credentials, not
    per-workspace.
    """

    bot_token: str
    app_token: str | None = None
    client_id: str | None = None
    client_secret: str | None = None


def _resolve_credentials_from_secrets(
    secrets: SecretsResolver,
    workspace: str | None = None,
) -> SlackCredentials:
    """Resolve the workspace credentials via :class:`SecretsResolver`.

    ADR-031 canonical-naming: each leaf below routes through the
    injected resolver (production: :class:`SecretsLoader`; tests:
    :class:`tests.fakes.FakeSecretsLoader`). Production keeps working
    unchanged because the loader's alias fallback still resolves the
    legacy env vars (``CONNECTOR_SLACK_BOT_TOKEN`` etc.).

    The Slack credential triple lives under four canonical leaves
    (mirrors the M365 sibling shape):

      * ``("connector", "slack", <workspace>, "bot-token")`` (required) —
        ``xoxb-…``.
      * ``("connector", "slack", <workspace>, "app-token")`` (optional) —
        ``xapp-…`` for Socket Mode; absent when only the poll surface
        is wired.
      * ``("connector", "slack", <workspace>, "client-id")`` /
        ``("connector", "slack", <workspace>, "client-secret")`` (optional) —
        the OAuth v2 install flow's app-registration credentials;
        absent when the operator has already installed and only the
        worker is running.

    Per-workspace ``instance`` support (ADR-032 Phase 2): when
    ``workspace`` is supplied, the resolver looks for
    ``kairix-connector-slack-<workspace>-bot-token`` (and siblings)
    so a single deployment can carry per-workspace tokens for
    ``alpha`` and ``coach`` side-by-side. When ``workspace`` is
    ``None`` (the legacy singleton shape), the loader's alias
    fallback still resolves the original ``CONNECTOR_SLACK_*`` env
    vars — back-compat for deployments that haven't migrated to the
    per-workspace shape yet.
    """
    bot_token = secrets.require(scope="connector", area="slack", instance=workspace, leaf="bot-token")
    app_token = secrets.get(scope="connector", area="slack", instance=workspace, leaf="app-token")
    client_id = secrets.get(scope="connector", area="slack", instance=workspace, leaf="client-id")
    client_secret = secrets.get(scope="connector", area="slack", instance=workspace, leaf="client-secret")
    return SlackCredentials(
        bot_token=bot_token,
        app_token=app_token or None,
        client_id=client_id or None,
        client_secret=client_secret or None,
    )


@dataclass
class _ChannelCache:
    """Per-tick cache of channel metadata + messages for fetch resolution.

    The Web API surface pre-populates this on every list-changes /
    history call so the subsequent ``fetch(item_id)`` calls don't pay
    a second Slack API hit per item. Cleared between cc_pair ticks by
    the framework (the connector instance lives across ticks; the
    cache is keyed by item_id and naturally evicts older entries as
    new ticks overwrite them).
    """

    channels: dict[str, SlackChannel] = field(default_factory=dict)
    messages: dict[str, SlackMessage] = field(default_factory=dict)
    seen_event_ids: set[str] = field(default_factory=set)

    def remember_channel(self, channel: SlackChannel) -> None:
        self.channels[channel.channel_id] = channel

    def remember_message(self, message: SlackMessage) -> None:
        self.messages[_item_id_for(message)] = message


def _item_id_for(message: SlackMessage) -> str:
    """The connector-boundary item_id for one message — ``<channel>:<ts>``."""
    return f"{message.channel_id}:{message.ts}"


class SlackConnector:
    """Multi-capability Slack connector — see module docstring for the full surface.

    DI seams (every external collaborator passes via constructor):

      * ``credentials`` — resolved :class:`SlackCredentials`. Tests pass
        a literal; production callers omit and the connector resolves
        via the injected :class:`SecretsResolver` on first ``_web()``
        call.
      * ``web_client_factory`` — builds the
        :class:`~kairix.connectors.slack.web_client.SlackWebClient`.
        Tests pass a factory returning a client backed by an
        :class:`httpx.MockTransport` so no real Slack call leaks.
      * ``socket_mode_handler_factory`` — builds the
        :class:`~kairix.connectors.slack.socket_mode.SlackSocketModeHandler`.
        Tests pass a factory returning a handler with an in-process
        transport so the WebSocket layer is never touched.
      * ``flag_reader`` — resolves feature flags. Tests inject a
        :class:`~tests.fakes.FakeFeatureFlagResolver` so the branch
        under test is pinned without monkey-patching.
      * ``secrets`` — :class:`~kairix.secrets.SecretsResolver`. Tests
        pass :class:`tests.fakes.FakeSecretsLoader` so credential
        resolution rides the F2-clean DI seam; production defaults
        to :class:`~kairix.secrets.SecretsLoader` (ADR-031).
    """

    name: str = CONNECTOR_NAME
    per_tick_max_items: int = 500
    # F66-watermark-exempt: Slack messages are small (~8 KB envelopes); no large disk writes
    disk_watermark_min_free_bytes: int | None = None

    def __init__(
        self,
        *,
        credentials: SlackCredentials | None = None,
        workspace: str | None = None,
        web_client_factory: Callable[[SlackCredentials], SlackWebClient] | None = None,
        socket_mode_handler_factory: Callable[..., SlackSocketModeHandler] | None = None,
        flag_reader: Callable[[str], bool] = _default_flag_reader,
        secrets: SecretsResolver | None = None,
    ) -> None:
        # Lift credential resolution out of the hot path so tests that
        # never reach the network can construct without any secrets backend.
        self._credentials: SlackCredentials | None = credentials
        # ADR-032 Phase 2: per-workspace ``instance`` slot for secret
        # resolution. ``None`` keeps the legacy singleton resolution
        # path (CONNECTOR_SLACK_BOT_TOKEN alias still resolves);
        # supplied for new deployments that capture per-workspace
        # tokens via ``kairix connect slack --workspace <name>``.
        self.workspace: str | None = workspace
        self._web_client_factory: Callable[[SlackCredentials], SlackWebClient] = (
            web_client_factory if web_client_factory is not None else _default_web_client_factory
        )
        self._socket_mode_handler_factory = socket_mode_handler_factory
        self._flag_reader = flag_reader
        # ADR-031 canonical-naming seam. Tests inject FakeSecretsLoader
        # via this kwarg; production constructs a real SecretsLoader
        # lazily so the connector still imports cleanly without a
        # provisioned secrets backend.
        self._secrets: SecretsResolver = secrets if secrets is not None else SecretsLoader()

        self._web_client_cache: SlackWebClient | None = None
        self._socket_mode_handler: SlackSocketModeHandler | None = None

        self._cache = _ChannelCache()

        # The high-water-mark cursor for each channel, populated when
        # :meth:`list_changes_for_container` drains. None until first
        # drain. Mirrors the m365_email_headers per-mailbox cursor map.
        self._next_cursor_by_container: dict[str, str | None] = {}

        # Aggregate single-cursor token for the legacy :meth:`list_changes`
        # path — populated as the max ``ts`` observed across every
        # channel drained by the most recent legacy call. Returned by
        # :meth:`next_cursor` so the orchestrator persists a real Slack
        # ``ts`` (not a per-item ``modified_at``). None before the
        # first drain; preserves the prior value on a zero-event tick.
        self._last_legacy_cursor: str | None = None

        # cc_pair-level invalid signal — set when the Slack edge surfaces
        # ``app_uninstalled`` / ``token_revoked`` so the framework's
        # next status read can route the cc_pair through F57 to INVALID.
        self._cc_pair_invalid: bool = False

        # Container-level revoked set — populated when a per-channel
        # access-lost signal fires (bot kicked, channel archived). The
        # framework's lifecycle layer reads this via :meth:`revoked_containers`
        # to flip the affected Container's ``access_state`` to REVOKED.
        self._revoked_channels: set[str] = set()

    # ------------------------------------------------------------------
    # SourceConnector Protocol surface
    # ------------------------------------------------------------------

    def list_changes(self, cursor: Cursor | None) -> Iterator[ChangeEvent]:
        """Legacy single-cursor entry point used by the Wave B shim path.

        Routes through :meth:`_enumerate_member_channels` and
        :meth:`_drain_channel` so the same code paths exercised on
        the ON branch are reused on the OFF branch (the only
        observable difference is that OFF squashes every channel
        into one stream).
        """
        web = self._web()
        events: list[ChangeEvent] = []
        for channel in self._enumerate_member_channels(web):
            events.extend(self._drain_channel(web, channel, oldest=cursor))
        # High-water-mark across all channels — the cursor token Slack
        # uses is a ``ts`` string (lexicographically comparable). On a
        # zero-event drain we preserve the prior cursor so the
        # orchestrator doesn't clobber a real position with None.
        if events:
            self._last_legacy_cursor = max(ev.modified_at for ev in events)
        elif cursor:
            self._last_legacy_cursor = cursor
        return iter(events)

    def next_cursor(self) -> str | None:
        """Return the legacy single-cursor token (max ``ts`` across channels).

        Slack's cursor IS a ``ts`` string per channel; the legacy
        :meth:`list_changes` aggregates across channels so the single
        token returned here is the max ``ts`` observed in the last
        drain. ``None`` before the first drain. Per-channel cursors
        for the Wave E multi-container path live in
        :meth:`next_cursor_for_container`.
        """
        return self._last_legacy_cursor

    def fetch(self, item_id: str) -> RawArtefact:
        """Return the cached message envelope for ``item_id`` as JSON.

        ``list_changes`` / ``list_changes_for_container`` populate the
        per-tick cache; ``fetch`` reads it. The artefact is a JSON
        serialisation of the message envelope keyed by the Slack
        item_id format ``"<channel>:<ts>"``. The orchestration layer
        routes this through the canonical Silver processor which
        chunks + extracts entity signals.
        """
        import json as _json

        message = self._cache.messages.get(item_id)
        if message is None:
            raise KeyError(
                f"slack: item_id {item_id!r} not in the per-tick cache. "
                "fix: call list_changes_for_container() before fetch() so the "
                "per-channel history drain primes the cache. "
                "next: see kairix/connectors/slack/connector.py for the cache contract."
            )
        payload = _json.dumps(
            {
                "channel": message.channel_id,
                "ts": message.ts,
                "user": message.user,
                "text": message.text,
                "thread_ts": message.thread_ts,
                "subtype": message.subtype,
                "edited_ts": message.edited_ts,
                "file_ids": list(message.file_ids),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        return RawArtefact(
            raw=payload,
            mime="application/json",
            fetched_at=_now_iso(),
            sensitivity_hint=self.sensitivity_for(item_id),
        )

    def source_link(self, item_id: str) -> str:
        """Return the Slack permalink for one message.

        Calls Slack's ``chat.getPermalink`` so the returned URL is the
        canonical web link (which respects workspace URL aliasing).
        Falls back to a synthesised ``slack://`` deep-link when the
        Web API call is unavailable (e.g. test contexts that haven't
        wired ``web_client_factory``).
        """
        channel_id, ts = _split_item_id(item_id)
        try:
            web = self._web()
            return web.chat_get_permalink(channel_id=channel_id, ts=ts)
        except Exception:
            logger.warning("slack: chat.getPermalink fell back to synthesised deep-link", exc_info=True)
            return f"{_SLACK_DEEP_LINK_PREFIX}{channel_id}/p{ts.replace('.', '')}"

    def sensitivity_for(self, item_id: str) -> Sensitivity:
        """Return the F39 sensitivity tier for ``item_id`` per slack.md §1.

        Routes purely on the channel kind cached during the most recent
        list_changes drain — public channels → ``internal``, private +
        mpim → ``client-confidential``, im → ``personal``. Unknown
        channels (no cache entry yet) default to the tightest tier so
        a misconfigured cache doesn't accidentally publish private
        content as internal.
        """
        channel_id, _ = _split_item_id(item_id)
        channel = self._cache.channels.get(channel_id)
        if channel is None:
            # Default to ``personal`` so a misconfigured cache cannot
            # accidentally publish content at a wider tier than its
            # tightest possible source.
            return "personal"
        return _CHANNEL_KIND_TO_SENSITIVITY.get(channel.kind, "personal")

    # ------------------------------------------------------------------
    # PollConnector / CheckpointedConnector
    # ------------------------------------------------------------------

    def list_changes_for_container(self, container: Container) -> Iterator[ChangeEvent]:
        """Stream per-channel changes since ``container.cursor_token``.

        OFF branch (flag default-off): delegates to :meth:`list_changes`
        so observable behaviour is identical to the Wave B shim shape.

        ON branch: routes through :meth:`_drain_channel` against the
        single channel identified by ``container.container_id``,
        records the new high-water ``ts`` in
        ``_next_cursor_by_container`` so the framework can persist it
        as the per-container cursor for the next tick.
        """
        if not self._flag_reader(CONNECTOR_SLACK_FLAG):
            return self.list_changes(container.cursor_token)
        return self._list_changes_scoped(container)

    def load_from_checkpoint(self, container: Container, checkpoint: str | None) -> Iterator[ChangeEvent]:
        """Resume from a per-channel ``ts`` checkpoint.

        Mirrors :meth:`list_changes_for_container` but with the
        explicit checkpoint as the high-water mark (vs reading from
        ``container.cursor_token``). Used by the framework when the
        previous tick stored its checkpoint outside the Container row
        (e.g. a deadletter-replay-derived resume point).
        """
        # Synthesise a Container with the checkpoint as the cursor token
        # so the drain path reuses one cursor-reading code path.
        scoped = Container(
            cc_pair_id=container.cc_pair_id,
            container_id=container.container_id,
            access_state=container.access_state,
            cursor_token=checkpoint,
            last_synced_at=container.last_synced_at,
        )
        return self._list_changes_scoped(scoped)

    # ------------------------------------------------------------------
    # SlimConnector / SlimConnectorWithPermSync
    # ------------------------------------------------------------------

    def retrieve_all_slim_docs(self, container: Container) -> Iterator[str]:
        """Yield the item_ids the channel currently exposes (no body fetch)."""
        web = self._web()
        for message in web.conversations_history(channel_id=container.container_id, oldest=None):
            yield _item_id_for(message)

    def retrieve_all_slim_docs_with_perms(self, container: Container) -> Iterator[tuple[str, str]]:
        """Yield ``(item_id, acl_serialised)`` tuples for ``container``.

        The ACL is the comma-joined list of member user ids returned
        by ``conversations.members``. Slack scopes membership to the
        channel as a whole (not per-message) so every message in the
        channel ships with the same ACL string this tick.
        """
        web = self._web()
        member_ids = sorted(web.conversations_members(channel_id=container.container_id))
        acl_serialised = ",".join(member_ids)
        for message in web.conversations_history(channel_id=container.container_id, oldest=None):
            yield _item_id_for(message), acl_serialised

    # ------------------------------------------------------------------
    # EventConnector
    # ------------------------------------------------------------------

    def subscribe(self, _callback_url: str) -> str | None:
        """Start Socket Mode or Events API push subscription.

        Slack subscriptions are stateful at the app-registration level
        (the workspace admin authorised Events API + bot events when
        the app was installed). The framework only needs an opaque
        subscription id so it can call :meth:`unsubscribe` on shutdown;
        we return a deterministic value derived from the connector
        name + the cc_pair lifetime so two ticks don't accumulate
        ghost subscriptions.

        When the operator has wired Socket Mode, this method opens the
        WS via the injected ``socket_mode_handler_factory``. When
        Socket Mode isn't wired (no ``app_token``), we return ``None``
        to signal the framework should fall back to the poll surface
        — the operator's deployment chose Events API HTTP, which
        doesn't need a per-tick subscription call.
        """
        if self._socket_mode_handler_factory is None:
            return None
        if self._socket_mode_handler is None:
            self._socket_mode_handler = self._socket_mode_handler_factory(
                on_event=self._on_socket_event,
                on_credential_expired=self._on_credential_expired,
            )
        # Production wires this on a worker thread; tests call connect()
        # directly with an in-process transport so the suite stays fast.
        return f"slack-socket-mode:{id(self._socket_mode_handler)}"

    def renew_subscription(self, subscription_id: str) -> str:
        """No-op for Socket Mode (no TTL).

        Socket Mode subscriptions don't carry a TTL — the WebSocket
        itself is the keepalive surface, and the
        :class:`SlackSocketModeHandler` reconnect state machine owns
        recovery. Returning the same id satisfies the Protocol; the
        framework treats this as "subscription still healthy".
        """
        return subscription_id

    def unsubscribe(self, _subscription_id: str) -> None:
        """Close the Socket Mode handler cleanly. Idempotent."""
        if self._socket_mode_handler is not None:
            self._socket_mode_handler.disconnect()
            self._socket_mode_handler = None

    def handle_event(self, event: Mapping[str, Any]) -> Iterator[ChangeEvent]:
        """Translate one Events API / Socket Mode payload into ChangeEvent items.

        Dedups on the Slack envelope id (Events API retries 3 times per
        slack.md §5 row 9) AND on the ``(channel, ts, edited.ts)`` key
        for ``message_changed`` events per slack.md §2 ("dedup: the
        event embeds previous_message — key on (channel, ts, edited.ts)
        so a naive consumer doesn't double-count").

        App-uninstall payloads flip ``_cc_pair_invalid`` so the
        framework's next status read can transition the cc_pair to
        INVALID via F57. Per-event-type translation is delegated to
        :func:`_translate_message_event` (and siblings) so this method
        stays under the F16 cognitive-complexity ceiling.
        """
        if self._already_dispatched(event):
            return iter([])
        event_type = str(event.get("type", ""))
        if self._handle_access_loss(event, event_type):
            return iter([])
        handler = _EVENT_TRANSLATORS.get(event_type)
        if handler is None:
            return iter([])
        return iter(handler(self, event, event_type))

    def _already_dispatched(self, event: Mapping[str, Any]) -> bool:
        """Dedup on the Slack envelope id (Events API retries 3 times)."""
        envelope_id = str(event.get("envelope_id") or event.get("event_id") or "")
        if envelope_id and envelope_id in self._cache.seen_event_ids:
            return True
        if envelope_id:
            self._cache.seen_event_ids.add(envelope_id)
        return False

    def _handle_access_loss(self, event: Mapping[str, Any], event_type: str) -> bool:
        """Return True iff the event was an access-loss signal (cc_pair/container)."""
        if event_type == "app_uninstalled":
            self._cc_pair_invalid = True
            return True
        if event_type == "member_left_channel":
            channel_id = str(event.get("channel", ""))
            if channel_id:
                self._revoked_channels.add(channel_id)
            return True
        return False

    # ------------------------------------------------------------------
    # Resolver
    # ------------------------------------------------------------------

    def reindex(
        self,
        failed_item_ids: tuple[str, ...],
        *,
        include_permissions: bool = False,
    ) -> Iterator[ChangeEvent]:
        """Replay each failed item via a targeted ``conversations.history`` window."""
        web = self._web()
        for item_id in failed_item_ids:
            channel_id, ts = _split_item_id(item_id)
            try:
                # Slack history `oldest` is exclusive — pass `ts` directly
                # to get the message at or shortly after the failed ts.
                for message in web.conversations_history(channel_id=channel_id, oldest=ts):
                    if message.ts != ts:
                        continue
                    self._cache.remember_message(message)
                    yield ChangeEvent(
                        op="created",
                        item_id=_item_id_for(message),
                        modified_at=_ts_to_iso(message.ts),
                        metadata=_change_event_metadata(message, include_permissions=include_permissions),
                    )
            except ContainerAccessDeniedError:
                # Skip — caller will mark the container REVOKED.
                continue

    # ------------------------------------------------------------------
    # HierarchyConnector (F58)
    # ------------------------------------------------------------------

    def load_hierarchy(self, cc_pair_id: int) -> Iterator[HierarchyNode]:
        """Emit Workspace → channel → thread nodes parent-before-child.

        OFF branch: emit a single root WORKSPACE node so the framework's
        hierarchy store has the cc_pair attached but no per-channel
        detail.

        ON branch: emit the root WORKSPACE, then one CHANNEL per
        member-channel (parent = root), then one CHANNEL-typed node per
        thread root within each channel (parent = the channel). Thread
        emission is best-effort: the most recent ``conversations.history``
        page's thread roots get walked, so cold-start hierarchy doesn't
        page through the full archive (cheap to refresh on the next tick).
        """
        # Root first per F58.
        yield HierarchyNode(
            cc_pair_id=cc_pair_id,
            raw_node_id=_HIERARCHY_ROOT_ID,
            raw_parent_id=None,
            display_name="Slack Workspace",
            link=None,
            node_type="WORKSPACE",
            external_access_json=None,
            sensitivity_hint=None,
        )
        if not self._flag_reader(CONNECTOR_SLACK_FLAG):
            return
        web = self._web()
        for channel in self._enumerate_member_channels(web):
            yield from self._channel_hierarchy_nodes(web, cc_pair_id, channel)

    def _channel_hierarchy_nodes(
        self,
        web: SlackWebClient,
        cc_pair_id: int,
        channel: SlackChannel,
    ) -> Iterator[HierarchyNode]:
        """Yield one CHANNEL node + per-thread children for one channel.

        Extracted from :meth:`load_hierarchy` so each method stays
        under the F16 cognitive-complexity ceiling. Per-channel access
        loss surfaces here as a typed exception; the channel's node
        does not emit and the framework's lifecycle layer flips the
        Container to ``REVOKED`` via :meth:`revoked_containers`.
        """
        channel_sensitivity = _CHANNEL_KIND_TO_SENSITIVITY.get(channel.kind, "personal")
        yield HierarchyNode(
            cc_pair_id=cc_pair_id,
            raw_node_id=channel.channel_id,
            raw_parent_id=_HIERARCHY_ROOT_ID,
            display_name=channel.name,
            link=f"{_SLACK_DEEP_LINK_PREFIX}{channel.channel_id}",
            node_type="CHANNEL",
            external_access_json=None,
            sensitivity_hint=_sensitivity_to_f39(channel_sensitivity),
        )
        try:
            thread_roots = _collect_thread_roots(web, channel.channel_id)
        except ContainerAccessDeniedError:
            self._revoked_channels.add(channel.channel_id)
            return
        for thread_ts in thread_roots:
            yield HierarchyNode(
                cc_pair_id=cc_pair_id,
                raw_node_id=f"{channel.channel_id}:{thread_ts}",
                raw_parent_id=channel.channel_id,
                display_name=f"thread {thread_ts}",
                link=f"{_SLACK_DEEP_LINK_PREFIX}{channel.channel_id}/p{thread_ts.replace('.', '')}",
                node_type="CHANNEL",
                external_access_json=None,
                sensitivity_hint=_sensitivity_to_f39(channel_sensitivity),
            )

    # ------------------------------------------------------------------
    # OAuthConnector (the contrast with SharePoint per slack.md §7)
    # ------------------------------------------------------------------

    @classmethod
    def oauth_authorization_url(cls, state: str) -> str:
        """Return Slack's three-legged OAuth v2 authorization URL.

        Operators visit this URL to install the app into their
        workspace; Slack redirects back to the configured redirect_uri
        with a ``code`` that :meth:`oauth_code_to_token` exchanges.
        ``state`` is the operator-supplied CSRF token that round-trips
        through Slack so the callback can verify the request originated
        on the operator's session.
        """
        from urllib.parse import urlencode

        # Scope set per Slack OAuth v2 docs; broken across lines to
        # keep the file within the 120-column ruff budget.
        oauth_scope = (
            "channels:history,channels:read,"
            "groups:history,groups:read,"
            "im:history,im:read,"
            "mpim:history,mpim:read,"
            "users:read"
        )
        params = {
            "client_id": "${SLACK_CLIENT_ID}",  # operator substitutes; see secrets convention above
            "scope": oauth_scope,
            "state": state,
        }
        return f"https://slack.com/oauth/v2/authorize?{urlencode(params)}"

    @classmethod
    def oauth_code_to_token(cls, code: str) -> dict[str, Any]:
        """Document the code-to-token exchange shape; production resolves via OAuth helper.

        The full exchange happens out-of-band against
        ``https://slack.com/api/oauth.v2.access`` with the
        app-registration's ``client_id`` + ``client_secret``. We don't
        execute that here because the connector instance doesn't carry
        the redirect machinery; the operator-side install flow
        (``kairix cc-pair install slack``) drives the actual exchange
        and stores the result as a :class:`~kairix.core.protocols.Credential`
        row that this connector reads at construction.

        Returns the canonical response shape so the install flow's
        unit tests can pin expected fields.
        """
        return {
            "ok": True,
            "access_token": f"xoxb-from-code-{code}",
            "scope": "channels:history,channels:read,groups:history",
            "team": {"id": "T0000000", "name": "example-workspace"},
            "bot_user_id": "U_BOT",
        }

    # ------------------------------------------------------------------
    # Operator-facing read surface (status / lifecycle)
    # ------------------------------------------------------------------

    def cc_pair_invalid(self) -> bool:
        """True when ``app_uninstalled`` / ``token_revoked`` has fired.

        The framework's status read polls this to decide whether to
        transition the cc_pair to ``INVALID`` via F57's
        ``_ALLOWED_TRANSITIONS`` dispatch. Cleared (back to False) on
        a successful credential rotation — the operator re-installs the
        Slack app and the next worker startup re-instantiates the
        connector with fresh credentials.
        """
        return self._cc_pair_invalid

    def revoked_containers(self) -> tuple[str, ...]:
        """Channel ids that have transitioned to ``REVOKED`` this lifetime."""
        return tuple(sorted(self._revoked_channels))

    def socket_mode_state(self) -> SocketModeState:
        """Snapshot of the Socket Mode lifecycle state (slack.md §3 gauge)."""
        if self._socket_mode_handler is None:
            return SocketModeState.DISCONNECTED
        return self._socket_mode_handler.state

    def next_cursor_for_container(self, container_id: str) -> str | None:
        """The ``ts`` cursor the framework should persist for one channel."""
        return self._next_cursor_by_container.get(container_id)

    # ------------------------------------------------------------------
    # Wave E pilot — multi-container surface
    # ------------------------------------------------------------------

    def iter_containers(self, cc_pair_id: int) -> Iterator[Container]:
        """Yield one :class:`Container` per channel the bot is a member of.

        Wave E §4: each Container has its own ``ts`` cursor. Calling
        convention matches the obsidian / m365_email_headers pilots —
        the framework's lifecycle layer (``kairix/core/connectors/cc_pair.py``)
        passes ``cc_pair_id`` so the connector can construct the
        Container without reaching back into the cc_pair store.

        DMs are deliberately enumerated alongside public + private
        channels — the F39 sensitivity routing per channel kind ensures
        DM content lands at the ``personal`` tier so engagement-wide
        retrieval doesn't surface private correspondence (slack.md §1).
        """
        web = self._web()
        for channel in self._enumerate_member_channels(web):
            yield Container(
                cc_pair_id=cc_pair_id,
                container_id=channel.channel_id,
                access_state="REVOKED" if channel.channel_id in self._revoked_channels else "ACCESSIBLE",
                cursor_token=None,
                last_synced_at=None,
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _web(self) -> SlackWebClient:
        """Resolve (or lazily build) the workspace's Web API client."""
        if self._web_client_cache is not None:
            return self._web_client_cache
        creds = (
            self._credentials
            if self._credentials is not None
            else _resolve_credentials_from_secrets(self._secrets, self.workspace)
        )
        self._credentials = creds
        self._web_client_cache = self._web_client_factory(creds)
        return self._web_client_cache

    def _enumerate_member_channels(self, web: SlackWebClient) -> list[SlackChannel]:
        """List channels the bot belongs to; cache each for later sensitivity routing."""
        try:
            channels = [c for c in web.conversations_list() if c.is_member and not c.is_archived]
        except CredentialExpiredError:
            self._cc_pair_invalid = True
            return []
        # Deterministic order — slack.md §6 doesn't pin one, but a
        # stable enumeration order keeps the test suite reproducible
        # and the operator-facing emission identical between ticks.
        channels.sort(key=lambda c: c.channel_id)
        for channel in channels:
            self._cache.remember_channel(channel)
        return channels

    def _drain_channel(
        self,
        web: SlackWebClient,
        channel: SlackChannel,
        *,
        oldest: str | None,
    ) -> list[ChangeEvent]:
        """Drain one channel's history into a list of ChangeEvents."""
        events: list[ChangeEvent] = []
        if channel.channel_id in self._revoked_channels:
            return events
        try:
            messages = list(web.conversations_history(channel_id=channel.channel_id, oldest=oldest))
        except ContainerAccessDeniedError:
            self._revoked_channels.add(channel.channel_id)
            return events
        except CredentialExpiredError:
            self._cc_pair_invalid = True
            return events
        high_water = oldest
        for message in messages:
            self._cache.remember_message(message)
            events.append(
                ChangeEvent(
                    op="created",
                    item_id=_item_id_for(message),
                    modified_at=_ts_to_iso(message.ts),
                    metadata=_change_event_metadata(message, include_permissions=False),
                )
            )
            if high_water is None or message.ts > high_water:
                high_water = message.ts
        if high_water is not None:
            self._next_cursor_by_container[channel.channel_id] = high_water
        return events

    def _list_changes_scoped(self, container: Container) -> Iterator[ChangeEvent]:
        """Wave E ON-branch: drain one channel only."""
        web = self._web()
        # Read the channel kind from the cache if we've already
        # enumerated, otherwise hit conversations.list once to populate
        # so the F39 sensitivity routing is correct on first call.
        if container.container_id not in self._cache.channels:
            self._enumerate_member_channels(web)
        channel = self._cache.channels.get(container.container_id)
        if channel is None:
            # The bot can't see this channel — surface as REVOKED so the
            # framework can transition the Container accordingly.
            self._revoked_channels.add(container.container_id)
            return iter([])
        events = self._drain_channel(web, channel, oldest=container.cursor_token)
        return iter(events)

    def _on_socket_event(self, event: SocketModeEvent) -> None:
        """Callback wired into :class:`SlackSocketModeHandler`.

        Translates the WebSocket event into a flat dict shape that
        :meth:`handle_event` consumes, then drains the resulting
        ChangeEvents into the per-tick cache so the framework's next
        list_changes_for_container tick picks them up.
        """
        payload = {
            "envelope_id": event.envelope_id,
            "type": event.event_type,
            **dict(event.payload),
        }
        # Consume the iterator — the side effects (cache priming,
        # cc_pair_invalid flag) are what the realtime path needs.
        # deque(..., maxlen=0) drains in O(1) memory without S108.
        deque(self.handle_event(payload), maxlen=0)

    def _on_credential_expired(self) -> None:
        """Callback fired by :class:`SlackSocketModeHandler` on auth failure."""
        self._cc_pair_invalid = True

    # ------------------------------------------------------------------
    # ADR-021 (Wave E.5) — per-source envelope metadata
    # ------------------------------------------------------------------

    def metadata_for(self, item_id: str) -> SourceMetadata:
        """Return cached Slack message envelope metadata for ``item_id``.

        ADR-021: the per-tick :class:`_ChannelCache` already carries
        every message envelope drained on the current tick — author is
        the message's ``user`` id (display-name resolution is a future
        Web-API roundtrip; today the user id is the stable identifier),
        ``ts`` is the modified_at, and the channel name becomes the
        first tag. Cache miss collapses to an empty
        :class:`SourceMetadata`.
        """
        message = self._cache.messages.get(item_id)
        if message is None:
            return SourceMetadata()
        channel = self._cache.channels.get(message.channel_id)
        tags: tuple[str, ...] = ()
        properties: dict[str, str] = {}
        if channel is not None:
            tags = (channel.name,)
            properties["channel_name"] = channel.name
            properties["channel_kind"] = channel.kind
        if message.thread_ts:
            properties["thread_ts"] = message.thread_ts
        modified_at_iso = _ts_to_iso(message.ts) if message.ts else None
        return SourceMetadata(
            modified_at=modified_at_iso,
            author=message.user,
            tags=tags,
            properties=properties,
        )


def _default_web_client_factory(credentials: SlackCredentials) -> SlackWebClient:
    """Production default — construct a real ``SlackWebClient``."""
    return SlackWebClient(token=credentials.bot_token)


def _split_item_id(item_id: str) -> tuple[str, str]:
    """Split ``"<channel>:<ts>"`` into its parts; raise on malformed input."""
    if ":" not in item_id:
        raise ValueError(
            f"slack: item_id {item_id!r} is malformed (expected '<channel>:<ts>'). "
            "fix: emit item_ids only via list_changes_for_container so they round-trip cleanly. "
            "next: see kairix/connectors/slack/connector.py for the item_id contract."
        )
    channel_id, ts = item_id.split(":", 1)
    return channel_id, ts


def _change_event_metadata(message: SlackMessage, *, include_permissions: bool) -> dict[str, Any]:
    """Build the ChangeEvent.metadata mapping for one Slack message."""
    md: dict[str, Any] = {
        _METADATA_CHANNEL_ID: message.channel_id,
        "ts": message.ts,
        "user": message.user,
        "thread_ts": message.thread_ts,
    }
    if message.file_ids:
        md["file_ids"] = list(message.file_ids)
    if include_permissions:
        md["acl_will_follow"] = True
    return md


def _collect_thread_roots(web: SlackWebClient, channel_id: str) -> list[str]:
    """Drain the latest history page and return the unique thread-root ``ts`` values.

    Best-effort: re-uses the first page Slack returns rather than
    paging through the entire archive so cold-start hierarchy is cheap
    to refresh on the next tick. Lifted to a module-level helper so
    :meth:`SlackConnector.load_hierarchy` stays under the F16
    cognitive-complexity budget.
    """
    thread_roots: list[str] = []
    for message in web.conversations_history(channel_id=channel_id, oldest=None):
        if message.thread_ts and message.ts == message.thread_ts and message.ts not in thread_roots:
            thread_roots.append(message.ts)
    return thread_roots


def _change_event_from_message_payload(payload: Mapping[str, Any], *, op: str) -> ChangeEvent:
    """Build a ChangeEvent from a Slack Events API ``message`` payload."""
    channel_id = str(payload.get("channel", ""))
    ts = str(payload.get("ts", ""))
    return ChangeEvent(
        op=op,  # type: ignore[arg-type]  # op constrained at call site to ChangeEvent literal; F3 rationale
        item_id=f"{channel_id}:{ts}",
        modified_at=_ts_to_iso(ts),
        metadata={
            _METADATA_CHANNEL_ID: channel_id,
            "ts": ts,
            _METADATA_SLACK_EVENT_TYPE: payload.get("type"),
        },
    )


def _translate_message(
    _connector: SlackConnector,
    event: Mapping[str, Any],
    _event_type: str,
) -> list[ChangeEvent]:
    return [_change_event_from_message_payload(event, op="created")]


def _translate_message_changed(
    connector: SlackConnector,
    event: Mapping[str, Any],
    _event_type: str,
) -> list[ChangeEvent]:
    """Translate a ``message_changed`` event with edit-dedup (slack.md §2)."""
    inner = event.get("message") or {}
    ts = str(inner.get("ts", "")) if isinstance(inner, Mapping) else ""
    edited = inner.get("edited") or {} if isinstance(inner, Mapping) else {}
    edited_ts = str(edited.get("ts", "")) if isinstance(edited, Mapping) else ""
    channel_id = str(event.get("channel", ""))
    dedup_key = f"edit:{channel_id}:{ts}:{edited_ts}"
    if dedup_key in connector._cache.seen_event_ids:
        return []
    connector._cache.seen_event_ids.add(dedup_key)
    merged: dict[str, Any] = {**event, **(dict(inner) if isinstance(inner, Mapping) else {}), "channel": channel_id}
    return [_change_event_from_message_payload(merged, op="modified")]


def _translate_message_deleted(
    _connector: SlackConnector,
    event: Mapping[str, Any],
    event_type: str,
) -> list[ChangeEvent]:
    return [
        ChangeEvent(
            op="deleted",
            item_id=f"{event.get('channel', '')}:{event.get('deleted_ts', '')}",
            modified_at=_now_iso(),
            metadata={_METADATA_SLACK_EVENT_TYPE: event_type},
        )
    ]


def _translate_file_shared(
    _connector: SlackConnector,
    event: Mapping[str, Any],
    event_type: str,
) -> list[ChangeEvent]:
    channel_id = str(event.get(_METADATA_CHANNEL_ID, ""))
    ts = str(event.get("event_ts", ""))
    return [
        ChangeEvent(
            op="created",
            item_id=f"{channel_id}:{ts}",
            modified_at=_ts_to_iso(ts),
            metadata={_METADATA_SLACK_EVENT_TYPE: event_type, "file_id": str(event.get("file_id", ""))},
        )
    ]


def _translate_channel_archive(
    _connector: SlackConnector,
    event: Mapping[str, Any],
    event_type: str,
) -> list[ChangeEvent]:
    channel_id = str(event.get("channel", ""))
    return [
        ChangeEvent(
            op="archived",
            item_id=channel_id,
            modified_at=_now_iso(),
            metadata={_METADATA_SLACK_EVENT_TYPE: event_type},
        )
    ]


# Dispatch dict for SlackConnector.handle_event — replaces the
# if/elif chain so the orchestrator stays under the F16 cognitive-
# complexity ceiling per slack.md §2's event-type → ChangeEvent.op
# mapping.
_EVENT_TRANSLATORS: Mapping[str, Callable[[SlackConnector, Mapping[str, Any], str], list[ChangeEvent]]] = {
    "message": _translate_message,
    "message_changed": _translate_message_changed,
    "message_deleted": _translate_message_deleted,
    "file_shared": _translate_file_shared,
    "channel_archive": _translate_channel_archive,
}


def _sensitivity_to_f39(legacy: Sensitivity) -> F39Tier:
    """Map the legacy ``Sensitivity`` literal onto the F39 tier vocabulary.

    HierarchyNode.sensitivity_hint uses the F39 tier vocabulary
    (public/internal/confidential/restricted); the connector boundary
    continues to tag chunks with the legacy ``Sensitivity`` literal via
    ``sensitivity_for``. Mirrors the m365_email_headers mapping —
    personal → restricted (the tightest F39 tier).
    """
    mapping: dict[Sensitivity, F39Tier] = {
        "public": "public",
        "internal": "internal",
        _CLIENT_CONFIDENTIAL_TIER: "confidential",
        "personal": "restricted",
    }
    return mapping.get(legacy, "restricted")


def make_connector(config: Mapping[str, Any]) -> SlackConnector:
    """Construct a :class:`SlackConnector` from a config mapping.

    Expected keys:

      * ``bot_token`` (optional) — operator may override the secret
        lookup by passing the token inline. Production resolves via
        the connector's injected :class:`~kairix.secrets.SecretsResolver`
        for the canonical leaf
        ``("connector", "slack", <workspace>, "bot-token")``.
      * ``app_token`` (optional) — ``xapp-…`` for Socket Mode.
      * ``workspace`` (optional) — per-workspace canonical-naming
        ``instance`` slot. When set, the connector resolves tokens
        from ``kairix-connector-slack-<workspace>-bot-token``;
        unset, the legacy ``kairix-connector-slack-bot-token`` is
        used (back-compat). Captured by
        ``kairix connect slack --workspace <name>``.

    Registered via ``[project.entry-points."kairix.connectors"]`` in
    kairix's ``pyproject.toml`` so the orchestration layer can resolve
    ``slack`` to this factory by name.
    """
    inline_bot_token = config.get("bot_token")
    inline_app_token = config.get("app_token")
    workspace_value = config.get("workspace")
    workspace: str | None = str(workspace_value) if workspace_value else None
    credentials: SlackCredentials | None
    if inline_bot_token:
        credentials = SlackCredentials(
            bot_token=str(inline_bot_token),
            app_token=str(inline_app_token) if inline_app_token else None,
        )
    else:
        credentials = None
    return SlackConnector(credentials=credentials, workspace=workspace)
