"""Localhost HTTP listener for OAuth2 callbacks.

One implementation, shared across all ``kairix.connect`` flows.
Default ``127.0.0.1:8080/oauth2callback``; finds the next free port on
collision; 120s default timeout.

The listener is a kairix-owned wrapper around ``http.server`` from
stdlib — no third-party HTTP server dep. The wrapper exists because the
``OAuth2Flow.authorize`` lifecycle needs a typed value back from the
callback (``CallbackResult``), not the raw ``BaseHTTPRequestHandler``
shape, and because port-collision-with-advance is a behaviour we want
pinned in tests.

F1/F2-clean: tests construct ``FakeCallbackListener`` from
``tests.fakes`` directly — they do NOT instantiate the real
:class:`LocalhostCallbackListener` and then patch its socket.
"""

from __future__ import annotations

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from kairix.connect.protocols import (
    CallbackDeniedError,
    CallbackResult,
    CallbackTimeoutError,
)

# Default port for the localhost OAuth2 callback. Matches the Google
# developer console default ("Authorized redirect URI" for Desktop apps);
# operators with port 8080 already in use can override via the
# ``--port`` CLI flag and the listener will advance from there.
DEFAULT_PORT = 8080

# How many ports forward from the requested port to try before giving up
# with a clear error. 50 covers operator environments with many local
# services already bound (typical dev laptops top out under 20).
_PORT_SCAN_LIMIT = 50

# Path the OAuth provider redirects to. Hardcoded — every flow uses the
# same path; the port is what varies.
_CALLBACK_PATH = "/oauth2callback"

# HTTP header name for HTML body responses.
_HEADER_CONTENT_TYPE = "Content-Type"
_HTML_MIME = "text/html"

# Response body the operator sees in their browser after a successful
# callback. Plain HTML, no JS — works in every browser without warnings.
_SUCCESS_HTML = "<html><body><h1>Connected.</h1><p>You can close this tab and return to the terminal.</p></body></html>"

# Same shape for the denied / error path so the operator sees a clear
# remediation hint in the browser (not just a blank tab).
_DENIED_HTML = (
    "<html><body><h1>Connection denied.</h1>"
    "<p>The OAuth provider returned an error. Return to the terminal "
    "for next steps.</p></body></html>"
)


class _CallbackHandler(BaseHTTPRequestHandler):
    """Single-shot HTTP handler that records the callback then unblocks the wait.

    The server thread holds one instance per request; the handler reads
    the query string, sets the server-side result attributes, and
    returns the HTML body to the operator's browser.
    """

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != _CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
            return
        params = parse_qs(parsed.query)
        error = params.get("error", [None])[0]
        # mypy: the server is our subclass that carries the result attrs.
        server = self.server
        if error:
            self.send_response(400)
            self.send_header(_HEADER_CONTENT_TYPE, _HTML_MIME)
            self.end_headers()
            self.wfile.write(_DENIED_HTML.encode("utf-8"))
            server._error = error  # type: ignore[attr-defined]  # F3 rationale: see comment above.
            server._done_event.set()  # type: ignore[attr-defined]  # F3 rationale: see comment above.
            return
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        # GitHub App install callback shape: ``installation_id`` +
        # ``setup_action`` instead of ``code``. Treat the install id as
        # the captured value (the flow reads it via ``params``) so the
        # listener Protocol stays uniform across services.
        installation_id = params.get("installation_id", [None])[0]
        captured_code = code or installation_id
        if not captured_code:
            self.send_response(400)
            self.send_header(_HEADER_CONTENT_TYPE, _HTML_MIME)
            self.end_headers()
            self.wfile.write(b"missing code")
            server._error = "missing_code"  # type: ignore[attr-defined]  # F3 rationale: see comment above.
            server._done_event.set()  # type: ignore[attr-defined]  # F3 rationale: see comment above.
            return
        flat_params = {k: v[0] for k, v in params.items() if v}
        server._result = CallbackResult(  # type: ignore[attr-defined]  # F3 rationale: see comment above.
            code=captured_code,
            state=state,
            params=flat_params,
        )
        server._done_event.set()  # type: ignore[attr-defined]  # F3 rationale: see comment above.
        self.send_response(200)
        self.send_header(_HEADER_CONTENT_TYPE, _HTML_MIME)
        self.end_headers()
        self.wfile.write(_SUCCESS_HTML.encode("utf-8"))

    def log_message(self, *_args: object, **_kwargs: object) -> None:
        """Silence the stdlib request-log spam — kairix logs at a higher level."""
        # Intentionally empty — stdlib BaseHTTPRequestHandler logs to stderr by default.


class _Server(HTTPServer):
    """HTTPServer subclass that carries the per-request result holder.

    Attributes mutated by :class:`_CallbackHandler`; read by
    :meth:`LocalhostCallbackListener.wait_for_callback`.
    """

    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, _CallbackHandler)
        self._done_event = threading.Event()
        self._result: CallbackResult | None = None
        self._error: str | None = None


def find_free_port(host: str, start_port: int, *, scan_limit: int = _PORT_SCAN_LIMIT) -> int:
    """Scan forward from ``start_port`` for the first free port on ``host``.

    Raises :class:`OSError` if no free port found within
    ``scan_limit`` ports of ``start_port``. Operators on machines
    where ports 8080..8129 are all in use almost certainly have a
    deeper issue worth surfacing.

    Args:
      scan_limit: Test seam — override the default scan budget
        (:data:`_PORT_SCAN_LIMIT` = 50). Tests pass 2 to drive the
        exhaustion path without binding 50 sockets.
    """
    for offset in range(scan_limit):
        candidate = start_port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, candidate))
            except OSError:
                continue
            return candidate
    raise OSError(
        f"kairix connect: no free port in {start_port}..{start_port + scan_limit - 1} on {host}. "
        f"fix: stop one of the services using these ports OR pass --port to a known-free port. "
        f"next: lsof -nP -iTCP -sTCP:LISTEN | grep '127.0.0.1:80' to identify the blocker. "
        f"run: kairix connect <service> --port 9090 --client-secret-path <path>",
    )


class LocalhostCallbackListener:
    """Default production :class:`CallbackListener` — stdlib HTTP server on localhost.

    Construction binds the socket immediately (so port-collision is
    surfaced before the browser flow opens). :meth:`wait_for_callback`
    starts a daemon thread that handles one request, then blocks on the
    completion event up to ``timeout_s``.

    Args:
      host: Address to bind. Default ``127.0.0.1`` — anything else is
        an unusual operator choice (e.g. binding to ``0.0.0.0`` for a
        remote-browser flow not yet supported in v1).
      port: Requested port. ``DEFAULT_PORT`` (8080) by default; if in
        use the listener scans forward up to ``_PORT_SCAN_LIMIT`` ports
        for the first free port.
    """

    def __init__(self, *, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
        self._host = host
        bound_port = find_free_port(host, port)
        self._server = _Server((host, bound_port))
        self._port = bound_port
        self._thread: threading.Thread | None = None

    @property
    def redirect_uri(self) -> str:
        return f"http://{self._host}:{self._port}{_CALLBACK_PATH}"

    @property
    def port(self) -> int:
        """Return the actual port the listener bound to.

        Differs from the requested ``port`` constructor argument when
        the operator's machine had the requested port in use and the
        listener advanced to the next free slot. Operators reading the
        CLI output will see the actual port so the consent screen URL
        matches what the listener is bound to.
        """
        return self._port

    def wait_for_callback(self, timeout_s: float = 120.0) -> CallbackResult:
        """Run the HTTP server for one request, return the captured callback.

        Starts a daemon thread that calls
        :meth:`HTTPServer.handle_request` exactly once, then blocks on
        the done-event up to ``timeout_s`` seconds.

        Raises :class:`CallbackTimeoutError` after timeout.
        Raises :class:`CallbackDeniedError` if the callback carried an
        ``error=`` query param (typically ``access_denied`` from a
        cancelled consent flow).
        """
        # Run one request in a background thread; the main thread blocks
        # on the done-event. This shape lets us bound the wait by
        # ``timeout_s`` rather than blocking forever on ``handle_request``.
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._serve_one,
                name="kairix-connect-listener",
                daemon=True,
            )
            self._thread.start()
        completed = self._server._done_event.wait(timeout=timeout_s)
        if not completed:
            self._cleanup()
            raise CallbackTimeoutError(
                f"kairix connect: no OAuth callback within {timeout_s:.0f}s on {self.redirect_uri}. "
                f"fix: confirm the browser opened to the consent screen — if the consent screen "
                f"never appeared, the browser may not have launched (check $DISPLAY on Linux). "
                f"next: re-run kairix connect <service> on a machine with a working browser. "
                f"run: open '{self.redirect_uri}' in your browser manually to confirm the listener is reachable.",
            )
        if self._server._error:
            err = self._server._error
            self._cleanup()
            if err == "access_denied":
                raise CallbackDeniedError(
                    "kairix connect: consent denied. The operator clicked Cancel or Deny on the "
                    "OAuth provider's consent screen. "
                    "fix: re-run kairix connect <service> and approve the consent screen. "
                    "next: confirm the operator has authority to grant the requested scopes; the connector "
                    "needs the listed scopes to read the source. "
                    "run: kairix connect <service> --client-secret-path <path>",
                )
            raise CallbackDeniedError(
                f"kairix connect: OAuth callback returned error '{err}'. "
                f"fix: confirm the OAuth client (client_secret.json) is configured for a Desktop "
                f"app with the redirect URI {self.redirect_uri}. "
                f"next: re-run kairix connect <service> after correcting the OAuth client config. "
                f"run: kairix connect <service> --client-secret-path <path>",
            )
        if self._server._result is None:
            self._cleanup()
            raise CallbackTimeoutError(
                "kairix connect: callback completed with no result and no error. "
                "fix: this is a kairix bug — please file an issue with the listener logs. "
                "next: include the full stderr output from the failing kairix connect run. "
                "run: kairix connect <service> --port 8081 --client-secret-path <path>",
            )
        result = self._server._result
        self._cleanup()
        return result

    def close(self) -> None:
        """Release the listening socket. Idempotent — safe to call twice."""
        self._cleanup()

    def _serve_one(self) -> None:
        """Worker-thread target — handle exactly one request, then return.

        ``handle_request`` blocks until a connection arrives; the main
        thread's timeout wins by setting the done-event and calling
        ``server_close()`` which unblocks any pending accept.
        """
        try:
            self._server.handle_request()
        except (OSError, ValueError):
            # Server was closed under us by the timeout path; the main
            # thread already populated the error state. The handler
            # thread's job is done either way.
            return

    def _cleanup(self) -> None:
        """Tear down the server socket. Safe to call multiple times."""
        try:
            self._server.server_close()
        except (OSError, AttributeError):
            # Already closed or never bound — no-op.
            return


__all__ = [
    "DEFAULT_PORT",
    "LocalhostCallbackListener",
    "find_free_port",
]


# Force-import: ``time`` is reserved for future implementations of the
# deferred no-browser flow (ADR-032 §"Open questions" #3) and is
# currently unused. Kept as a comment so the import doesn't slip out
# during refactor.
_ = time
