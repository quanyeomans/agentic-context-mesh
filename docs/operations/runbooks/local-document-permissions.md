# Runbook — Local document permission drift

**Symptom:** scans, summaries, or embed cycles report `permission denied` for files under the configured document root. Search may miss otherwise valid agent-generated documents.

## What is happening

Multiple agents and services can create or modify files under the same document tree with different users, groups, or umasks. Kairix should not mutate those files while scanning. It now skips unreadable files, records diagnostics, and continues with the readable corpus.

## Diagnose

```bash
kairix embed
kairix worker preflight --json
```

Check logs for `db.scanner: cannot read` or `generate_summaries: cannot read`. Each line includes a `fix:` hint and the unreadable path.

On the host, inspect one failing path:

```bash
ls -l "<path>"
namei -l "<path>"
id kairix
```

## Repair

For a shared local document tree, prefer group-readable files and directories:

```bash
sudo chgrp -R <kairix-readable-group> "<document-root>"
sudo find "<document-root>" -type d -exec chmod 2750 {} +
sudo find "<document-root>" -type f -exec chmod 0640 {} +
sudo usermod -aG <kairix-readable-group> kairix
```

Restart the worker after changing group membership:

```bash
sudo systemctl restart kairix.service
```

If the tree uses POSIX ACLs, check the ACL mask as well as the mode bits. A
file can show a group ACL while the mask makes it ineffective, for example
`group:openclaw:rwx #effective:---`. Repair the indexed root with an explicit
read-only group ACL and default ACL:

```bash
sudo setfacl -R -m g:<kairix-readable-group>:rX,m:rX "<document-root>"
sudo find "<document-root>" -type d -exec chmod g+s {} +
sudo find "<document-root>" -type d -exec setfacl -m d:g:<kairix-readable-group>:rX,d:m:rX {} +
```

On the Customer-Zero VM, `/data/documents` is intentionally mounted read-only
from `/data/obsidian-vault`. The `04-Agent-Knowledge` subtree is the exception:
it must be over-mounted read-write at
`/data/documents/04-Agent-Knowledge`, otherwise agent memory writes fall back to
`${KAIRIX_DATA_DIR}/agent-memory/...` and are no longer indexed as first-class
agent knowledge. Apply write ACLs only to that memory subtree or another
explicitly writable source:

```bash
sudo setfacl -R -m g:<kairix-readable-group>:rwX,m:rwX "<document-root>/04-Agent-Knowledge"
sudo find "<document-root>/04-Agent-Knowledge" -type d -exec setfacl -m d:g:<kairix-readable-group>:rwX,d:m:rwX {} +
```

The alpha deploy path writes this nested bind in
`docker-compose.kairix-vm-ops.yml`. It uses `KAIRIX_AGENT_MEMORY_HOST_PATH` when
set, otherwise derives the source from the active `/data/documents` mount and
falls back to `./documents/04-Agent-Knowledge` for fresh/manual layouts. Verify
the live mount with:

```bash
docker inspect app-kairix-1 --format '{{json .Mounts}}' \
  | jq -r '.[] | select(.Destination=="/data/documents/04-Agent-Knowledge")'
```

If a subtree is noisy, temporary, or not intended for retrieval, exclude or quarantine it in `kairix.config.yaml` rather than repeatedly repairing it.

## Verify

```bash
kairix embed
kairix search "known text from repaired document" --json
```

Expected:

- no repeated permission-denied diagnostics for the repaired path
- the repaired document appears in search after the embed cycle

## Prevent recurrence

- Run all document-writing agents with a shared primary group where possible.
- Set agent workspace umasks to create group-readable files.
- Keep temporary agent workspaces outside the indexed document root, or exclude them with a retention policy.
- Do not store secrets in the indexed document tree.
