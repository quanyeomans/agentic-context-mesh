# Kairix — Secrets Fetch Failure

**Symptom:** One or more of: Neo4j authentication fails, Azure OpenAI embedding calls fail with auth errors, `kairix onboard check` shows secrets tests failing, `/run/secrets/kairix.env` does not exist after VM boot.

---

## Quick Diagnosis

```bash
# Check if secrets file exists (destroyed on every reboot — must be recreated at boot)
ls -la /run/secrets/kairix.env
# Healthy: -rw-r----- 1 root kairix ... /run/secrets/kairix.env
# Broken: No such file or directory

# Check fetch service ran and succeeded
systemctl status kairix-fetch-secrets --no-pager

# Check fetch service logs
journalctl -u kairix-fetch-secrets -n 30 --no-pager
```

---

## Root Cause A — Fetch Service Failed to Start

The `kairix-fetch-secrets.service` is a `oneshot` systemd unit that runs at boot, fetches secrets from your secrets provider (e.g. Azure Key Vault), and writes them to `/run/secrets/kairix.env`.

**Common causes:**

| Cause | Symptom in journal | Fix |
|---|---|---|
| `/run/secrets/` directory permissions | `chown` error in logs | See Fix A below |
| Secrets CLI not on PATH | `az: command not found` or similar | See Fix B below |
| VM managed identity / service account RBAC not configured | `Authorization failed` | See Fix C below |
| Network not ready at boot time (race condition) | Timeout or connection refused | See Fix D below |
| Key vault name / endpoint misconfigured | `VaultNotFound` or `404` | Check `service.env` |

---

## Fix A — Directory Permissions

```bash
# Check current permissions
ls -la /run/secrets/

# /run/secrets/ must be root:kairix 750 (or root:root 755 + file is 640)
sudo mkdir -p /run/secrets
sudo chown root:kairix /run/secrets
sudo chmod 750 /run/secrets

# Restart the fetch service
sudo systemctl restart kairix-fetch-secrets
sleep 10

# Verify file created
ls -la /run/secrets/kairix.env
# Expected: -rw-r----- 1 root kairix
```

---

## Fix B — Secrets CLI Missing

```bash
# Check if the secrets CLI is available (example: Azure CLI)
which az || echo "az not found"

# If missing, check alternative paths
ls /opt/az/bin/az 2>/dev/null || ls /usr/bin/az 2>/dev/null

# If the CLI is at a non-standard path, update the fetch service ExecStart
cat /etc/systemd/system/kairix-fetch-secrets.service | grep ExecStart

# Alternatively, use the IMDS REST API directly (no az dependency):
# TOKEN=$(curl -sf -H "Metadata:true" \
#   "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://vault.azure.net" \
#   | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

---

## Fix C — RBAC Not Configured

The VM's system-assigned managed identity (or equivalent service account) needs the appropriate role to read secrets from your vault. For Azure Key Vault, the required role is `Key Vault Secrets User`.

```bash
# Check VM identity (Azure example)
az vm show --name $(hostname) --resource-group "your resource group" \
  --query identity --output json

# Verify RBAC assignment
az role assignment list \
  --scope "/subscriptions/$(az account show --query id -o tsv)/resourcegroups/your resource group/providers/Microsoft.KeyVault/vaults/${KAIRIX_KV_NAME}" \
  --output table

# If RBAC is missing: assign it via Azure portal or az CLI with owner permissions
```

---

## Fix D — Network Race Condition at Boot

If the fetch service runs before network is ready:

```bash
# Check if service waits for network
cat /etc/systemd/system/kairix-fetch-secrets.service | grep -E "After|Wants"
# Should include: After=network-online.target

# If missing, update the service unit:
# 1. Edit /etc/systemd/system/kairix-fetch-secrets.service
# 2. Add: After=network-online.target
#         Wants=network-online.target
# 3. Reload and enable
sudo systemctl daemon-reload
sudo systemctl enable kairix-fetch-secrets
sudo systemctl restart kairix-fetch-secrets
```

---

## Manual Fix — Force Secrets Fetch Now

```bash
# Trigger the fetch service manually (as root)
sudo systemctl start kairix-fetch-secrets

# If the service itself is broken, fetch manually (Azure Key Vault example):
VAULT="${KAIRIX_KV_NAME}"
OUT="/run/secrets/kairix.env"
sudo mkdir -p /run/secrets
sudo chown root:kairix /run/secrets && sudo chmod 750 /run/secrets

for secret in azure-openai-api-key azure-openai-endpoint kairix-neo4j-password; do
  value=$(az keyvault secret show --vault-name "$VAULT" --name "$secret" --query value -o tsv 2>/dev/null)
  if [[ -n "$value" ]]; then
    echo "$secret fetched OK"
  else
    echo "$secret FAILED"
  fi
done
# (Write to file manually if confirmed working — see your fetch-secrets.sh source)
```

---

## Verify Fix

```bash
# Secrets file exists with correct permissions
ls -la /run/secrets/kairix.env
# Expected: -rw-r----- 1 root kairix [timestamp] /run/secrets/kairix.env

# File has expected exports
wc -l /run/secrets/kairix.env
# Expected: 3 (or 4 with trailing newline)

# Search works (no auth errors)
kairix search "test"

# Onboard check passes secrets tests
kairix onboard check
```

---

## Related

- [runbook-emergency-recovery](runbook-emergency-recovery.md) — quick-reference for all failures
