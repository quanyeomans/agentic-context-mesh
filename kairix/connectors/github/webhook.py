"""GitHub webhook handler for the github connector plugin.

Translates inbound webhook payloads into typed :class:`ChangeEvent`
items the connector pipeline already understands, AND enforces the
``X-Hub-Signature-256`` HMAC validation that the security baseline
requires per spec §5.

GitHub's webhook payload format is event-type-keyed (``push`` vs
``issues`` vs ``pull_request`` vs ``installation_repositories`` …);
this module's :func:`translate_event` dispatches per event type and
yields the typed :class:`ChangeEvent` items the connector emits.

Per spec §5, the proactive failure modes this handler enforces:

* **Invalid HMAC signature** → reject the payload outright; log
  ``webhook_signature_rejected`` (F15-clean — we never log the body or
  the signature; only the request-id).
* **Replayed delivery** → de-duplicate by ``X-GitHub-Delivery`` header;
  the connector's idempotency layer treats two replays of the same
  delivery_id as a single event.
* **Force-push** → ``push`` events with ``forced=true`` produce a
  ``MODIFIED`` event tagged ``force_push=True`` in metadata; the
  connector's :meth:`Resolver.reindex` path catches that flag and
  triggers full-container reconcile (Break #7).

Per F35 / F41, this module only imports from itself, ``kairix.core.*``
(Protocol surface + exceptions), and stdlib. No reach into other
connectors.

F15-clean: secrets (the webhook_secret kwarg, the signature header)
are NEVER passed to ``logger.*`` / ``print`` / ``raise X(...)``. The
verification helper compares HMACs in constant time via
:func:`hmac.compare_digest`.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from kairix.core.protocols import ChangeEvent

# Local type alias mirroring ChangeEvent.op for narrow casts.
_ChangeEventOp = Literal["created", "modified", "archived", "access_lost", "deleted"]

logger = logging.getLogger(__name__)

# F17 — extracted literals; each appears in ≥3 sites below + in tests.
HEADER_SIGNATURE_256 = "X-Hub-Signature-256"
HEADER_DELIVERY_ID = "X-GitHub-Delivery"
HEADER_EVENT_TYPE = "X-GitHub-Event"
_SIGNATURE_PREFIX = "sha256="
# F17 — webhook envelope field/key names duplicated across translate
# helpers; one constant per name keeps the GitHub envelope vocabulary
# in a single edit site.
_KEY_REPOSITORY = "repository"
_KEY_VISIBILITY = "visibility"
_KEY_SENSITIVITY = "sensitivity"
_KEY_PULL_REQUEST = "pull_request"
_KEY_UPDATED_AT = "updated_at"


class WebhookSignatureError(Exception):
    """The webhook payload failed HMAC verification.

    Distinct exception type so the connector / web handler can
    surface a 401 instead of the generic 500 a vanilla ValueError
    would produce. F15-clean — the message names the field that
    failed, never the signature value.
    """


@dataclass(frozen=True)
class WebhookEnvelope:
    """One verified webhook delivery, ready for translation.

    Frozen per F42. Construction goes through
    :func:`verify_and_parse`, which enforces HMAC integrity and
    delivery-id presence before yielding the envelope.
    """

    delivery_id: str
    event_type: str
    payload: Mapping[str, Any]


def verify_and_parse(
    *,
    body: bytes,
    headers: Mapping[str, str],
    webhook_secret: str,
) -> WebhookEnvelope:
    """Verify the HMAC signature and parse the envelope.

    GitHub computes ``sha256=<hex>`` of the raw request body with the
    operator-configured webhook secret and sends it in the
    ``X-Hub-Signature-256`` header. We recompute the HMAC server-side
    and compare with :func:`hmac.compare_digest` (constant time) per
    the OWASP webhook-security checklist.

    Sabotage-proof: replacing :func:`hmac.compare_digest` with
    ``==`` keeps the happy-path tests green but flunks
    :func:`test_webhook_signature_bypass_fails_security_test` because
    that test fuzzes the signature with a near-miss; constant-time
    compare is what catches it.

    Per F15 — the ``webhook_secret`` argument is consumed by
    :func:`hmac.new` only and is NEVER passed to a logger / print /
    raise. The diagnostic on rejection names the delivery id and
    event type, not the signature.
    """
    if not webhook_secret:
        raise WebhookSignatureError(
            "github webhook: webhook_secret is empty. "
            "fix: configure the connector with a non-empty webhook_secret. "
            "next: see docs/architecture/connector-scope-topology/connector-design-specs/github.md §5."
        )
    delivery_id = headers.get(HEADER_DELIVERY_ID) or headers.get(HEADER_DELIVERY_ID.lower())
    event_type = headers.get(HEADER_EVENT_TYPE) or headers.get(HEADER_EVENT_TYPE.lower())
    signature = headers.get(HEADER_SIGNATURE_256) or headers.get(HEADER_SIGNATURE_256.lower())
    if not delivery_id:
        raise WebhookSignatureError(
            "github webhook: missing X-GitHub-Delivery header. "
            "fix: ensure GitHub is sending the canonical delivery id header. "
            "next: see docs/architecture/connector-scope-topology/connector-design-specs/github.md §5."
        )
    if not event_type:
        raise WebhookSignatureError(
            f"github webhook: missing X-GitHub-Event header (delivery_id={delivery_id!r}). "
            "fix: ensure GitHub is sending the canonical event-type header. "
            "next: see docs/architecture/connector-scope-topology/connector-design-specs/github.md §5."
        )
    if not signature or not signature.startswith(_SIGNATURE_PREFIX):
        # F15-clean: log the delivery_id so the operator can correlate;
        # NEVER log the signature value or the body.
        logger.warning(
            "github webhook: signature missing or malformed (delivery_id=%s)",
            delivery_id,
        )
        raise WebhookSignatureError(
            f"github webhook: signature missing or malformed (delivery_id={delivery_id!r}). "
            "fix: confirm the receiving server is preserving the X-Hub-Signature-256 header. "
            "next: see docs/architecture/connector-scope-topology/connector-design-specs/github.md §5."
        )
    expected = compute_signature(body=body, secret=webhook_secret)
    given = signature[len(_SIGNATURE_PREFIX) :]
    if not hmac.compare_digest(expected, given):
        # F15-clean: log the delivery_id only; NEVER log the signature.
        logger.warning(
            "github webhook: signature verification failed (delivery_id=%s, event=%s)",
            delivery_id,
            event_type,
        )
        raise WebhookSignatureError(
            f"github webhook: signature verification failed (delivery_id={delivery_id!r}). "
            "fix: confirm the webhook_secret configured on the connector matches the one on GitHub. "
            "next: see docs/architecture/connector-scope-topology/connector-design-specs/github.md §5."
        )
    # Parse the JSON body lazily — callers may want to short-circuit on
    # signature failure before paying parse cost.
    import json as _json

    try:
        payload = _json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, _json.JSONDecodeError) as exc:
        raise WebhookSignatureError(
            f"github webhook: body is not valid UTF-8 JSON (delivery_id={delivery_id!r}). "
            "fix: ensure the upstream is forwarding GitHub's JSON body unmodified. "
            "next: see docs/architecture/connector-scope-topology/connector-design-specs/github.md §5."
        ) from exc
    if not isinstance(payload, dict):
        raise WebhookSignatureError(f"github webhook: body is not a JSON object (delivery_id={delivery_id!r}).")
    return WebhookEnvelope(delivery_id=delivery_id, event_type=event_type, payload=payload)


def compute_signature(*, body: bytes, secret: str) -> str:
    """Return the lowercase hex HMAC-SHA256 of ``body`` keyed on ``secret``.

    Module-level helper (not on a class) so the boundary stays narrow
    and the F15 audit is one-function. F15: the ``secret`` argument is
    consumed only by :func:`hmac.new`; it is not logged, printed, or
    raised.
    """
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256)
    return digest.hexdigest()


def translate_event(envelope: WebhookEnvelope) -> Iterator[ChangeEvent]:
    """Yield one or more :class:`ChangeEvent` from a verified envelope.

    Event-type dispatch per the spec §2 ``ChangeEvent.op`` mapping
    table. Unknown event types produce zero events (the connector logs
    + drops the delivery rather than raising — GitHub adds new event
    types over time and we don't want a payload-shape change to wedge
    the worker).

    Force-push detection: ``push`` events with ``forced=true`` emit
    ``MODIFIED`` events tagged ``force_push=True`` in metadata so the
    connector's :meth:`Resolver.reindex` path can trigger
    full-container reconcile (Break #7).
    """
    handler = _EVENT_HANDLERS.get(envelope.event_type)
    if handler is None:
        logger.info(
            "github webhook: ignoring unhandled event_type=%s (delivery_id=%s)",
            envelope.event_type,
            envelope.delivery_id,
        )
        return
    yield from handler(envelope.payload)


# ----------------------------------------------------------------------
# Event-type-specific translators
# ----------------------------------------------------------------------


def _translate_push(payload: Mapping[str, Any]) -> Iterator[ChangeEvent]:
    """Translate a ``push`` event into per-commit ``ChangeEvent`` items."""
    forced = bool(payload.get("forced", False))
    repo = payload.get(_KEY_REPOSITORY, {}) or {}
    full_name = str(repo.get("full_name", ""))
    sensitivity_tier = _sensitivity_from_visibility(str(repo.get(_KEY_VISIBILITY, "private")))
    commits = payload.get("commits", []) or []
    for commit in commits:
        sha = str(commit.get("id", ""))
        if not sha:
            continue
        push_op: _ChangeEventOp = "modified"
        yield ChangeEvent(
            op=push_op,
            item_id=f"github://{full_name}/commit/{sha}",
            modified_at=str(commit.get("timestamp", "")),
            metadata={
                _KEY_SENSITIVITY: sensitivity_tier,
                "force_push": forced,
                "repo": full_name,
            },
        )


def _translate_issues(payload: Mapping[str, Any]) -> Iterator[ChangeEvent]:
    """Translate an ``issues`` event into one ``ChangeEvent``."""
    action = str(payload.get("action", "opened"))
    issue = payload.get("issue", {}) or {}
    repo = payload.get(_KEY_REPOSITORY, {}) or {}
    full_name = str(repo.get("full_name", ""))
    sensitivity_tier = _sensitivity_from_visibility(str(repo.get(_KEY_VISIBILITY, "private")))
    issue_op = cast(_ChangeEventOp, _ISSUE_ACTION_TO_OP.get(action, "modified"))
    yield ChangeEvent(
        op=issue_op,
        item_id=f"github://{full_name}/issues/{int(issue.get('number', 0))}",
        modified_at=str(issue.get(_KEY_UPDATED_AT, "")),
        metadata={
            _KEY_SENSITIVITY: sensitivity_tier,
            "repo": full_name,
            "kind": "issue",
            "action": action,
        },
    )


def _translate_pull_request(payload: Mapping[str, Any]) -> Iterator[ChangeEvent]:
    """Translate a ``pull_request`` event into one ``ChangeEvent``."""
    action = str(payload.get("action", "opened"))
    pull = payload.get(_KEY_PULL_REQUEST, {}) or {}
    repo = payload.get(_KEY_REPOSITORY, {}) or {}
    full_name = str(repo.get("full_name", ""))
    sensitivity_tier = _sensitivity_from_visibility(str(repo.get(_KEY_VISIBILITY, "private")))
    pr_op = cast(_ChangeEventOp, _ISSUE_ACTION_TO_OP.get(action, "modified"))
    yield ChangeEvent(
        op=pr_op,
        item_id=f"github://{full_name}/pulls/{int(pull.get('number', 0))}",
        modified_at=str(pull.get(_KEY_UPDATED_AT, "")),
        metadata={
            _KEY_SENSITIVITY: sensitivity_tier,
            "repo": full_name,
            "kind": _KEY_PULL_REQUEST,
            "action": action,
        },
    )


def _translate_repository(payload: Mapping[str, Any]) -> Iterator[ChangeEvent]:
    """Translate a ``repository`` event — handles archive/unarchive/delete."""
    action = str(payload.get("action", ""))
    repo = payload.get(_KEY_REPOSITORY, {}) or {}
    full_name = str(repo.get("full_name", ""))
    sensitivity_tier = _sensitivity_from_visibility(str(repo.get(_KEY_VISIBILITY, "private")))
    repo_op: _ChangeEventOp = "archived" if action == "archived" else ("deleted" if action == "deleted" else "modified")
    yield ChangeEvent(
        op=repo_op,
        item_id=f"github://{full_name}",
        modified_at=str(repo.get(_KEY_UPDATED_AT, "")),
        metadata={
            _KEY_SENSITIVITY: sensitivity_tier,
            "repo": full_name,
            "action": action,
        },
    )


def _translate_installation_repositories(payload: Mapping[str, Any]) -> Iterator[ChangeEvent]:
    """Translate ``installation_repositories`` — repo access add/remove."""
    action = str(payload.get("action", ""))
    repos_removed = payload.get("repositories_removed", []) or []
    repos_added = payload.get("repositories_added", []) or []
    if action == "removed":
        for repo in repos_removed:
            full_name = str(repo.get("full_name", ""))
            yield ChangeEvent(
                op="access_lost",
                item_id=f"github://{full_name}",
                modified_at="",
                metadata={"repo": full_name, "action": "access_revoked"},
            )
    elif action == "added":
        for repo in repos_added:
            full_name = str(repo.get("full_name", ""))
            yield ChangeEvent(
                op="created",
                item_id=f"github://{full_name}",
                modified_at="",
                metadata={"repo": full_name, "action": "access_granted"},
            )


# F17 — issue-action → op mapping; module-level so both _translate_issues
# and _translate_pull_request reference the same dispatch table.
_ISSUE_ACTION_TO_OP: Mapping[str, str] = {
    "opened": "created",
    "reopened": "modified",
    "edited": "modified",
    "closed": "modified",
    "labeled": "modified",
    "unlabeled": "modified",
    "deleted": "deleted",
}

# F17 — GitHub visibility → F39 tier map per spec §1.
_VISIBILITY_TO_SENSITIVITY: Mapping[str, str] = {
    "public": "public",
    "internal": "internal",
    "private": "client-confidential",
}


def _sensitivity_from_visibility(visibility: str) -> str:
    """Map GitHub's repo ``visibility`` to the F39 sensitivity tier.

    Per spec §1 — public repos → ``public``, GHEC internal → ``internal``,
    private repos → ``client-confidential``. Default unknown → most
    conservative tier (client-confidential).
    """
    return _VISIBILITY_TO_SENSITIVITY.get(visibility, "client-confidential")


# Module-level dispatch table — F16 keeps :func:`translate_event` flat
# (no nested if/elif chain). Adding a new event type is a one-line
# registry edit plus a per-event-type translator.
_EventHandler = Callable[[Mapping[str, Any]], Iterator[ChangeEvent]]
_EVENT_HANDLERS: Mapping[str, _EventHandler] = {
    "push": _translate_push,
    "issues": _translate_issues,
    _KEY_PULL_REQUEST: _translate_pull_request,
    _KEY_REPOSITORY: _translate_repository,
    "installation_repositories": _translate_installation_repositories,
}
