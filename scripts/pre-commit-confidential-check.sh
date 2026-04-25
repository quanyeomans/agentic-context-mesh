#!/bin/bash
# Pre-commit hook: block commits containing confidential or TC-specific data.
#
# Install: cp scripts/pre-commit-confidential-check.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
# Or add to your existing pre-commit chain.
#
# What it checks:
#   - Personal home directory paths (/Users/danielmcmahon, /home/openclaw)
#   - TC-specific Azure resource names (kv-tc-agents, cog-tc-agents, rg-agents-core)
#   - Internal hostnames (ssh.threecubes.ai, vm-openclaw)
#   - Client names that shouldn't be in a public repo
#   - API keys or tokens that look real

set -e

STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM | grep -E '\.(py|md|yaml|yml|sh|json|toml)$' || true)

if [ -z "$STAGED_FILES" ]; then
    exit 0
fi

BLOCKED_PATTERNS=(
    # Personal paths
    "/Users/danielmcmahon"
    "/home/openclaw"

    # TC-specific Azure resources
    "kv-tc-agents"
    "cog-tc-agents"
    "rg-agents-core"
    "RG-AGENTS-CORE"

    # Internal hostnames
    "ssh\.threecubes\.ai"
    "vm-openclaw"

    # Client names (real companies associated with TC work — use generic names in public repo)
    # Note: "Avanade" removed from this list since it was replaced with "Acme Partners"

    # Real API keys (basic pattern — detect-secrets handles this more thoroughly)
    "sk-[a-zA-Z0-9]{20,}"
    "AKIA[A-Z0-9]{16}"
)

FAILURES=0

for pattern in "${BLOCKED_PATTERNS[@]}"; do
    MATCHES=$(echo "$STAGED_FILES" | xargs grep -lnE "$pattern" 2>/dev/null || true)
    if [ -n "$MATCHES" ]; then
        echo "BLOCKED: confidential pattern '$pattern' found in staged files:"
        echo "$MATCHES" | while read -r file; do
            echo "  $file:"
            grep -nE "$pattern" "$file" | head -3 | sed 's/^/    /'
        done
        FAILURES=$((FAILURES + 1))
    fi
done

if [ "$FAILURES" -gt 0 ]; then
    echo ""
    echo "Commit blocked: $FAILURES confidential pattern(s) found in staged files."
    echo "Move internal data to the Obsidian vault or tc-agent-zone repo."
    echo "To bypass (emergencies only): git commit --no-verify"
    exit 1
fi
