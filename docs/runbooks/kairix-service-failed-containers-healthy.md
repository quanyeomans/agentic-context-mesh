# Runbook — `kairix.service` failed in systemd but containers are healthy

You landed here because `systemctl status kairix.service` shows `failed` (or `activating (auto-restart)`) while `curl http://localhost:8080/health` returns `{"ok":true,"status":"live"}` and `docker compose ps` shows the kairix containers as `Up (healthy)`.

This is a known disconnect: the docker containers were started by a previous successful run (or by a manual `docker compose up -d`) and continue to serve traffic under dockerd's own restart policy. systemd's view is stale. Search and ingest still work; the systemd state is lying.

## Diagnose

Run this first. The output names the layer that is broken.

```bash
sudo journalctl -xeu kairix.service --no-pager | tail -30
```

Match the headline error against the table.

| Headline in journal | Layer | Fix |
|---|---|---|
| `Failed to set up mount namespacing: /var/lib/kairix: No such file or directory` and `code=exited, status=226/NAMESPACE` | systemd namespace setup | [§1](#1-readwritepaths-target-missing) |
| `Unknown key name 'StartLimitIntervalSec' in section 'Service', ignoring` (any daemon-reload) | unit-file section misplacement | [§2](#2-startlimit-keys-in-wrong-section) |
| `(kairix-preflight) FAIL: required env keys missing after merging .env + secrets` while the merged file does have the keys | preflight invoked as non-root | [§3](#3-execstartpre-not-running-as-root) |
| `permissions-preflight.sh: No such file or directory` | preflight script not copied to runtime path | [§4](#4-preflight-not-installed) |

Pick the matching section, apply the fix, then go to [§5 Verify](#5-verify).

## 1. `ReadWritePaths=` target missing

The shipped unit declares `ReadWritePaths=/opt/kairix /var/lib/kairix /run/secrets`. If any of those paths does not exist, systemd refuses to set up the mount namespace and the unit exits 226 before `ExecStartPre=` runs.

fix: create the missing directory with the right ownership, then start the unit.

next: run `systemctl status kairix.service` and confirm `Active: active (exited)`.

```bash
sudo install -d -m 0750 -o kairix -g kairix /var/lib/kairix
sudo systemctl reset-failed kairix.service
sudo systemctl start kairix.service
```

Updated installs (v2026.6.6 and later) ship the unit with `ReadWritePaths=-/opt/kairix -/var/lib/kairix -/run/secrets` (`-` prefix = path is optional). If you are running an older unit, either replace the unit file from `scripts/install/kairix.service.example` or pin the directory explicitly as above.

## 2. `StartLimit*` keys in wrong section

`StartLimitIntervalSec=` and `StartLimitBurst=` belong in `[Unit]`, not `[Service]`. systemd silently ignores them under `[Service]` and the unit has no startup-rate limit at all — every restart counts as a fresh attempt and the unit can crash-loop forever on a host with a broken preflight.

fix: replace the unit file with the canonical example, then daemon-reload.

next: run `sudo systemctl daemon-reload` and confirm no `Unknown key name` warning appears in `journalctl -xe`.

```bash
sudo install -m 0644 scripts/install/kairix.service.example /etc/systemd/system/kairix.service
sudo systemctl daemon-reload
sudo systemctl reset-failed kairix.service
sudo systemctl start kairix.service
```

## 3. `ExecStartPre=` not running as root

The preflight script's self-heal section (`chown` / `chmod` of `.env`) and its `/run/secrets/kairix.env` read both need root. If the unit declares `User=kairix` and `ExecStartPre=` is *not* prefixed with `+`, systemd runs the preflight as the kairix user and the script's checks fail on hosts where `/run/secrets/kairix.env` is `root:root` mode 0640.

fix: prefix `ExecStartPre=` with `+` in the unit file.

next: `daemon-reload` and start the unit.

```bash
sudo sed -i 's|^ExecStartPre=/opt/kairix/bin/permissions-preflight.sh|ExecStartPre=+/opt/kairix/bin/permissions-preflight.sh|' /etc/systemd/system/kairix.service
sudo systemctl daemon-reload
sudo systemctl reset-failed kairix.service
sudo systemctl start kairix.service
```

Or replace the whole file with the canonical example, which carries the `+` prefix:

```bash
sudo install -m 0644 scripts/install/kairix.service.example /etc/systemd/system/kairix.service
```

## 4. Preflight not installed

The unit references `/opt/kairix/bin/permissions-preflight.sh`, but the install step that copies the script there was skipped or wiped (e.g. by a half-finished `openclaw-upgrade.sh`).

fix: install the script from the source repo (do not symlink — `ProtectSystem=strict` blocks symlinks that point outside the `ReadWritePaths=` set, and you will land back in §1 with a 226).

next: confirm the script runs cleanly under root.

```bash
sudo install -m 0750 -o kairix -g kairix \
    /data/development/kairix/scripts/install/permissions-preflight.sh \
    /opt/kairix/bin/permissions-preflight.sh

sudo /opt/kairix/bin/permissions-preflight.sh
# expect: [kairix-preflight] ok — all host-side preflight checks pass

sudo systemctl reset-failed kairix.service
sudo systemctl start kairix.service
```

## 5. Verify

Confirm all four signals are green. If any fails, return to the diagnose table.

```bash
systemctl status kairix.service --no-pager | head -8
# expect: Active: active (exited), ExecStartPre status=0/SUCCESS, ExecStart status=0/SUCCESS

curl -fsS http://localhost:8080/health
# expect: {"ok":true,"status":"live"}

systemctl --failed --no-pager
# expect: 0 loaded units listed.

sudo journalctl --since '5 minutes ago' --no-pager | grep -iE 'kairix.*Unknown key|kairix.*NAMESPACE|kairix-preflight FAIL'
# expect: no output
```

## Why this disconnect happens

Docker manages container lifecycles itself (`restart: unless-stopped` in the compose file). A `docker compose up -d` from a successful past run leaves the containers running under dockerd even when the systemd unit that started them is later wedged. systemd's `Type=oneshot` semantics mean the unit's state reflects only the `ExecStartPre=` + `ExecStart=` exit codes from the *last* attempt, not the running container fleet.

Once you have fixed the failing layer, systemd's state catches up to docker's reality at the next clean start.

## Escalation

If §1-4 all apply, or the diagnose-table headline is not listed, attach to the issue:

- `sudo systemctl status kairix.service --no-pager`
- `sudo journalctl -xeu kairix.service --no-pager | tail -100`
- `cat /etc/systemd/system/kairix.service`
- `ls -la /opt/kairix/bin/ /var/lib/kairix/ /run/secrets/kairix.env`
- `sudo /opt/kairix/bin/permissions-preflight.sh 2>&1`
- Output of `docker compose ps` in `/opt/kairix/app/`.

File at <https://github.com/three-cubes/kairix/issues> with title `kairix-service-failed-containers-healthy: <headline>`.
