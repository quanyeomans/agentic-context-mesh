"""Web setup wizard — Starlette routes + Jinja2/HTMX screens.

Mounted at ``/setup`` by :func:`kairix.agents.mcp.transport.build_mcp_app`
when the ``setup_wizard_web`` feature flag is ON. Same container, same
port as the MCP transport — no second service to deploy.

Composition idioms mirror the transport composer:

- Public surface is a single function, :func:`build_setup_wizard_mount`.
- No module-level state — the Jinja environment, the lazily-resolved
  service, and the secrets resolver all live in the builder's closure.
- The wizard renders against the :class:`SetupService` Protocol only
  (``kairix.platform.setup.service``); every side effect (credential
  validation, folder scans, index runs, search) happens behind that
  boundary. Tests inject ``FakeSetupService`` from ``tests/fakes.py``
  through the ``service_factory`` seam.

Security posture: requests from non-loopback clients must carry the
``X-Kairix-Operator-Token`` header matching the canonical
``kairix-infra-operator-token`` secret (resolved through
:class:`kairix.secrets.loader.SecretsLoader`). Loopback requests —
the laptop-first install this wizard exists for — skip the token.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Literal

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

if TYPE_CHECKING:
    import jinja2

    from kairix.platform.setup.service import SetupService
    from kairix.secrets.loader import SecretsResolver

# Absolute paths the browser navigates between. The mount path is part
# of the wizard's contract (templates hard-code form actions + redirect
# targets), so it is a constant rather than a build_setup_wizard_mount
# parameter.
SETUP_PATH_PREFIX = "/setup"
_PROVIDER_URL = f"{SETUP_PATH_PREFIX}/provider"
_KEY_URL = f"{SETUP_PATH_PREFIX}/key"
_FOLDER_URL = f"{SETUP_PATH_PREFIX}/folder"
_INDEXING_URL = f"{SETUP_PATH_PREFIX}/indexing"
_FIRST_SEARCH_URL = f"{SETUP_PATH_PREFIX}/first-search"

# Operator token header + the canonical secret identity it must match.
OPERATOR_TOKEN_HEADER = "X-Kairix-Operator-Token"  # noqa: S105 — HTTP header NAME, not a credential value
_TOKEN_SCOPE: Literal["infra"] = "infra"  # noqa: S105 — canonical secret-identity segment, not a credential value
_TOKEN_AREA = "operator"  # noqa: S105 — canonical secret-identity segment, not a credential value
_TOKEN_LEAF = "token"  # noqa: S105 — canonical secret-identity segment, not a credential value

# Client hosts that count as loopback (token check skipped).
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})

# Template names (single source so a rename touches one line).
_TPL_WELCOME = "setup/welcome.html"
_TPL_PROVIDER = "setup/provider.html"
_TPL_KEY = "setup/key.html"
_TPL_FOLDER = "setup/folder.html"
_TPL_INDEXING = "setup/indexing.html"
_TPL_FIRST_SEARCH = "setup/first_search.html"
_TPL_CONNECT_AGENT = "setup/connect_agent.html"
_TPL_DONE = "setup/done.html"
_TPL_KEY_VALIDATION = "partials/key_validation_result.html"
_TPL_FOLDER_SCAN = "partials/folder_scan_result.html"
_TPL_INDEXING_PROGRESS = "partials/indexing_progress.html"
_TPL_SEARCH_RESULTS = "partials/search_results.html"
_TPL_HANDSHAKE = "partials/handshake_result.html"

# Total step-indicator positions rendered by setup/base.html.
_TOTAL_STEPS = 7

# Form field names shared across handlers.
_FIELD_FOLDER_PATH = "folder_path"
_FIELD_PROVIDER = "provider"
_FIELD_API_KEY = "api_key"  # pragma: allowlist secret — form FIELD NAME, not a credential value
_FIELD_ENDPOINT = "endpoint"
_FIELD_DEPLOYMENT = "deployment"

# Azure-shaped provider plugin names — the key screen shows the optional
# deployment-name field for these (#484). Azure routes requests by
# deployment name, so the probe model must be one the operator deployed.
_AZURE_PROVIDER_NAMES = frozenset({"azure_foundry", "azure_legacy"})


def _config_write_error(exc: OSError) -> str:
    """F21-shaped banner for a failed config write (#485).

    The stock compose mounts the operator's ``kairix.config.yaml``
    read-only, so a wizard save on a deployment without the overlay
    configured raises ``OSError`` — render the rescue path instead of a
    raw 500.
    """
    target = getattr(exc, "filename", None) or "its configured location"
    return (
        f"Could not write the config file at {target} — it may be mounted read-only."
        " fix: the standard compose mounts kairix.config.yaml read-only; set"
        " KAIRIX_CONFIG_OVERLAY_PATH (stock value /var/lib/kairix/kairix.config.local.yaml)"
        " so setup writes land on the data volume."
        " next: restart the container, then save again."
    )


def _key_context(provider: str) -> dict[str, Any]:
    """Template context for the key screen — azure picks get the
    deployment-name field (#484)."""
    return {"step": 3, "provider": provider, "azure_provider": provider in _AZURE_PROVIDER_NAMES}


def _handle_key_save(
    fields: dict[str, str],
    service: SetupService,
    render: Callable[[str, dict[str, Any]], Response],
) -> Response:
    """Persist the provider pick; on a read-only config (#485), re-render
    the key screen with the rescue banner instead of a raw 500.

    Module-level (not a closure) so the builder stays under the F16
    complexity ceiling; ``render`` is the builder's template closure.
    """
    provider = fields.get(_FIELD_PROVIDER, "")
    try:
        service.save_provider(
            provider,
            fields.get(_FIELD_API_KEY, ""),
            fields.get(_FIELD_ENDPOINT) or None,
            fields.get("model") or None,
            deployment=fields.get(_FIELD_DEPLOYMENT) or None,
        )
    except OSError as exc:
        return render(_TPL_KEY, {**_key_context(provider), "save_error": _config_write_error(exc)})
    return RedirectResponse(_FOLDER_URL, status_code=303)


def _handle_folder_save(
    fields: dict[str, str],
    service: SetupService,
    render: Callable[[str, dict[str, Any]], Response],
) -> Response:
    """Persist the folder pick and start indexing; on a read-only config
    (#485) re-render with the rescue banner — indexing must NOT start
    on a failed save. Module-level for the same F16 reason as
    :func:`_handle_key_save`.
    """
    path = fields.get(_FIELD_FOLDER_PATH, "")
    try:
        service.save_source(path)
    except OSError as exc:
        return render(
            _TPL_FOLDER,
            {
                "step": 4,
                "hint": service.source_hint(),
                _FIELD_FOLDER_PATH: path,
                "save_error": _config_write_error(exc),
            },
        )
    service.start_index()
    return RedirectResponse(_INDEXING_URL, status_code=303)


def _default_service_factory() -> SetupService:
    """Lazy import of the production backend factory.

    Deferred so the wizard UI ships independently of the backend — a
    build where ``build_setup_service`` is still the NotImplementedError
    stub fails honestly at first request (see that stub's message).
    """
    from kairix.platform.setup.service import build_setup_service

    return build_setup_service()


def _default_secrets_resolver() -> SecretsResolver:
    """Production secrets seam — the canonical env → KV-mount loader."""
    from kairix.secrets.loader import SecretsLoader

    return SecretsLoader()


def _default_provider_names() -> tuple[str, ...]:
    """Provider names from the real plugin registry (entry points)."""
    from kairix.providers import EntryPointRegistry

    return tuple(EntryPointRegistry().available())


def _build_template_env() -> jinja2.Environment:
    """Jinja environment over the package's vendored templates.

    ``PackageLoader`` resolves from both source checkouts and installed
    wheels (templates ship as package data — see
    ``[tool.setuptools.package-data]`` in pyproject.toml).
    """
    from jinja2 import Environment, PackageLoader, select_autoescape

    env = Environment(
        loader=PackageLoader("kairix.platform.setup.web", "templates"),
        autoescape=select_autoescape(("html",)),
    )
    env.globals["total_steps"] = _TOTAL_STEPS
    return env


def _is_loopback_client(scope: Scope) -> bool:
    """True only for explicit loopback peers; missing client fails closed."""
    client = scope.get("client")
    if client is None:
        return False
    host = client[0]
    return host in _LOOPBACK_HOSTS


class OperatorTokenGuard:
    """ASGI guard wrapping the wizard mount.

    Loopback requests pass through. Non-loopback requests must carry
    ``X-Kairix-Operator-Token`` matching the canonical
    ``kairix-infra-operator-token`` secret. The provided header value
    is never logged or echoed (F15).
    """

    def __init__(self, app: ASGIApp, *, secrets: SecretsResolver) -> None:
        self._app = app
        self._secrets = secrets

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or _is_loopback_client(scope):
            await self._app(scope, receive, send)
            return
        expected = self._secrets.get(_TOKEN_SCOPE, _TOKEN_AREA, None, _TOKEN_LEAF)
        if expected is None:
            response: Response = PlainTextResponse(
                "Setup wizard remote access is disabled: no operator token is configured. "
                "fix: set the kairix-infra-operator-token secret (env "
                "KAIRIX_INFRA_OPERATOR_TOKEN or the KV mount), or open the wizard "
                f"from the host itself (loopback). next: retry with the {OPERATOR_TOKEN_HEADER} header.",
                status_code=403,
            )
            await response(scope, receive, send)
            return
        provided = _header_value(scope, OPERATOR_TOKEN_HEADER)
        if provided is not None and hmac.compare_digest(provided, expected):
            await self._app(scope, receive, send)
            return
        response = PlainTextResponse(
            "Setup wizard remote access requires a valid operator token. "
            f"fix: send the {OPERATOR_TOKEN_HEADER} header matching the "
            "kairix-infra-operator-token secret. next: retry the request.",
            status_code=403,
        )
        await response(scope, receive, send)


def _header_value(scope: Scope, name: str) -> str | None:
    """Case-insensitive single-header lookup from the raw ASGI scope."""
    wanted = name.lower().encode("latin-1")
    headers: tuple[tuple[bytes, bytes], ...] = tuple(scope.get("headers", ()))
    for key, value in headers:
        if key.lower() == wanted:
            return value.decode("latin-1")
    return None


def _static_directory() -> str:
    """Resolve the vendored static assets directory inside the package."""
    from importlib.resources import files

    return str(files("kairix.platform.setup.web") / "static")


def _progress_pct(*, chunks_done: int, chunks_total: int, done: bool) -> int:
    """Whole-number progress percentage, safe for a zero total."""
    if done:
        return 100
    if chunks_total <= 0:
        return 0
    return min(100, (chunks_done * 100) // chunks_total)


def _string_fields(form: Mapping[str, Any]) -> dict[str, str]:
    """String-valued form fields only — file-upload values are dropped."""
    return {key: str(value) for key, value in form.items() if isinstance(value, str)}


def _progress_headers(status: Any) -> dict[str, str] | None:
    """``HX-Redirect`` header set once indexing finished cleanly."""
    if status.done and not status.error:
        return {"HX-Redirect": _FIRST_SEARCH_URL}
    return None


def build_setup_wizard_mount(
    *,
    service_factory: Callable[[], SetupService] = _default_service_factory,
    secrets: SecretsResolver | None = None,
    provider_names_fn: Callable[[], tuple[str, ...]] = _default_provider_names,
) -> Mount:
    """Compose the wizard's routes into a guarded ``Mount("/setup", ...)``.

    Args:
        service_factory: Callable returning the :class:`SetupService`
            the screens render against. Resolved lazily on first
            request and memoised; the production default is the
            ``build_setup_service`` factory.
        secrets: Secrets resolver for the operator-token guard. Tests
            pass ``FakeSecretsLoader``; defaults to the production
            :class:`~kairix.secrets.loader.SecretsLoader`.
        provider_names_fn: Source of provider names for the picker
            screen. The default reads the installed provider plugin
            registry.

    Returns:
        A Starlette ``Mount`` ready to append to the MCP app's routes.
    """
    factory = service_factory
    names_fn = provider_names_fn
    env = _build_template_env()
    # Lazily-resolved, memoised service. A one-element list keeps the
    # closure mutable without module-level state.
    holder: list[SetupService] = []

    def _service() -> SetupService:
        if not holder:
            holder.append(factory())
        return holder[0]

    def _render(template_name: str, context: dict[str, Any], **kwargs: Any) -> HTMLResponse:
        return HTMLResponse(env.get_template(template_name).render(**context), **kwargs)

    def _endpoint(handler: Callable[[Request, SetupService], Response]) -> Callable[[Request], Any]:
        """Wrap a handler with service resolution.

        Every screen resolves the service — including the welcome
        screen, which doesn't strictly need it — so a flag-ON
        deployment without the wizard backend fails on the FIRST
        request with the stub's structured message, not three screens
        into the flow.
        """

        def endpoint(request: Request) -> Response:
            # Sync on purpose (Sonar S7503 — nothing here awaits);
            # Starlette runs sync endpoints on its threadpool.
            try:
                service = _service()
            except NotImplementedError as exc:
                return PlainTextResponse(str(exc), status_code=503)
            return handler(request, service)

        return endpoint

    def _form_endpoint(handler: Callable[[dict[str, str], SetupService], Response]) -> Callable[[Request], Any]:
        """Like :func:`_endpoint` but parses the urlencoded form first."""

        async def endpoint(request: Request) -> Response:
            try:
                service = _service()
            except NotImplementedError as exc:
                return PlainTextResponse(str(exc), status_code=503)
            form = await request.form()
            return handler(_string_fields(form), service)

        return endpoint

    def welcome(_request: Request, _service_: SetupService) -> Response:
        return _render(_TPL_WELCOME, {"step": 1})

    def provider_screen(_request: Request, _service_: SetupService) -> Response:
        return _render(_TPL_PROVIDER, {"step": 2, "providers": names_fn()})

    def key_screen(request: Request, _service_: SetupService) -> Response:
        provider = request.query_params.get("provider", "")
        if not provider:
            return RedirectResponse(_PROVIDER_URL, status_code=303)
        return _render(_TPL_KEY, _key_context(provider))

    def key_validate(fields: dict[str, str], service: SetupService) -> Response:
        validation = service.validate_provider(
            fields.get(_FIELD_PROVIDER, ""),
            fields.get(_FIELD_API_KEY, ""),
            fields.get(_FIELD_ENDPOINT) or None,
            deployment=fields.get(_FIELD_DEPLOYMENT) or None,
        )
        return _render(_TPL_KEY_VALIDATION, {"validation": validation})

    def key_save(fields: dict[str, str], service: SetupService) -> Response:
        return _handle_key_save(fields, service, _render)

    def folder_screen(_request: Request, service: SetupService) -> Response:
        return _render(_TPL_FOLDER, {"step": 4, "hint": service.source_hint()})

    def folder_scan(fields: dict[str, str], service: SetupService) -> Response:
        path = fields.get(_FIELD_FOLDER_PATH, "")
        scan = service.scan_folder(path)
        return _render(_TPL_FOLDER_SCAN, {"scan": scan, _FIELD_FOLDER_PATH: path})

    def folder_save(fields: dict[str, str], service: SetupService) -> Response:
        return _handle_folder_save(fields, service, _render)

    def indexing_screen(_request: Request, _service_: SetupService) -> Response:
        return _render(_TPL_INDEXING, {"step": 5})

    def indexing_progress(_request: Request, service: SetupService) -> Response:
        status = service.index_status()
        pct = _progress_pct(chunks_done=status.chunks_done, chunks_total=status.chunks_total, done=status.done)
        return _render(_TPL_INDEXING_PROGRESS, {"status": status, "pct": pct}, headers=_progress_headers(status))

    def first_search_screen(_request: Request, _service_: SetupService) -> Response:
        return _render(_TPL_FIRST_SEARCH, {"step": 6})

    def search(fields: dict[str, str], service: SetupService) -> Response:
        query = fields.get("query", "")
        preview = service.first_search(query)
        return _render(_TPL_SEARCH_RESULTS, {"preview": preview, "query": query})

    def connect_agent_screen(_request: Request, service: SetupService) -> Response:
        info = service.agent_connect_info()
        return _render(_TPL_CONNECT_AGENT, {"step": 7, "info": info})

    def connect_agent_verify(_fields: dict[str, str], service: SetupService) -> Response:
        result = service.verify_agent_handshake()
        return _render(_TPL_HANDSHAKE, {"result": result})

    def done_screen(_request: Request, service: SetupService) -> Response:
        status = service.index_status()
        return _render(_TPL_DONE, {"status": status})

    routes: list[BaseRoute] = [
        Route("/", _endpoint(welcome), methods=["GET"]),
        Route("/provider", _endpoint(provider_screen), methods=["GET"]),
        Route("/key", _endpoint(key_screen), methods=["GET"]),
        Route("/key", _form_endpoint(key_save), methods=["POST"]),
        Route("/key/validate", _form_endpoint(key_validate), methods=["POST"]),
        Route("/folder", _endpoint(folder_screen), methods=["GET"]),
        Route("/folder", _form_endpoint(folder_save), methods=["POST"]),
        Route("/folder/scan", _form_endpoint(folder_scan), methods=["POST"]),
        Route("/indexing", _endpoint(indexing_screen), methods=["GET"]),
        Route("/indexing/progress", _endpoint(indexing_progress), methods=["GET"]),
        Route("/first-search", _endpoint(first_search_screen), methods=["GET"]),
        Route("/search", _form_endpoint(search), methods=["POST"]),
        Route("/connect-agent", _endpoint(connect_agent_screen), methods=["GET"]),
        Route("/connect-agent/verify", _form_endpoint(connect_agent_verify), methods=["POST"]),
        Route("/done", _endpoint(done_screen), methods=["GET"]),
        Mount("/static", app=StaticFiles(directory=_static_directory()), name="setup-static"),
    ]

    from starlette.routing import Router

    resolved_secrets = secrets if secrets is not None else _default_secrets_resolver()
    guarded = OperatorTokenGuard(Router(routes=routes), secrets=resolved_secrets)
    return Mount(SETUP_PATH_PREFIX, app=guarded, name="setup-wizard")


__all__ = [
    "OPERATOR_TOKEN_HEADER",
    "SETUP_PATH_PREFIX",
    "OperatorTokenGuard",
    "build_setup_wizard_mount",
]
