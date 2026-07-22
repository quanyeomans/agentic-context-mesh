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
