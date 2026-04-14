# Planning: Incremental File Watcher

**Status:** Planned — not yet started  
**Target version:** v1.0.0  
**Primary motivation:** The current embed cron runs every 60 seconds. A file added to the vault is invisible to search for up to 59 seconds. Session prep queries against recently-added content (today's meeting notes, last-minute decision records) return stale results during that window.

---

## Problem statement

`kairix embed --changed` is triggered by a cron job every 60 seconds. This is the only mechanism for indexing vault changes. Two failure modes:

1. **Staleness window** — new vault content is invisible to search for up to 59 seconds after write
2. **Missed changes** — if a file is written and deleted/renamed within a 60-second window, the cron never sees it

Both matter most during active work sessions, exactly when session-prep queries are most likely.

A file watcher eliminates the cron gap: the watcher triggers `kairix embed --paths <changed_file>` within seconds of each vault write.

---

## Approach

Use `watchfiles` (wraps `inotify` on Linux, `kqueue` on macOS, `ReadDirectoryChangesW` on Windows). The watcher runs as a long-lived daemon alongside the main kairix process. On each vault change event, it calls `kairix embed` for the changed paths.

```
Vault write (Obsidian / sync)
        │
        ▼
watchfiles inotify event (< 1s)
        │
        ▼
Debounce 2s (accumulate batch of concurrent writes)
        │
        ▼
kairix embed --paths <file1> [<file2> ...]
        │
        ▼
Files indexed; available to search immediately
```

Debouncing is important: Obsidian writes in bursts (save + frontmatter update + wikilink injection). A 2-second debounce collapses these into one embed call.

---

## Implementation plan

### New file: `kairix/watcher/daemon.py`

```python
"""
kairix.watcher.daemon
~~~~~~~~~~~~~~~~~~~~~

Incremental vault file watcher daemon.

Watches KAIRIX_VAULT_ROOT for .md file changes. Debounces events over a
configurable window, then dispatches kairix.embed.run() for changed paths.

Runs until interrupted (SIGINT / SIGTERM). Designed to run as a sidecar
process alongside the main kairix service.

Inputs:
  vault_root: Path — directory to watch (recursively)
  debounce_seconds: float — accumulation window before triggering embed (default 2.0)
  extensions: frozenset[str] — file extensions to watch (default: {'.md'})

Failure modes:
  - Single file embed failure: logged as ERROR; watcher continues for subsequent events
  - vault_root does not exist: raises ValueError at startup
  - watchfiles unavailable: raises ImportError with install instructions
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS: float = 2.0         # Accumulate burst writes before embedding. Tuned for Obsidian save pattern.
WATCH_EXTENSIONS: frozenset[str] = frozenset({".md"})


def watch(
    vault_root: Path,
    embed_fn: Callable[[list[Path]], None],
    debounce_seconds: float = DEBOUNCE_SECONDS,
    extensions: frozenset[str] = WATCH_EXTENSIONS,
) -> None:
    """Watch vault_root and call embed_fn with batches of changed paths.

    Blocks until interrupted. embed_fn receives a deduplicated list of
    changed Path objects. Any exception from embed_fn is caught and logged;
    the watcher continues.
    """
    try:
        from watchfiles import watch as _watch, Change  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "kairix.watcher requires 'watchfiles'. Install: pip install watchfiles"
        ) from exc

    if not vault_root.exists():
        raise ValueError(f"vault_root does not exist: {vault_root}")

    logger.info("watcher: watching %s (debounce %.1fs)", vault_root, debounce_seconds)

    pending: set[Path] = set()
    last_event_time: float = 0.0

    for changes in _watch(vault_root, recursive=True):
        changed_paths = {
            Path(path)
            for change_type, path in changes
            if change_type in (Change.added, Change.modified)
            and Path(path).suffix in extensions
        }
        if not changed_paths:
            continue

        pending.update(changed_paths)
        last_event_time = time.monotonic()

        # Debounce: wait until no new events for debounce_seconds
        time.sleep(debounce_seconds)
        if time.monotonic() - last_event_time < debounce_seconds:
            continue   # More events arrived during sleep; keep accumulating

        batch = list(pending)
        pending.clear()

        logger.info("watcher: embedding %d changed file(s)", len(batch))
        try:
            embed_fn(batch)
        except Exception as exc:
            logger.error("watcher: embed failed for batch of %d — %s", len(batch), exc)
```

### New file: `kairix/watcher/cli.py`

```python
"""
kairix.watcher.cli
~~~~~~~~~~~~~~~~~~

CLI entry point for the file watcher daemon.

Usage:
    kairix watch [--vault-root PATH] [--debounce SECONDS]

Runs until SIGINT or SIGTERM. Designed to run as a background service
or Docker sidecar.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from pathlib import Path


def _embed_changed(paths: list[Path]) -> None:
    """Invoke kairix embed for a batch of changed paths."""
    from kairix.embed.embed import embed_paths  # type: ignore[import]
    embed_paths(paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kairix watch",
        description="Watch vault root and incrementally embed changed files.",
    )
    parser.add_argument(
        "--vault-root",
        default=os.environ.get("KAIRIX_VAULT_ROOT", ""),
        help="Path to Obsidian vault root (default: KAIRIX_VAULT_ROOT env var)",
    )
    parser.add_argument(
        "--debounce",
        type=float,
        default=2.0,
        help="Seconds to accumulate burst writes before triggering embed (default: 2.0)",
    )
    args = parser.parse_args(argv)

    if not args.vault_root:
        parser.error("--vault-root or KAIRIX_VAULT_ROOT is required")

    vault_root = Path(args.vault_root)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

    def _handle_signal(signum: int, _frame: object) -> None:
        logger = logging.getLogger(__name__)
        logger.info("watcher: received signal %d, exiting", signum)
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    from kairix.watcher.daemon import watch
    watch(vault_root, _embed_changed, debounce_seconds=args.debounce)
    return 0
```

### New file: `kairix/watcher/__init__.py`

Empty — package marker.

### Changes to `kairix/cli.py`

Add `watch` subcommand dispatch:

```python
elif args.command == "watch":
    from kairix.watcher.cli import main as watch_main
    sys.exit(watch_main(remaining_args))
```

### Changes to `pyproject.toml`

Add `watchfiles` dependency:

```toml
[project.dependencies]
# ... existing ...
"watchfiles>=0.21,<1.0"
```

Add `kairix watch` to the CLI entry points section if applicable.

---

## Docker integration

The file watcher is designed to run as a separate process alongside the main kairix service. Two deployment patterns:

### Pattern A: Docker Compose sidecar (recommended for VM deployment)

```yaml
# docker/docker-compose.yml addition
kairix-watcher:
  build:
    context: ..
    dockerfile: Dockerfile
  command: ["kairix", "watch", "--vault-root", "/path/to/vault"]
  depends_on:
    vault-agent:
      condition: service_healthy
  environment:
    KAIRIX_VAULT_ROOT: ${KAIRIX_VAULT_ROOT:-/path/to/vault}
    KAIRIX_DATA_DIR: ${KAIRIX_DATA_DIR:-/data/kairix}
  volumes:
    - kairix-secrets:/run/secrets:ro
    - ${KAIRIX_VAULT_ROOT:-/path/to/vault}:/path/to/vault:ro
    - ${KAIRIX_DATA_DIR:-/data/kairix}:/data/kairix
  restart: unless-stopped
```

### Pattern B: Supervisord within the kairix container

For single-container deployments, `supervisord` runs both the main kairix process and the watcher in the same container. Not preferred — violates single-responsibility — but documented for constrained environments.

---

## Cron relationship

The existing 60-second embed cron is **not removed** when the watcher is deployed. The watcher handles new and modified files; the cron acts as a safety net for:
- Files written while the watcher was restarting
- Vault syncs that bypass inotify (e.g., remote rsync writes)
- Bulk vault changes during watcher downtime

The cron interval can be increased to 10 minutes once the watcher is confirmed stable on VM, reducing redundant embed calls.

---

## Tests

### `tests/watcher/test_daemon.py`

| Test | Type | Description |
|---|---|---|
| `test_watch_calls_embed_fn_on_md_change` | unit | Mock `watchfiles.watch` yields one change; assert embed_fn called with that path |
| `test_watch_filters_non_md_extensions` | unit | Yield `.png` change; assert embed_fn not called |
| `test_watch_ignores_delete_events` | unit | Yield `Change.deleted` event; assert embed_fn not called |
| `test_watch_batches_concurrent_changes` | unit | Yield 3 .md changes in single iteration; embed_fn called once with 3 paths |
| `test_watch_continues_after_embed_error` | unit | embed_fn raises on first call; second change event still processed |
| `test_watch_raises_on_missing_vault_root` | unit | `ValueError` raised when vault_root does not exist |
| `test_watch_raises_on_missing_watchfiles` | unit | Simulate import error; `ImportError` with install instructions |
| `test_debounce_accumulates_burst` | unit | Two changes in quick succession → single embed_fn call with both paths |

### `tests/watcher/test_cli.py`

| Test | Type | Description |
|---|---|---|
| `test_cli_requires_vault_root` | unit | No `--vault-root` and no env var → sys.exit with error |
| `test_cli_reads_vault_root_from_env` | unit | `KAIRIX_VAULT_ROOT` set → passed to `watch()` |
| `test_cli_custom_debounce` | unit | `--debounce 5.0` → passed to `watch()` |

Coverage target: `kairix/watcher/` ≥ 80% (per ENGINEERING.md general module target).

---

## Rollout sequence

1. Implement `kairix/watcher/daemon.py` and `kairix/watcher/cli.py` with full test coverage
2. Add `watchfiles>=0.21` to `pyproject.toml`; run `pip-audit` to confirm no CVEs
3. Wire `kairix watch` subcommand into `kairix/cli.py`
4. Deploy to VM as a manual foreground process (`kairix watch --vault-root /path/to/vault`) to verify event capture
5. Measure time from vault write to search availability (target: < 5 seconds)
6. Add to `docker-compose.yml` as `kairix-watcher` service
7. Reduce cron interval from 60s to 10 minutes (safety net only)
8. Update OPERATIONS.md with watcher deployment and monitoring instructions

---

## Success criteria

| Metric | Target |
|---|---|
| Time from vault write to search availability | < 5 seconds (p95) |
| False positive embeds (non-.md changes trigger embed) | 0 |
| Watcher stability (mean time between crashes) | > 7 days on VM |
| Embed CPU overhead vs cron | ≤ cron overhead (batch sizes should be smaller) |
| Cron redundant embeds after deploy | ≤ 5% of watcher-triggered embeds |

---

## Dependencies and constraints

- `watchfiles>=0.21` — wraps OS-native filesystem notifications (inotify on Linux VM). Pure Python fallback uses polling at 0.2s intervals if inotify unavailable.
- The `embed_fn` passed to `watch()` must be idempotent — already-indexed files re-embedded return unchanged vectors (this is the existing embed pipeline behaviour)
- Vault root is mounted read-only in the watcher container — no write access required
- Signal handling (SIGTERM) is required for Docker `stop` graceful shutdown
- Debounce window of 2.0s is configurable; Obsidian's auto-save + wikilink injection pattern typically completes within 1.5s. Adjust via `--debounce` if vault sync produces longer burst windows.
