"""Unit-level coverage for kairix.connect.listener.

Covers happy-path callback, port-collision-with-advance, timeout, and
denied-callback paths. The real :class:`LocalhostCallbackListener`
binds a socket, so we exercise it with a real HTTP client on a
locally-bound port. No third-party mocks.
"""

from __future__ import annotations

import socket
import threading
import time
from urllib.request import urlopen

import pytest

from kairix.connect.listener import (
    DEFAULT_PORT,
    LocalhostCallbackListener,
    find_free_port,
)
from kairix.connect.protocols import (
    CallbackDeniedError,
    CallbackTimeoutError,
)

pytestmark = pytest.mark.unit


def _find_test_port() -> int:
    """Pick a free port the OS hands us, then close so the listener can rebind."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _hit_callback_in_background(url: str, delay: float = 0.05) -> threading.Thread:
    """Send a single GET to ``url`` after ``delay`` seconds.

    Used to unblock the listener's ``wait_for_callback`` from a separate
    thread without monkey-patching anything.
    """

    def _hit() -> None:
        time.sleep(delay)
        try:
            urlopen(url, timeout=5).read()
        except Exception:
            pass

    t = threading.Thread(target=_hit, daemon=True)
    t.start()
    return t


def test_listener_redirect_uri_uses_bound_port() -> None:
    """The listener exposes the redirect URI for the actual bound port."""
    port = _find_test_port()
    listener = LocalhostCallbackListener(port=port)
    try:
        assert listener.redirect_uri == f"http://127.0.0.1:{port}/oauth2callback"
        assert listener.port == port
    finally:
        listener.close()


def test_listener_advances_past_port_in_use() -> None:
    """When the requested port is in use, the listener scans forward."""
    port = _find_test_port()
    # Hold the port so the listener has to advance.
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    try:
        listener = LocalhostCallbackListener(port=port)
        try:
            assert listener.port > port
        finally:
            listener.close()
    finally:
        blocker.close()


def test_listener_captures_callback_code() -> None:
    """The happy path: a callback to ``/oauth2callback?code=...`` is captured."""
    port = _find_test_port()
    listener = LocalhostCallbackListener(port=port)
    try:
        callback_url = f"{listener.redirect_uri}?code=test-code-xyz&state=abc"
        worker = _hit_callback_in_background(callback_url)
        result = listener.wait_for_callback(timeout_s=5.0)
        worker.join(timeout=2.0)
        assert result.code == "test-code-xyz"
        assert result.state == "abc"
    finally:
        listener.close()


def test_listener_raises_on_access_denied() -> None:
    """``error=access_denied`` raises :class:`CallbackDeniedError` with consent hint."""
    port = _find_test_port()
    listener = LocalhostCallbackListener(port=port)
    try:
        callback_url = f"{listener.redirect_uri}?error=access_denied"
        worker = _hit_callback_in_background(callback_url)
        with pytest.raises(CallbackDeniedError, match="consent denied"):
            listener.wait_for_callback(timeout_s=5.0)
        worker.join(timeout=2.0)
    finally:
        listener.close()


def test_listener_raises_on_generic_error() -> None:
    """Any other ``error=`` value raises :class:`CallbackDeniedError`."""
    port = _find_test_port()
    listener = LocalhostCallbackListener(port=port)
    try:
        callback_url = f"{listener.redirect_uri}?error=invalid_scope"
        worker = _hit_callback_in_background(callback_url)
        with pytest.raises(CallbackDeniedError, match="invalid_scope"):
            listener.wait_for_callback(timeout_s=5.0)
        worker.join(timeout=2.0)
    finally:
        listener.close()


def test_listener_raises_on_missing_code() -> None:
    """A callback without ``code=`` raises (treated as denied)."""
    port = _find_test_port()
    listener = LocalhostCallbackListener(port=port)
    try:
        callback_url = listener.redirect_uri  # no query string
        worker = _hit_callback_in_background(callback_url)
        with pytest.raises(CallbackDeniedError, match="missing_code"):
            listener.wait_for_callback(timeout_s=5.0)
        worker.join(timeout=2.0)
    finally:
        listener.close()


def test_listener_404s_unrelated_paths() -> None:
    """Requests to non-callback paths are 404'd but don't unblock the wait."""
    port = _find_test_port()
    listener = LocalhostCallbackListener(port=port)
    try:
        # Send to wrong path, listener should 404 + remain blocked, then
        # we send the real callback to unblock.
        def _drive() -> None:
            time.sleep(0.05)
            try:
                urlopen(f"http://127.0.0.1:{port}/wrong-path", timeout=2)
            except Exception:
                pass

        threading.Thread(target=_drive, daemon=True).start()
        # 404 path is handled by the handler but doesn't set the done
        # event, so without a follow-up real callback the wait would
        # time out. Send a short-timeout wait and confirm.
        with pytest.raises(CallbackTimeoutError):
            listener.wait_for_callback(timeout_s=0.5)
    finally:
        listener.close()


def test_listener_timeout_raises() -> None:
    """No callback within timeout → :class:`CallbackTimeoutError`."""
    port = _find_test_port()
    listener = LocalhostCallbackListener(port=port)
    try:
        with pytest.raises(CallbackTimeoutError, match="no OAuth callback"):
            listener.wait_for_callback(timeout_s=0.3)
    finally:
        listener.close()


def test_find_free_port_raises_when_all_used() -> None:
    """``find_free_port`` raises when every candidate is bound.

    F1-clean: ``find_free_port`` accepts ``scan_limit=`` as a
    constructor-injection seam so tests pass 2 instead of binding 50
    sockets — no monkeypatching of kairix internals.

    Strengthened: the previous form had an ``if len(blockers) == 2:``
    guard that let the test pass silently when only one socket bound.
    The new form scans 20000..40000 for an adjacent free-port pair and
    asserts the OSError unconditionally — if no adjacent pair is found
    the test fails loudly rather than silently passing.
    """
    held: list[socket.socket] = []
    for candidate in range(20000, 40000):
        a = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        b = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            a.bind(("127.0.0.1", candidate))
            b.bind(("127.0.0.1", candidate + 1))
        except OSError:
            a.close()
            b.close()
            continue
        a.listen(1)
        b.listen(1)
        held = [a, b]
        try:
            with pytest.raises(OSError, match="no free port"):
                find_free_port("127.0.0.1", candidate, scan_limit=2)
            return  # assertion fired — done
        finally:
            for s in held:
                s.close()
    pytest.fail("could not find an adjacent free-port pair to exhaust — test environment issue")


def test_listener_close_is_idempotent() -> None:
    """Calling ``close()`` twice doesn't raise."""
    port = _find_test_port()
    listener = LocalhostCallbackListener(port=port)
    listener.close()
    listener.close()  # second call is a no-op


def test_default_port_matches_google_default() -> None:
    """The hardcoded default lines up with the Google console default URI."""
    assert DEFAULT_PORT == 8080
