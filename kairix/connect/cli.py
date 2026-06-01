"""``kairix connect`` CLI dispatcher.

Operator surface for capturing OAuth2 tokens via the ``kairix connect
<service>`` family. Phase 1 wires:

  * ``kairix connect google-gmail`` — Gmail OAuth2 flow
  * ``kairix connect google-drive`` — Google Drive OAuth2 flow
  * ``kairix connect google-calendar`` — Google Calendar OAuth2 flow

Each subcommand:
  1. Reads the OAuth client credentials from ``--client-secret-path``.
  2. Starts the localhost callback listener (``--port``, default 8080).
  3. Opens the browser to the consent screen.
  4. Captures the code via the listener (timeout 120s by default).
  5. Exchanges the code for tokens.
  6. Writes the tokens to the chosen store (``--store``, default ``file``).
  7. Prints a success summary listing canonical names and store target.

F6-compliance: tests inject :class:`ConnectDeps` rather than passing
``*_fn=None`` kwargs to the free functions. The deps dataclass mirrors
``kairix.worker.WorkerDeps``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from kairix.connect.listener import DEFAULT_PORT, LocalhostCallbackListener
from kairix.connect.oauth2.google import GoogleOAuth2Flow
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

# Map of CLI subcommand → (service_area, scope override). Drives the
# ``GoogleOAuth2Flow`` construction and the canonical-name writes.
_GOOGLE_SUBCOMMANDS: dict[str, str] = {
    "google-gmail": "gmail",
    "google-drive": "google-drive",
    "google-calendar": "google-calendar",
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
        :class:`OAuth2Flow` given subcommand + client_secret_path +
        port. Tests inject a fake that returns the captured tokens
        directly.
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
    oauth2_flow_factory: Callable[[str, Path, int], OAuth2Flow] = field(
        default_factory=lambda: _default_oauth2_flow_factory,
    )
    token_store_factory: Callable[[str], TokenStore] = field(
        default_factory=lambda: _default_token_store_factory,
    )
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)


def _default_listener_factory(host: str, port: int) -> CallbackListener:
    return LocalhostCallbackListener(host=host, port=port)


def _default_oauth2_flow_factory(subcommand: str, client_secret_path: Path, _port: int) -> OAuth2Flow:
    """Build the per-subcommand OAuth2Flow. Phase 1 covers Google only."""
    service_area = _GOOGLE_SUBCOMMANDS.get(subcommand)
    if service_area is None:
        raise ValueError(
            f"kairix connect: unsupported subcommand {subcommand!r}. "
            f"fix: pass one of {sorted(_GOOGLE_SUBCOMMANDS)}. "
            f"next: see kairix/connect/README.md for the supported services. "
            f"run: kairix connect google-gmail --client-secret-path <path>",
        )
    return GoogleOAuth2Flow(
        service_area=service_area,
        client_secret_path=client_secret_path,
    )


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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kairix connect",
        description=(
            "Capture OAuth2 tokens for a kairix connector. Opens the operator's browser "
            "to the service's consent screen, captures the callback on a localhost listener, "
            "and writes canonical-named secrets to the chosen store backend."
        ),
    )
    parser.add_argument(
        "subcommand",
        choices=sorted(_GOOGLE_SUBCOMMANDS),
        help="Which service to connect (one of: google-gmail | google-drive | google-calendar).",
    )
    parser.add_argument(
        "--client-secret-path",
        required=True,
        type=Path,
        help="Path to the operator-downloaded client_secret.json from the GCP console.",
    )
    parser.add_argument(
        "--store",
        default="file",
        help=(
            "Where to write the captured tokens. "
            "One of: file (default, writes $KAIRIX_SECRETS_FILE) | "
            "stdout (TSV emission) | "
            "azure-kv[:<vault-name>|:<vault-url>] (Azure Key Vault via DefaultAzureCredential)."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Localhost port for the OAuth callback listener. Default {DEFAULT_PORT}.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for the OAuth callback listener. Default 127.0.0.1.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Seconds to wait for the operator to complete the browser flow. Default 120.",
    )
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
        flow = deps.oauth2_flow_factory(args.subcommand, args.client_secret_path, args.port)
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
    try:
        store = deps.token_store_factory(args.store)
    except ValueError as exc:
        deps.stderr.write(str(exc) + "\n")
        return 1
    scope: Scope = "connector"
    area = _GOOGLE_SUBCOMMANDS[args.subcommand]
    try:
        report = store.store(
            scope=scope,
            area=area,
            instance=None,
            tokens=tokens,
            client=client,
        )
    except ConnectError as exc:
        deps.stderr.write(str(exc) + "\n")
        return 1
    _print_success(deps.stdout, args.subcommand, report, tokens, client)
    return 0


def _print_success(
    stdout: TextIO,
    subcommand: str,
    report: WriteReport,
    _tokens: CapturedTokens,
    _client: ClientCredentials,
) -> None:
    """Print the operator-facing success summary.

    F15-clean: the token values are deliberately not echoed; only the
    canonical names are printed. Operators read the values back from
    the configured store.
    """
    stdout.write(f"kairix connect {subcommand}: ok\n")
    stdout.write(f"  backend: {report.backend}\n")
    if report.target:
        stdout.write(f"  target:  {report.target}\n")
    stdout.write("  names:\n")
    for name in report.canonical_names:
        stdout.write(f"    - {name}\n")


if __name__ == "__main__":
    raise SystemExit(main())
