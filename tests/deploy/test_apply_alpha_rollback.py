"""Behavioural tests for the auto-rollback in scripts/deploy/apply-alpha.sh.

The script is driven via subprocess with a fake `docker` (and `systemctl`) on
PATH, so we exercise the real shell — including the EXIT-trap exit-code dispatch
that is easy to get subtly wrong under `set -e` (the trap must capture the
rollback return with `rb=0; rollback_to_prev "$@" || rb=$?` and forward `$@`).

Exit-code contract:
  0  = clean success (no rollback)
  10 = deploy failed, rolled back to the prior tag + verified (prod restored)
  11 = deploy failed, rollback impossible (no distinct prior tag)
  12 = deploy failed, rollback also failed (prod down / page a human)
  (a step-(e) regression is NOT rolled back: it exits with the raw failure code)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit  # fast, hermetic (all externals faked) — F8 category

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy" / "apply-alpha.sh"
PRIOR = "2026.6.25a1"  # the known-good tag already running before the deploy
NEW = "2026.6.28a3"  # the tag we are deploying

FAKE_DOCKER = r"""#!/usr/bin/env python3
import os, sys
argv = sys.argv[1:]
tag = os.environ.get("KAIRIX_IMAGE_TAG", "")
log = os.environ.get("FAKE_DOCKER_LOG")
if log:
    open(log, "a").write("TAG=%s :: %s\n" % (tag, " ".join(argv)))
state = os.environ.get("FAKE_STATE_FILE")

def fset(name):
    return set(filter(None, os.environ.get(name, "").split(",")))

def cur_digest():
    if state and os.path.exists(state):
        return open(state).read().strip()
    return os.environ.get("FAKE_INITIAL_DIGEST", "")

sub = argv[0] if argv else ""
if sub == "inspect":
    fmt = argv[2] if len(argv) > 2 else ""
    if "Config" in fmt:           # {{ index .Config.Image }} -> image reference
        sys.stdout.write(os.environ.get("FAKE_LIVE_REF", ""))
    else:                          # {{ .Image }} -> resolved digest of running ctr
        sys.stdout.write(cur_digest())
    sys.exit(0)
if sub == "compose":
    if "pull" in argv:
        sys.exit(1 if tag in fset("FAKE_PULL_FAIL_TAGS") else 0)
    if "up" in argv:
        if tag in fset("FAKE_UP_FAIL_TAGS"):
            sys.exit(1)
        if state:
            open(state, "w").write("d-" + tag)   # successful up resolves the tag
        sys.exit(0)
    sys.exit(0)
if sub == "exec":
    joined = " ".join(argv)
    if "onboard" in joined:
        d = cur_digest()
        curtag = d[2:] if d.startswith("d-") else ""
        ok = "false" if curtag in fset("FAKE_ONBOARD_FAIL_TAGS") else "true"
        sys.stdout.write('{"fully_passed": %s, "passed": 18, "total": 18}' % ok)
        sys.exit(0)
    if "benchmark" in joined:
        sys.stdout.write("Weighted total: %s\n" % os.environ.get("FAKE_WEIGHTED", "0.810"))
        sys.exit(0)
    sys.exit(0)
sys.exit(0)
"""


def _run(tmp_path: Path, tag: str, *, prior=PRIOR, initial_digest=None, **fake_env):
    """Run apply-alpha.sh against a fake docker. Returns (proc, docker_log, env_text)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text(FAKE_DOCKER)
    (bindir / "docker").chmod(0o755)
    (bindir / "systemctl").write_text("#!/bin/sh\nexit 0\n")
    (bindir / "systemctl").chmod(0o755)

    compose = tmp_path / "compose"
    compose.mkdir()
    (compose / "docker-compose.yml").write_text("services: {}\n")
    if prior is not None:
        (compose / ".env").write_text(f"KAIRIX_IMAGE_TAG={prior}\n")

    log = tmp_path / "docker.log"
    state = tmp_path / "state"  # intentionally absent until the first `up`

    env = dict(os.environ)
    env["PATH"] = "{}:{}".format(bindir, env["PATH"])
    env["KAIRIX_COMPOSE_DIR"] = str(compose)
    env["FAKE_DOCKER_LOG"] = str(log)
    env["FAKE_STATE_FILE"] = str(state)
    # The running container's digest before the deploy. Defaults to the prior
    # tag's digest so a clean rollback verifies; override to force a mismatch.
    env["FAKE_INITIAL_DIGEST"] = initial_digest if initial_digest is not None else (f"d-{prior}")
    env["FAKE_LIVE_REF"] = "ghcr.io/three-cubes/kairix:%s" % (prior or "")
    for k, v in fake_env.items():
        env[k] = str(v)

    proc = subprocess.run(
        ["sh", str(SCRIPT), tag],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    env_text = (compose / ".env").read_text() if (compose / ".env").exists() else ""
    return proc, (log.read_text() if log.exists() else ""), env_text


def test_happy_path_no_rollback(tmp_path):
    proc, dlog, env_text = _run(tmp_path, NEW)
    assert proc.returncode == 0, proc.stderr
    assert f"KAIRIX_IMAGE_TAG={NEW}" in env_text
    assert "rolling back" not in proc.stderr.lower()
    # exactly one `up`, for the new tag — no rollback up of the prior tag
    ups = [ln for ln in dlog.splitlines() if " up " in ln]
    assert len(ups) == 1 and (f"TAG={NEW}") in ups[0]


def test_up_fail_rolls_back_and_exits_10(tmp_path):
    proc, dlog, env_text = _run(tmp_path, NEW, FAKE_UP_FAIL_TAGS=NEW)
    assert proc.returncode == 10, proc.stderr
    assert f"KAIRIX_IMAGE_TAG={PRIOR}" in env_text  # .env restored
    assert "SUCCEEDED" in proc.stderr
    # the rollback `up` ran for the prior tag, forwarding the -f file list
    rb_ups = [ln for ln in dlog.splitlines() if " up " in ln and (f"TAG={PRIOR}") in ln]
    assert rb_ups and "-f docker-compose.yml" in rb_ups[0]


def test_onboard_fail_rolls_back_and_exits_10(tmp_path):
    proc, _dlog, env_text = _run(tmp_path, NEW, FAKE_ONBOARD_FAIL_TAGS=NEW)
    assert proc.returncode == 10, proc.stderr
    assert f"KAIRIX_IMAGE_TAG={PRIOR}" in env_text


def test_offline_pull_fail_rolls_back_without_pulling(tmp_path):
    proc, dlog, _env = _run(tmp_path, NEW, FAKE_PULL_FAIL_TAGS=NEW)
    assert proc.returncode == 10, proc.stderr
    # the ONLY pull was the (failed) new-tag pull; the rollback never pulls
    pulls = [ln for ln in dlog.splitlines() if " pull " in ln]
    assert len(pulls) == 1 and (f"TAG={NEW}") in pulls[0]
    assert any(" up " in ln and (f"TAG={PRIOR}") in ln for ln in dlog.splitlines())


def test_regression_does_not_roll_back(tmp_path):
    # step (e) regression: a HEALTHY build scoring below baseline must NOT be
    # rolled back — exit with the raw failure code, leave the new build serving.
    proc, dlog, env_text = _run(tmp_path, NEW, FAKE_WEIGHTED="0.500")
    assert proc.returncode == 1, proc.stderr
    assert f"KAIRIX_IMAGE_TAG={NEW}" in env_text  # new build stays
    assert "rolling back" not in proc.stderr.lower()
    assert sum(1 for ln in dlog.splitlines() if " up " in ln) == 1  # no rollback up


def test_emits_reflib_marker_on_pass(tmp_path):
    # The migrated deploy plane (PLA-250) reads the reflib eval result off the
    # box-side stdout to post the `vm-reflib-regression` commit status. apply-alpha
    # must emit a single machine-readable marker carrying verdict + weighted +
    # baseline + tolerance on the PASS path (default FAKE_WEIGHTED=0.810 > the
    # 0.808 baseline minus 0.05 tolerance -> pass).
    proc, _dlog, _env = _run(tmp_path, NEW)
    assert proc.returncode == 0, proc.stderr
    assert "KAIRIX_REFLIB verdict=pass weighted=0.810 baseline=0.808 tolerance=0.05" in proc.stdout, proc.stdout


def test_emits_reflib_marker_on_regression(tmp_path):
    # On a regression the marker must STILL be emitted (verdict=regress) so the
    # workflow can post state=failure with the achieved weighted score, not just
    # an absent status. The marker goes to stdout; the FAIL line stays on stderr.
    proc, _dlog, _env = _run(tmp_path, NEW, FAKE_WEIGHTED="0.500")
    assert proc.returncode == 1, proc.stderr
    assert "KAIRIX_REFLIB verdict=regress weighted=0.500 baseline=0.808 tolerance=0.05" in proc.stdout, proc.stdout


def test_same_tag_redeploy_rollback_impossible_exits_11(tmp_path):
    proc, _dlog, _env = _run(tmp_path, PRIOR, FAKE_UP_FAIL_TAGS=PRIOR)
    assert proc.returncode == 11, proc.stderr
    assert "MANUAL INTERVENTION" in proc.stderr


def test_fresh_host_no_prior_rollback_impossible_exits_11(tmp_path):
    proc, _dlog, _env = _run(
        tmp_path,
        NEW,
        prior=None,
        initial_digest="",
        FAKE_LIVE_REF="",
        FAKE_UP_FAIL_TAGS=NEW,
    )
    assert proc.returncode == 11, proc.stderr


def test_rollback_also_fails_exits_12(tmp_path):
    proc, _dlog, _env = _run(tmp_path, NEW, FAKE_UP_FAIL_TAGS=f"{NEW},{PRIOR}")
    assert proc.returncode == 12, proc.stderr
    assert "PAGE A HUMAN" in proc.stderr


def test_rollback_digest_mismatch_exits_12(tmp_path):
    # rollback `up` succeeds but the prior tag resolved to a different digest
    # than the one running before the deploy -> cannot confirm known-good binary.
    proc, _dlog, _env = _run(
        tmp_path,
        NEW,
        initial_digest="d-MOVED",
        FAKE_UP_FAIL_TAGS=NEW,
    )
    assert proc.returncode == 12, proc.stderr
    assert "different digest" in proc.stderr
