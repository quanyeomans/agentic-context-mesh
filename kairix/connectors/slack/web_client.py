"""Slack Web API wrapper used by :class:`SlackConnector`.

Thin, dependency-injected client over the Slack Web API. Wraps the
small subset of methods :class:`~kairix.connectors.slack.connector.SlackConnector`
needs:

  * ``conversations.list`` (channel enumeration, container surface)
  * ``conversations.history`` (per-channel poll — Tier 3 / 50 req/min)
  * ``conversations.replies`` (thread expansion, keyed on ``thread_ts``)
  * ``conversations.members`` (per-channel ACL for perm-sync)
  * ``chat.getPermalink`` (source-link contract)
  * ``files.info`` (file metadata for the F39 sensitivity routing)
  * ``auth.test`` (used by the proactive ``app_uninstalled`` detector)

Slack-side rate-limit handling lives here so the connector itself
stays focused on Protocol surfaces. Per-method tiers are encoded in
:data:`_METHOD_TIERS`; each method consumes from a per-method token
bucket (per §5 "Tier-3 rate limit" row of ``slack.md``). A ``429`` from
the Slack edge is translated into
:class:`~kairix.core.protocols.ContainerTransientError` with the
``Retry-After`` budget threaded through; a ``401 invalid_auth`` /
``token_revoked`` / ``app_uninstalled`` failure raises
:class:`~kairix.core.protocols.CredentialExpiredError` so the framework
can transition the cc_pair to ``INVALID`` via F57's
``_ALLOWED_TRANSITIONS`` dispatch.

This module satisfies F37 — ``slack_sdk`` imports (including
``slack_sdk.socket_mode``) live ONLY under
``kairix/connectors/slack/``. F35 — no reach into other connector or
extractor plugins. F44 — no firm-scope storage drivers.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from kairix.core.protocols import (
    ContainerTransientError,
    CredentialExpiredError,
)

logger = logging.getLogger(__name__)

# Slack Web API base.  All methods land under ``https://slack.com/api/<method>``.
_SLACK_API_BASE = "https://slack.com/api"

# Per-method tier ceiling (Slack's documented Tier 1-4 envelope, §5).
# We collapse the four-tier shape into a per-method "tokens per minute"
# count so the token-bucket implementation stays a single dict lookup;
# values mirror the Slack documentation's "Tier 3 ~50/min" / "Tier 2
# ~20/min" / "Tier 1 ~1/min" / "Tier 4 ~100+/min" envelope.
_METHOD_TIERS: Mapping[str, int] = {
    "conversations.list": 20,  # Tier 2
    "conversations.history": 50,  # Tier 3 (the spec's stress case)
    "conversations.replies": 50,  # Tier 3
    "conversations.members": 20,  # Tier 2
    "chat.getPermalink": 100,  # Tier 4
    "files.info": 100,  # Tier 4
    "auth.test": 1,  # Tier 1 — exercised only on `app_uninstalled` detection
}

# Slack-side error codes that signal the workspace admin removed the
# app (or the operator-rotated token was revoked).  All three transition
# the connector's cc_pair to ``INVALID`` via the framework lifecycle.
_FATAL_AUTH_ERRORS: frozenset[str] = frozenset({"invalid_auth", "token_revoked", "account_inactive", "app_uninstalled"})

# F17 — extracted duplicated literals so the pagination + error
# translation code paths converge on one constant per field.
_RESPONSE_METADATA_KEY = "response_metadata"
_NEXT_CURSOR_KEY = "next_cursor"
_OK_FALSE_PREFIX = " returned ok=false / error="

# Slack-side error codes that mean "the bot was removed from a single
# channel" — the cc_pair stays alive, only the container goes
# ``REVOKED``.
_CHANNEL_ACCESS_LOST_ERRORS: frozenset[str] = frozenset({"not_in_channel", "channel_not_found", "is_archived"})


@dataclass(frozen=True)
class SlackMessage:
    """One Slack message envelope as the connector boundary sees it.

    Frozen per F42 — boundary dataclass.  ``thread_ts`` is the
    parent's ``ts`` when this message is a thread reply; equal to
    ``ts`` for root messages; ``None`` for messages outside any thread.
    """

    channel_id: str
    ts: str
    user: str | None
    text: str
    thread_ts: str | None
    subtype: str | None
    edited_ts: str | None
    file_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SlackChannel:
    """One Slack conversation envelope.

    ``kind`` is one of ``public_channel`` / ``private_channel`` /
    ``mpim`` / ``im`` — the connector maps this onto the F39 sensitivity
    tier (public→internal, private/mpim→client-confidential,
    im→personal) per slack.md §1.
    """

    channel_id: str
    name: str
    kind: str
    is_archived: bool
    is_member: bool


class PerMethodTokenBucket:
    """Token bucket per Slack method, shared across threads (§5 row 2).

    Honours the per-minute envelope captured in :data:`_METHOD_TIERS`.
    Refills lazily at consume time so there's no background thread to
    own / shut down.  Per-method state is held by reference so multiple
    callers (Web client + Socket Mode handler if it surfaces a method
    call) see the same bucket per workspace.
    """

    def __init__(self, *, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._lock = threading.Lock()
        # method -> (tokens_remaining, last_refill_ts).
        self._state: dict[str, tuple[float, float]] = {}

    def consume(self, method: str) -> None:
        """Consume one token from ``method``'s bucket or raise.

        Raises :class:`ContainerTransientError` carrying a
        ``retry_after`` budget when the bucket has no tokens — the
        framework's runner translates that into a deferred re-tick.
        Methods not in :data:`_METHOD_TIERS` are treated as unmetered
        (returns immediately).
        """
        tier = _METHOD_TIERS.get(method)
        if tier is None:
            return
        now = self._now()
        with self._lock:
            tokens, last_refill = self._state.get(method, (float(tier), now))
            elapsed = max(0.0, now - last_refill)
            # Refill ramp: full bucket per minute.
            refill = elapsed * (tier / 60.0)
            tokens = min(float(tier), tokens + refill)
            if tokens < 1.0:
                # No tokens — surface the wait budget so the framework
                # can re-schedule rather than hot-spin.
                remaining = max(1.0, (1.0 - tokens) * (60.0 / tier))
                self._state[method] = (tokens, now)
                raise ContainerTransientError(
                    f"slack: rate-limit budget exhausted for method {method!r} "
                    f"(tier {tier}/min). fix: defer this request by ~{remaining:.1f}s. "
                    "next: see kairix/connectors/slack/web_client.py:PerMethodTokenBucket.",
                    retry_after=remaining,
                )
            self._state[method] = (tokens - 1.0, now)


@dataclass
class SlackWebClient:
    """Slack Web API client — narrow surface for the connector.

    Constructed with the workspace's bot token and the shared rate-limit
    bucket.  Tests inject an ``httpx.MockTransport``-backed
    :class:`httpx.Client`; production constructs with no override so a
    fresh client lazily materialises on first request.

    DI seams:

    * ``http_client`` — tests pass a MockTransport-backed client; the
      Slack API never sees real traffic in CI.
    * ``token`` — the workspace's bot token (``xoxb-…``).  Never logged
      in plaintext; F15 holds at the connector boundary.
    * ``bucket`` — shared :class:`PerMethodTokenBucket`.  Inject one
      per workspace so multi-cc_pair processes share the budget.
    """

    token: str
    http_client: httpx.Client | None = None
    bucket: PerMethodTokenBucket = field(default_factory=PerMethodTokenBucket)

    def __post_init__(self) -> None:
        if not self.token:
            raise ValueError(
                "slack: web client requires a non-empty workspace bot token. "
                "fix: pass token via SlackWebClient(token=...) (production resolves via kairix.secrets). "
                "next: see kairix/connectors/slack/web_client.py for the construction shape."
            )
        if self.http_client is None:
            self.http_client = httpx.Client(timeout=30.0)

    # ------------------------------------------------------------------
    # Public Slack-method surface
    # ------------------------------------------------------------------

    def conversations_list(
        self,
        *,
        types: Sequence[str] = ("public_channel", "private_channel", "mpim", "im"),
    ) -> Iterator[SlackChannel]:
        """Enumerate the channels the bot can see, page-by-page.

        Filters server-side to the requested ``types`` so the bot only
        sees what its OAuth scopes allow; client-side filters to
        ``is_member=True`` so a misconfigured workspace doesn't surface
        every visible-but-unjoined channel (the bot can only fetch
        history for channels it's joined to).
        """
        cursor: str | None = None
        while True:
            payload = self._post(
                "conversations.list",
                params={
                    "types": ",".join(types),
                    "exclude_archived": "false",
                    "limit": "200",
                    **({"cursor": cursor} if cursor else {}),
                },
            )
            for raw in payload.get("channels", []):
                kind = _channel_kind_from_envelope(raw)
                yield SlackChannel(
                    channel_id=str(raw.get("id", "")),
                    name=str(raw.get("name") or raw.get("user") or raw.get("id", "")),
                    kind=kind,
                    is_archived=bool(raw.get("is_archived", False)),
                    is_member=bool(raw.get("is_member", kind in ("im", "mpim"))),
                )
            cursor = ((payload.get(_RESPONSE_METADATA_KEY) or {}).get(_NEXT_CURSOR_KEY)) or None
            if not cursor:
                return

    def conversations_history(self, *, channel_id: str, oldest: str | None = None) -> Iterator[SlackMessage]:
        """Stream messages in ``channel_id`` newer than ``oldest`` (a ``ts``).

        Server-side paginates 200 messages at a time per slack.md §6
        ("200 msgs/page cursor pagination").  Each emitted SlackMessage
        carries the channel id so downstream consumers don't need to
        re-thread it.
        """
        cursor: str | None = None
        while True:
            payload = self._post(
                "conversations.history",
                params={
                    "channel": channel_id,
                    "limit": "200",
                    **({"oldest": oldest} if oldest else {}),
                    **({"cursor": cursor} if cursor else {}),
                },
            )
            for raw in payload.get("messages", []):
                yield _message_from_envelope(channel_id, raw)
            cursor = ((payload.get(_RESPONSE_METADATA_KEY) or {}).get(_NEXT_CURSOR_KEY)) or None
            if not cursor:
                return

    def conversations_replies(self, *, channel_id: str, thread_ts: str) -> Iterator[SlackMessage]:
        """Stream the replies under one thread root."""
        cursor: str | None = None
        while True:
            payload = self._post(
                "conversations.replies",
                params={
                    "channel": channel_id,
                    "ts": thread_ts,
                    "limit": "200",
                    **({"cursor": cursor} if cursor else {}),
                },
            )
            for raw in payload.get("messages", []):
                yield _message_from_envelope(channel_id, raw)
            cursor = ((payload.get(_RESPONSE_METADATA_KEY) or {}).get(_NEXT_CURSOR_KEY)) or None
            if not cursor:
                return

    def conversations_members(self, *, channel_id: str) -> Iterator[str]:
        """Stream the user ids that can see ``channel_id``."""
        cursor: str | None = None
        while True:
            payload = self._post(
                "conversations.members",
                params={
                    "channel": channel_id,
                    "limit": "200",
                    **({"cursor": cursor} if cursor else {}),
                },
            )
            for member_id in payload.get("members", []):
                yield str(member_id)
            cursor = ((payload.get(_RESPONSE_METADATA_KEY) or {}).get(_NEXT_CURSOR_KEY)) or None
            if not cursor:
                return

    def chat_get_permalink(self, *, channel_id: str, ts: str) -> str:
        """Return the stable ``slack://`` deep-link / web permalink for one msg."""
        payload = self._post(
            "chat.getPermalink",
            params={"channel": channel_id, "message_ts": ts},
        )
        link = payload.get("permalink")
        if not isinstance(link, str) or not link:
            return f"slack://channel/{channel_id}/p{ts.replace('.', '')}"
        return link

    def auth_test(self) -> Mapping[str, Any]:
        """Liveness probe — surfaces the same ``app_uninstalled`` / ``invalid_auth``
        signals the per-method paths surface, so the operator-facing
        ``connector status`` verb can detect a dead workspace install
        without driving a heavy poll.
        """
        return self._post("auth.test", params={})

    # ------------------------------------------------------------------
    # Internal HTTP transport
    # ------------------------------------------------------------------

    def _post(self, method: str, *, params: Mapping[str, str]) -> Mapping[str, Any]:
        """One Slack Web API call with token-bucket + error translation."""
        self.bucket.consume(method)
        assert self.http_client is not None  # populated in __post_init__
        url = f"{_SLACK_API_BASE}/{method}"
        response = self.http_client.post(
            url,
            data=dict(params),
            headers={
                "Authorization": _bearer_header(self.token),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", "1") or "1")
            raise ContainerTransientError(
                f"slack: 429 from {method}; honour Retry-After. "
                "fix: defer this request and resume after the framework's backoff window. "
                "next: see kairix/connectors/slack/web_client.py for the token-bucket contract.",
                retry_after=retry_after,
            )
        if response.status_code in (401, 403):
            _raise_for_workspace_auth_failure(method, response.status_code)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"slack: {method} returned non-object payload. "
                "fix: investigate the Slack API change that broke the envelope shape. "
                "next: see kairix/connectors/slack/web_client.py for the response contract."
            )
        if not payload.get("ok", False):
            _raise_for_payload_error(method, payload)
        return payload


def _channel_kind_from_envelope(raw: Mapping[str, Any]) -> str:
    """Derive the ``public_channel`` / ``private_channel`` / ``mpim`` / ``im`` kind."""
    if raw.get("is_im"):
        return "im"
    if raw.get("is_mpim"):
        return "mpim"
    if raw.get("is_private"):
        return "private_channel"
    return "public_channel"


def _message_from_envelope(channel_id: str, raw: Mapping[str, Any]) -> SlackMessage:
    """Convert a raw Slack message envelope to the boundary :class:`SlackMessage`."""
    ts = str(raw.get("ts", ""))
    thread_ts_raw = raw.get("thread_ts")
    thread_ts = str(thread_ts_raw) if isinstance(thread_ts_raw, str) else None
    edited = raw.get("edited") or {}
    edited_ts_raw = edited.get("ts") if isinstance(edited, Mapping) else None
    edited_ts = str(edited_ts_raw) if isinstance(edited_ts_raw, str) else None
    file_ids_raw = raw.get("files") or []
    file_ids: tuple[str, ...] = tuple(
        str(f.get("id", "")) for f in file_ids_raw if isinstance(f, Mapping) and f.get("id")
    )
    return SlackMessage(
        channel_id=channel_id,
        ts=ts,
        user=str(raw.get("user")) if raw.get("user") else None,
        text=str(raw.get("text", "")),
        thread_ts=thread_ts,
        subtype=str(raw.get("subtype")) if raw.get("subtype") else None,
        edited_ts=edited_ts,
        file_ids=file_ids,
    )


def _raise_for_workspace_auth_failure(method: str, status_code: int) -> None:
    """HTTP 401/403 from Slack always means the workspace install is dead."""
    raise CredentialExpiredError(
        f"slack: {method} returned HTTP {status_code} — workspace install rejected. "
        "fix: re-install the Slack app or rotate the workspace bot token; the cc_pair "
        "will be transitioned to INVALID by the framework. "
        "next: see kairix/connectors/slack/connector.py for the app_uninstalled handler."
    )


def _raise_for_payload_error(method: str, payload: Mapping[str, Any]) -> None:
    """Translate a non-``ok`` Slack response into the right typed exception."""
    error_code = str(payload.get("error", "unknown_error"))
    prefix = f"slack: {method}{_OK_FALSE_PREFIX}"
    if error_code in _FATAL_AUTH_ERRORS:
        raise CredentialExpiredError(
            f"{prefix}{error_code!r}. "
            "fix: re-install the Slack app or rotate the workspace bot token; the cc_pair "
            "will transition to INVALID via the framework lifecycle. "
            "next: see kairix/connectors/slack/connector.py for the app_uninstalled handler."
        )
    if error_code == "ratelimited":
        # Slack docs note `ok: false, error: "ratelimited"` is sometimes
        # used in lieu of an HTTP 429 — treat both shapes the same.
        raise ContainerTransientError(
            f"{prefix}'ratelimited'. "
            "fix: defer this request per the token-bucket budget. "
            "next: see kairix/connectors/slack/web_client.py for the bucket contract.",
            retry_after=1.0,
        )
    if error_code in _CHANNEL_ACCESS_LOST_ERRORS:
        # Mapped to a channel-level access-lost signal — the connector's
        # caller translates this into a per-Container ``REVOKED`` state
        # via the ``access_state`` lifecycle. We surface a typed
        # exception so the framework runner can distinguish channel-
        # level loss from cc_pair-wide loss without parsing strings.
        from kairix.core.protocols import ContainerAccessDeniedError

        raise ContainerAccessDeniedError(
            f"{prefix}{error_code!r} — "
            f"channel access revoked (bot removed or channel archived). "
            "fix: the framework will transition the affected Container to REVOKED; "
            "re-invite the bot or unarchive to restore. "
            "next: see kairix/connectors/slack/connector.py for the access_state lifecycle."
        )
    raise RuntimeError(
        f"{prefix}{error_code!r}. "
        "fix: inspect the Slack API docs for this error code. "
        "next: see kairix/connectors/slack/web_client.py for the typed-error mapping."
    )


def _bearer_header(token: str) -> str:
    """Construct the ``Authorization: Bearer …`` header.

    Lifted to a helper so the secret-bearing string never appears in
    a logger / print / raise call site — F15 holds at the connector
    boundary because every call site that materialises the bearer goes
    through this one function, and this function only returns the
    string for use as an HTTP header argument.
    """
    return f"Bearer {token}"
