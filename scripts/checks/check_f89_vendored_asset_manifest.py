"""F89: every vendored browser-asset has a sha256-pinned manifest entry.

Motivation (EPIC #499 Phase 3; the un-pinned vendored-asset class)
------------------------------------------------------------------
The setup wizard ships third-party browser code — ``htmx.min.js`` drives
every async interaction; ``pico.classless.min.css`` is the whole visual
base — straight out of ``kairix/platform/setup/web/static/``. Nothing
recorded what upstream version those bytes came from. A teammate (or a
supply-chain attacker with write access) could drop a different
``htmx.min.js`` in place — a newer release that changed behaviour, or a
tampered build — and every test stays green: the wizard still renders,
the bytes just aren't the ones anyone reviewed. F89 makes the served
bytes a pinned contract: each static file carries a manifest row with
its upstream version, the sha256 of the exact bytes on disk, the
upstream URL, and a rationale, and the check fails when a file has no
row OR when its on-disk sha256 no longer matches the row (a swap or a
tamper).

What F89 governs (the served-asset surface)
-------------------------------------------
Every regular file under any ``kairix/**/web/static/`` tree EXCEPT the
manifest itself. Today that is one tree
(``kairix/platform/setup/web/static/``); the glob covers any future web
static dir for free. The manifest is a committed ``ASSETS.lock`` JSON
file living beside the assets in each static dir:

    {
      "assets": {
        "<filename>": {
          "vendored": true,
          "version": "1.9.12",
          "sha256": "<64 hex chars of the file's bytes>",
          "url": "https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js",
          "rationale": "<why this file, why this pin>"
        }
      }
    }

The manifest pins EVERY served file, vendored or first-party — a
first-party ``kairix.css`` carries ``"vendored": false`` but still gets
a sha row, so a tamper of it fails the same way a swapped vendor file
does. ``version``, ``url``, and ``rationale`` are required, non-empty
provenance fields; ``sha256`` is the load-bearing tamper guard.

A row is satisfied when ALL hold:
  * the file exists on disk,
  * the row has non-empty ``version``, ``url``, ``rationale``,
  * the row's ``sha256`` equals the sha256 of the file's bytes.

A violation is any of: a static file with no row; a row whose recorded
sha256 differs from the file on disk (the swap/tamper case); a row
missing a required provenance field; a malformed or absent manifest in a
static dir that contains assets.

Intentionally NOT caught (precision over recall — a detector agents
distrust is worse than no detector):

  * **Whether the recorded ``url`` actually serves those bytes.** F89
    proves the manifest matches the DISK, not that the disk matches
    UPSTREAM — verifying the URL would need a network fetch the gate
    cannot make hermetically. The rationale + URL are review surface;
    the sha pins the bytes locally.
  * **Whether ``version`` is the real upstream version.** A lie in the
    version string (``"1.9.12"`` recorded for 2.0 bytes) is a review
    miss, not a structural one — the rule has no oracle for the true
    version. The sha still pins exactly-these-bytes.
  * **Sub-resource-integrity (SRI) attributes in the templates.** Whether
    ``<script src=...>`` carries an ``integrity=`` hash is a separate
    concern (a browser-side guard); F89 is the build-side byte pin.
  * **Manifest rows for files that no longer exist.** A stale row whose
    file was deleted is harmless noise, not a served-bytes risk; it is
    not flagged (the file-side sweep is the contract).

This rule has no per-file baseline that grandfathers offenders: the
manifest is created WITH the current assets (their real shas computed),
so the tree is green at landing. A missing row or a sha mismatch is a
hard, binary failure — there is nothing to ratchet down.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _arch_lib import REPO_ROOT, gate

# The manifest filename that lives beside the assets in each static dir.
MANIFEST_NAME = "ASSETS.lock"

# Provenance fields every row must carry, non-empty.
_REQUIRED_FIELDS = ("version", "url", "rationale")

REMEDIATION = """F89: a vendored browser-asset under a web/static/ tree is not pinned
in its ASSETS.lock manifest, or its on-disk bytes no longer match the
pinned sha256. This is the supply-chain class where a swapped or
outdated htmx.min.js / pico.css ships untraced — every test stays green
because the wizard still renders, but the bytes aren't the reviewed
ones.

fix: per the failing line printed above —
  * a static file with NO manifest row: add a row to the ASSETS.lock in
    that static dir with the file's upstream version, the sha256 of its
    bytes (`shasum -a 256 <file>`), the upstream URL, and a one-line
    rationale (why this asset, why this pin). First-party files get
    `"vendored": false` but still need a row.
  * a sha256 MISMATCH (a swap/tamper, or a deliberate upgrade): if the
    new bytes are intended, recompute the sha (`shasum -a 256 <file>`),
    update the row's `sha256` + `version`, and record WHY in the
    rationale. If you did not change the file, the bytes were tampered —
    restore the reviewed asset.
  * a row missing version / url / rationale: fill the provenance field.
next: re-run `python3 scripts/checks/check_f89_vendored_asset_manifest.py`
to confirm the gate goes green.
run: bash scripts/safe-commit.sh "chore(setup): pin <asset> in the web-static manifest (#499 phase 3)"

Pass example: kairix/platform/setup/web/static/ASSETS.lock
  {
    "assets": {
      "htmx.min.js": {
        "vendored": true,
        "version": "1.9.12",
        "sha256": "<64 hex chars from `shasum -a 256 htmx.min.js`>",
        "url": "https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js",
        "rationale": "HTMX drives every async wizard interaction; pinned to the tested 1.9.x line."
      }
    }
  }

Forbidden example: dropping a newer kairix/platform/setup/web/static/htmx.min.js
into the tree without touching ASSETS.lock — the file's sha256 no longer
matches the pinned row, so F89 fails (exactly the swap the rule exists to
catch). Or adding a static asset with no row at all — it ships
un-provenanced."""


def _static_dirs(repo_root: Path) -> list[Path]:
    """Every ``web/static`` directory under ``kairix/**``."""
    kairix = repo_root / "kairix"
    if not kairix.exists():
        return []
    return sorted(p for p in kairix.rglob("static") if p.is_dir() and p.parent.name == "web")


def _asset_files(static_dir: Path) -> list[Path]:
    """Regular served files in ``static_dir`` (recursively), minus the
    manifest itself and editor/VCS noise."""
    out: list[Path] = []
    for path in sorted(static_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name == MANIFEST_NAME:
            continue
        if "__pycache__" in path.parts or path.name.startswith("."):
            continue
        out.append(path)
    return out


def _sha256(path: Path) -> str:
    """Hex sha256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_manifest(static_dir: Path) -> tuple[dict[str, dict[str, object]] | None, str | None]:
    """Parse ``static_dir/ASSETS.lock``.

    Returns ``(assets_map, error)``: ``assets_map`` is the
    ``{filename: row}`` mapping, or ``None`` with an ``error`` string
    when the manifest is absent or malformed.
    """
    manifest_path = static_dir / MANIFEST_NAME
    if not manifest_path.exists():
        return None, f"no {MANIFEST_NAME} manifest beside the served assets"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        return None, f"{MANIFEST_NAME} is not valid JSON ({exc})"
    assets = data.get("assets") if isinstance(data, dict) else None
    if not isinstance(assets, dict):
        return None, f"{MANIFEST_NAME} has no top-level 'assets' object"
    return assets, None


def _row_violation(row: object, on_disk_sha: str) -> str | None:
    """The reason a manifest ``row`` fails for a file whose bytes hash to
    ``on_disk_sha`` — or ``None`` if the row is satisfied."""
    if not isinstance(row, dict):
        return "manifest row is not an object"
    for field in _REQUIRED_FIELDS:
        value = row.get(field)
        if not isinstance(value, str) or not value.strip():
            return f"manifest row missing non-empty '{field}'"
    recorded = row.get("sha256")
    if not isinstance(recorded, str) or not recorded.strip():
        return "manifest row missing 'sha256'"
    if recorded.strip().lower() != on_disk_sha:
        pinned = recorded.strip()[:12]
        return f"sha256 mismatch — manifest pins {pinned}…, file hashes to {on_disk_sha[:12]}… (swap/tamper)"
    return None


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Sweep every web/static tree; print per-asset detail; return the
    violating files (repo-relative — the asset itself for an unpinned /
    mismatched file, the manifest for a malformed/absent manifest)."""
    violations: set[Path] = set()
    for static_dir in _static_dirs(repo_root):
        assets = _asset_files(static_dir)
        manifest_map, manifest_error = _load_manifest(static_dir)
        if manifest_map is None:
            if not assets:
                continue  # an empty static dir needs no manifest
            rel = (static_dir / MANIFEST_NAME).relative_to(repo_root)
            violations.add(rel)
            print(f"  [f89] {rel}: {manifest_error}")
            continue
        for asset in assets:
            rel = asset.relative_to(repo_root)
            row = manifest_map.get(asset.name)
            if row is None:
                violations.add(rel)
                print(f"  [f89] {rel}: no manifest row in {MANIFEST_NAME} — un-pinned served asset")
                continue
            reason = _row_violation(row, _sha256(asset))
            if reason is not None:
                violations.add(rel)
                print(f"  [f89] {rel}: {reason}")
    return violations


def main() -> int:
    return gate("f89", collect_violations(), REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())
