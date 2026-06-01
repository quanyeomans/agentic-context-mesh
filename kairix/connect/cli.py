"""``kairix connect`` CLI dispatcher.

Operator surface for capturing OAuth2 tokens via the ``kairix connect
<service>`` family. Phase 1 + Phase 2 + Phase 3 wires:

  * ``kairix connect google-gmail`` — Gmail OAuth2 flow
  * ``kairix connect google-drive`` — Google Drive OAuth2 flow
  * ``kairix connect google-calendar`` — Google Calendar OAuth2 flow
  * ``kairix connect slack --workspace <name>`` — Slack OAuth v2 flow,
    per-workspace canonical-naming via the ``instance`` slot
  * ``kairix connect github-app --app-id <id> --private-key-path <pem>``
    — GitHub App install + JWT exchange; carries ``installation_id``
    via the ``CapturedTokens.metadata`` slot

Each subcommand:
  1. Reads the OAuth client credentials from the per-service source
     (Google: ``--client-secret-path``; Slack: ``--client-id`` +
     ``--client-secret``; GitHub App: ``--app-id`` + ``--private-key-path``).
  2. Starts the localhost callback listener (``--port``, default 8080).
  3. Opens the browser to the consent / install screen.
  4. Captures the callback via the listener (timeout 120s by default).
  5. Exchanges the captured value for tokens (Google: code → tokens;
     GitHub App: installation_id + JWT → installation access token).
  6. Writes the tokens to the chosen store (``--store``, default ``file``).
  7. Prints a success summary listing canonical names and store target.

F6-compliance: tests inject :class:`ConnectDeps` rather than passing
``*_fn=None`` kwargs to the free functions. The deps dataclass mirrors
``kairix.worker.WorkerDeps``.

Dispatch is via :data:`SUBCOMMAND_REGISTRY` — a mapping from
subcommand name to a flow-factory callable. New services land by
adding one row + one subparser; no if/elif chain.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from kairix.connect.listener import DEFAULT_PORT, LocalhostCallbackListener
from kairix.connect.oauth2.github_app import GITHUB_APP_SERVICE_AREA, GitHubAppFlow
from kairix.connect.oauth2.google import GoogleOAuth2Flow
from kairix.connect.oauth2.slack import SLACK_SERVICE_AREA, SlackOAuth2Flow
from kairix.connect.protocols import (
    CallbackListener,
    CapturedTokens,
    ClientCredentials,
    ConnectError,
    OAuth2Flow,
    TokenStore,
    WriteReport,
)
from kairix.connect.store.azure_kv_store import AzureKeyVaultTokenStore
from kairix.connect.store.file_store import FileTokenStore
from kairix.connect.store.stdout_store import StdoutTokenStore
from kairix.secrets.naming import Scope


# Per-subcommand spec: how to derive (area, instance) for the canonical
# write AND how to build the OAuth2Flow from the argparse namespace.
@dataclass(frozen=True)
class _SubcommandSpec:
    """One row in :data:`SUBCOMMAND_REGISTRY`.

    Fields:

      * ``service_area`` — the canonical-naming "area" slot (per ADR-031).
        Google subcommands set distinct areas (``"gmail"``,
        ``"google-drive"``, ``"google-calendar"``); Slack sets
        ``"slack"`` and uses the ``instance`` slot for the workspace.
      * ``flow_builder`` — callable receiving the argparse Namespace
        and returning a concrete :class:`OAuth2Flow`. Slack reads
        ``args.workspace`` + ``args.client_id`` + ``args.client_secret``;
        Google reads ``args.client_secret_path``.
      * ``instance_reader`` — callable receiving the argparse
        Namespace and returning the canonical-naming ``instance`` slot
        value (``None`` for Google singletons; the workspace name for
        Slack so per-workspace tokens land in distinct KV entries).
    """

    service_area: str
    flow_builder: Callable[[argparse.Namespace], OAuth2Flow]
    instance_reader: Callable[[argparse.Namespace], str | None]


def _build_google_flow(subcommand: str) -> Callable[[argparse.Namespace], OAuth2Flow]:
    """Return a flow-builder closure for one Google subcommand."""

    def build(args: argparse.Namespace) -> OAuth2Flow:
        return GoogleOAuth2Flow(
            service_area=_GOOGLE_AREA_FOR_SUBCOMMAND[subcommand],
            client_secret_path=args.client_secret_path,
        )

    return build


def _build_slack_flow(args: argparse.Namespace) -> OAuth2Flow:
    """Build a :class:`SlackOAuth2Flow` from the parsed CLI args."""
    return SlackOAuth2Flow(
        workspace=args.workspace,
        client_id=args.client_id,
        client_secret=args.client_secret,
    )


def _build_github_app_flow(args: argparse.Namespace) -> OAuth2Flow:
    """Build a :class:`GitHubAppFlow` from the parsed CLI args.

    GitHub App's flow needs three inputs the Google + Slack flows
    don't share: the numeric App id (``--app-id``), the PEM private
    key (``--private-key-path``), and the App URL slug
    (``--app-slug``, default ``"kairix-bot"``) that drives the install
    URL.
    """
    return GitHubAppFlow(
        app_id=args.app_id,
        private_key_path=args.private_key_path,
        app_slug=args.app_slug,
    )


def _none_instance(_args: argparse.Namespace) -> str | None:
    """Instance-reader for singleton services (Google + GitHub App)."""
    return None


def _slack_workspace_instance(args: argparse.Namespace) -> str | None:
    """Instance-reader for Slack — the workspace name lands in the slot."""
    return str(args.workspace)


# Subcommand names — hoisted constants so F17 (no string literal ≥10 chars
# duplicated ≥3 times) stays clean across the registry, the argparse
# subparser registration, and the per-subcommand flow builders.
_CMD_GOOGLE_GMAIL = "google-gmail"
_CMD_GOOGLE_DRIVE = "google-drive"
_CMD_GOOGLE_CALENDAR = "google-calendar"

# Map of Google subcommand → canonical service-area. Drives both the
# argparse subparser registration and the per-subcommand flow builder.
_GOOGLE_AREA_FOR_SUBCOMMAND: dict[str, str] = {
    _CMD_GOOGLE_GMAIL: "gmail",
    _CMD_GOOGLE_DRIVE: _CMD_GOOGLE_DRIVE,
    _CMD_GOOGLE_CALENDAR: _CMD_GOOGLE_CALENDAR,
}


# Public dispatch registry. New services land by adding one row here +
# one subparser in :func:`_build_parser`. The dispatch is then automatic
# — no if/elif chain inside :func:`_run`.
SUBCOMMAND_REGISTRY: dict[str, _SubcommandSpec] = {
    _CMD_GOOGLE_GMAIL: _SubcommandSpec(
        service_area="gmail",
        flow_builder=_build_google_flow(_CMD_GOOGLE_GMAIL),
        instance_reader=_none_instance,
    ),
    _CMD_GOOGLE_DRIVE: _SubcommandSpec(
        service_area=_CMD_GOOGLE_DRIVE,
        flow_builder=_build_google_flow(_CMD_GOOGLE_DRIVE),
        instance_reader=_none_instance,
    ),
    _CMD_GOOGLE_CALENDAR: _SubcommandSpec(
        service_area=_CMD_GOOGLE_CALENDAR,
        flow_builder=_build_google_flow(_CMD_GOOGLE_CALENDAR),
        instance_reader=_none_instance,
    ),
    "slack": _SubcommandSpec(
        service_area=SLACK_SERVICE_AREA,
        flow_builder=_build_slack_flow,
        instance_reader=_slack_workspace_instance,
    ),
    "github-app": _SubcommandSpec(
        service_area=GITHUB_APP_SERVICE_AREA,
        flow_builder=_build_github_app_flow,
        instance_reader=_none_instance,
    ),
}


@dataclass(frozen=True)
class ConnectDeps:
    """Injectable dependencies for the ``kairix connect`` CLI.

    F6-clean: every field is non-Optional with a ``default_factory`` so
    mypy sees the real callable directly. Tests construct
    ``ConnectDeps(listener_factory=fake, ...)`` to pin the flow without
    monkeypatching.

    Fields:

      * ``listener_factory`` — builds a :class:`CallbackListener` given
        host + port. Production default constructs
        :class:`LocalhostCallbackListener`; tests inject a fake that
        returns a recording stub.
      * ``oauth2_flow_factory`` — builds the per-service
        :class:`OAuth2Flow` given the parsed argparse Namespace. Tests
        inject a fake that returns a recording flow directly.
      * ``token_store_factory`` — builds the :class:`TokenStore` given
        the ``--store`` string. Tests inject a fake that records the
        write target.
      * ``stdout`` — TextIO the summary line writes to. Defaults to
        ``sys.stdout``.
      * ``stderr`` — TextIO error messages write to. Defaults to
        ``sys.stderr``.
    """

    listener_factory: Callable[[str, int], CallbackListener] = field(
        default_factory=lambda: _default_listener_factory,
    )
    oauth2_flow_factory: Callable[[argparse.Namespace], OAuth2Flow] = field(
        default_factory=lambda: _default_oauth2_flow_factory,
    )
    token_store_factory: Callable[[str], TokenStore] = field(
        default_factory=lambda: _default_token_store_factory,
    )
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)


def _default_listener_factory(host: str, port: int) -> CallbackListener:
    return LocalhostCallbackListener(host=host, port=port)


def _default_oauth2_flow_factory(args: argparse.Namespace) -> OAuth2Flow:
    """Build the per-subcommand OAuth2Flow from the parsed argv.

    Routes through :data:`SUBCOMMAND_REGISTRY` — adding a new service
    means appending one row to the registry + one subparser to
    :func:`_build_parser`; this dispatcher stays unchanged.
    """
    spec = SUBCOMMAND_REGISTRY.get(args.subcommand)
    if spec is None:
        raise ValueError(
            f"kairix connect: unsupported subcommand {args.subcommand!r}. "
            f"fix: pass one of {sorted(SUBCOMMAND_REGISTRY)}. "
            f"next: see kairix/connect/README.md for the supported services. "
            f"run: kairix connect google-gmail --client-secret-path <path>",
        )
    return spec.flow_builder(args)


def _default_token_store_factory(store_spec: str) -> TokenStore:
    """Resolve the ``--store=...`` string to a concrete :class:`TokenStore`."""
    if store_spec in ("file", "default", ""):
        return FileTokenStore()
    if store_spec == "stdout":
        return StdoutTokenStore()
    if store_spec.startswith("azure-kv"):
        return _build_azure_kv_store(store_spec)
    raise ValueError(
        f"kairix connect: unknown --store value {store_spec!r}. "
        f"fix: pass one of file | stdout | azure-kv:<vault-name> | azure-kv:<vault-url>. "
        f"next: see kairix/connect/README.md for the store backend matrix. "
        f"run: kairix connect <service> --store=file --client-secret-path <path>",
    )


def _build_azure_kv_store(store_spec: str) -> TokenStore:
    """Parse the ``azure-kv[:<vault-name-or-url>]`` form into the store."""
    # store_spec is one of:
    #   "azure-kv"                                    — read $KAIRIX_KV_NAME
    #   "azure-kv:my-vault"                           — short-name form
    #   "azure-kv:https://my-vault.vault.azure.net/"  — full-URL form
    suffix = store_spec[len("azure-kv") :]
    if not suffix:
        return AzureKeyVaultTokenStore()
    if not suffix.startswith(":"):
        raise ValueError(
            f"kairix connect: malformed --store value {store_spec!r}. "
            f"fix: pass --store=azure-kv (read $KAIRIX_KV_NAME) OR "
            f"--store=azure-kv:<vault-name> OR --store=azure-kv:<full-vault-url>. "
            f"next: see kairix/connect/README.md for the store backend matrix. "
            f"run: kairix connect <service> --store=azure-kv:<vault-name> --client-secret-path <path>",
        )
    value = suffix[1:]
    if value.startswith("https://") or value.startswith("http://"):
        return AzureKeyVaultTokenStore(vault_url=value)
    return AzureKeyVaultTokenStore(vault_name=value)


def _add_common_store_args(p: argparse.ArgumentParser) -> None:
    """Attach the shared ``--store / --port / --host / --timeout`` flags.

    Lifted to one place so adding a new subcommand doesn't drift on
    these — every subcommand has identical store + listener semantics.
    """
    p.add_argument(
        "--store",
        default="file",
        help=(
            "Where to write the captured tokens. "
            "One of: file (default, writes $KAIRIX_SECRETS_FILE) | "
            "stdout (TSV emission) | "
            "azure-kv[:<vault-name>|:<vault-url>] (Azure Key Vault via DefaultAzureCredential)."
        ),
    )
    p.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Localhost port for the OAuth callback listener. Default {DEFAULT_PORT}.",
    )
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for the OAuth callback listener. Default 127.0.0.1.",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Seconds to wait for the operator to complete the browser flow. Default 120.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kairix connect",
        description=(
            "Capture OAuth2 tokens for a kairix connector. Opens the operator's browser "
            "to the service's consent screen, captures the callback on a localhost listener, "
            "and writes canonical-named secrets to the chosen store backend."
        ),
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # Google family — three subcommands share the same argv shape.
    for cmd in sorted(_GOOGLE_AREA_FOR_SUBCOMMAND):
        p = sub.add_parser(
            cmd,
            help=f"Connect {cmd} — captures Google OAuth2 tokens for the matching service.",
        )
        p.add_argument(
            "--client-secret-path",
            required=True,
            type=Path,
            help="Path to the operator-downloaded client_secret.json from the GCP console.",
        )
        _add_common_store_args(p)

    # Slack — per-workspace via the canonical-naming ``instance`` slot.
    slack_p = sub.add_parser(
        "slack",
        help="Connect slack — captures a Slack workspace bot token via OAuth v2.",
    )
    slack_p.add_argument(
        "--workspace",
        required=True,
        type=str,
        help=(
            "Operator-chosen workspace identifier — lands in the canonical-naming "
            "instance slot (kairix-connector-slack-<workspace>-bot-token). Use a slug "
            "like 'alpha' or 'coach' so per-workspace tokens stay distinct in your KV."
        ),
    )
    slack_p.add_argument(
        "--client-id",
        required=True,
        type=str,
        help=("Slack app's OAuth client_id from https://api.slack.com/apps -> Basic Information."),
    )
    slack_p.add_argument(
        "--client-secret",
        required=True,
        type=str,
        help=("Slack app's OAuth client_secret from https://api.slack.com/apps -> Basic Information."),
    )
    _add_common_store_args(slack_p)

    # GitHub App — App-id + PEM private key drive a JWT-signed install flow.
    gh_p = sub.add_parser(
        "github-app",
        help="Connect github-app — captures a GitHub App installation id via the App install flow.",
    )
    gh_p.add_argument(
        "--app-id",
        required=True,
        type=str,
        help=(
            "GitHub App numeric id from github.com/settings/apps/<your-app> 'About' "
            "section. Drives the JWT 'iss' claim used to mint installation access tokens."
        ),
    )
    gh_p.add_argument(
        "--private-key-path",
        required=True,
        type=Path,
        help=(
            "Path to the GitHub App PEM private key. Download from "
            "github.com/settings/apps/<your-app> -> 'Private keys' -> "
            "'Generate a private key'. The key file is the long-lived "
            "credential — installation access tokens are minted on demand."
        ),
    )
    gh_p.add_argument(
        "--app-slug",
        default="kairix-bot",
        type=str,
        help=(
            "GitHub App URL slug used to construct the install URL "
            "(https://github.com/apps/<slug>/installations/new). Default "
            "'kairix-bot' — override if your App was published under a "
            "different slug."
        ),
    )
    _add_common_store_args(gh_p)
    return parser


def main(argv: Sequence[str] | None = None, *, deps: ConnectDeps | None = None) -> int:
    """Entry point for ``kairix connect <subcommand>``.

    Returns 0 on success, 1 on operator-correctable error, 2 on
    bug-like internal error. Always prints a summary line to
    ``deps.stdout`` on success; prints the F21-shaped error to
    ``deps.stderr`` on failure.
    """
    deps = deps if deps is not None else ConnectDeps()
    parser = _build_parser()
    args = parser.parse_args(argv)
    return _run(args, deps)


def _run(args: argparse.Namespace, deps: ConnectDeps) -> int:
    """Execute the connect flow. Lifted to keep ``main`` simple (F16)."""
    try:
        listener = deps.listener_factory(args.host, args.port)
    except OSError as exc:
        deps.stderr.write(str(exc) + "\n")
        return 1
    try:
        flow = deps.oauth2_flow_factory(args)
    except (FileNotFoundError, ValueError) as exc:
        listener.close()
        deps.stderr.write(str(exc) + "\n")
        return 1
    try:
        client = flow.discover_client_credentials()
        tokens = flow.authorize(listener=listener)
    except ConnectError as exc:
        listener.close()
        deps.stderr.write(str(exc) + "\n")
        return 1
    except FileNotFoundError as exc:
        listener.close()
        deps.stderr.write(str(exc) + "\n")
        return 1
    except (ValueError, RuntimeError) as exc:
        # Per-service OAuth flows raise ValueError/RuntimeError when the
        # provider response is malformed or the operator's input is
        # invalid (Google: bad client_secret.json; GitHub App: missing
        # installation_id callback). Surface the F21 message to stderr
        # and return rc=1 so the operator gets the same error contract
        # as the F-rule ConnectError path.
        listener.close()
        deps.stderr.write(str(exc) + "\n")
        return 1
    try:
        store = deps.token_store_factory(args.store)
    except ValueError as exc:
        deps.stderr.write(str(exc) + "\n")
        return 1
    spec = SUBCOMMAND_REGISTRY[args.subcommand]
    scope: Scope = "connector"
    instance = spec.instance_reader(args)
    try:
        report = store.store(
            scope=scope,
            area=spec.service_area,
            instance=instance,
            tokens=tokens,
            client=client,
        )
    except ConnectError as exc:
        deps.stderr.write(str(exc) + "\n")
        return 1
    _print_success(deps.stdout, args.subcommand, report, tokens, client, flow)
    return 0


def _print_success(
    stdout: TextIO,
    subcommand: str,
    report: WriteReport,
    _tokens: CapturedTokens,
    _client: ClientCredentials,
    flow: OAuth2Flow,
) -> None:
    """Print the operator-facing success summary.

    F15-clean: the token values are deliberately not echoed; only the
    canonical names are printed. Operators read the values back from
    the configured store.

    Slack adds an extra line naming the team — the Slack OAuth
    response carries ``team_id`` + ``team_name`` so the operator
    sees which workspace the bot was actually installed into (a
    typo in ``--workspace`` would otherwise silently land tokens
    under the wrong instance).
    """
    stdout.write(f"kairix connect {subcommand}: ok\n")
    stdout.write(f"  backend: {report.backend}\n")
    if report.target:
        stdout.write(f"  target:  {report.target}\n")
    team_id = getattr(flow, "team_id", "") or ""
    team_name = getattr(flow, "team_name", "") or ""
    if team_id or team_name:
        stdout.write(f"  team:    {team_name} ({team_id})\n")
    stdout.write("  names:\n")
    for name in report.canonical_names:
        stdout.write(f"    - {name}\n")


if __name__ == "__main__":
    raise SystemExit(main())
