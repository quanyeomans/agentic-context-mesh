.PHONY: setup setup-dev setup-check fitness-config lint format check test test-unit test-bdd test-contract test-integration test-all test-ci type-check security commit clean \
        go-modules go-fmt go-vet go-lint go-test go-build go-check

# Developer-environment setup — wires the pre-commit hooks so first-commit
# enforcement matches CI from the very first push on a fresh clone.
# Closes the dormant-hook gap a fresh clone otherwise lands in.
#
# Pre-commit refuses to install when ``core.hooksPath`` is set, even when
# the value is the repo default (``.git/hooks``). We detect that exact
# case and unset it for the install, then leave the rest of the
# environment alone.
setup:
	@command -v pre-commit >/dev/null || { echo "pre-commit not installed — install via 'uv pip install pre-commit' or 'pipx install pre-commit'"; exit 1; }
	@hp=$$(git config --get core.hooksPath 2>/dev/null); \
	if [ -n "$$hp" ] && [ "$$hp" = ".git/hooks" -o "$$hp" = "$$(pwd)/.git/hooks" ]; then \
	    echo "setup: unsetting redundant core.hooksPath=$$hp (== default)"; \
	    git config --unset-all core.hooksPath; \
	elif [ -n "$$hp" ]; then \
	    echo "setup: core.hooksPath=$$hp is non-default; refusing to override. Unset manually if intentional, then re-run."; exit 1; \
	fi
	pre-commit install --install-hooks
	pre-commit install --hook-type commit-msg --install-hooks 2>/dev/null || true
	@echo "setup: pre-commit hooks installed. Run 'make check' to verify the gate locally."

# Full developer setup on a fresh clone: prerequisites check + editable
# install with CI's canonical extras set + pre-commit wiring. Idempotent —
# safe to re-run after pulling new deps.
setup-dev:
	bash scripts/dev/setup.sh

# Dry-run developer setup — reports what would happen without installing.
# Useful for "did I miss a prereq" troubleshooting.
setup-check:
	bash scripts/dev/setup.sh --check

# Fetch the F73 private-infra pattern set from org config (the org VARIABLE
# PRIVATE_INFRA_PATTERNS in the three-cubes org) and cache it to the
# gitignored .private-infra-patterns file the check falls back to. Requires
# the `gh` CLI authenticated as an org member. CI uses the matching org
# SECRET; this is the org-member-readable equivalent for local dev. To
# export into your shell instead of caching to a file:
#   eval "$(bash scripts/fetch-fitness-config.sh)"
fitness-config:
	bash scripts/fetch-fitness-config.sh --write

# Combined quality check — run all linting, formatting, and type checks
lint: lint-check format-check type-check

# Individual checks
lint-check:
	ruff check kairix/ tests/

format-check:
	ruff format --check kairix/ tests/

type-check:
	mypy kairix/ --strict

# Auto-fix formatting and imports
format:
	ruff check kairix/ tests/ --fix
	ruff format kairix/ tests/

# Tests by category
test: test-unit test-bdd test-contract

test-unit:
	pytest tests/ -m unit -x --timeout=30

test-bdd:
	pytest tests/ -m bdd -x --timeout=30

test-contract:
	pytest tests/ -m contract -x --timeout=30

test-integration:
	pytest tests/ -m integration -x --timeout=60

test-all:
	pytest tests/ -m "unit or bdd or contract" -x --timeout=30

# The LITERAL CI Stage 2 test tier (SGO-105). `make check` runs THIS, not the
# weaker `test-all`, so a green local `make check` means green CI Stage 2 by
# construction. Mirrors the two ci.yml (Stage 2 `unit-and-type`) steps exactly:
#   1. "Unit + BDD + Contract tests with coverage" — same selector, same
#      --cov/--cov-report/--cov-fail-under=80 repo floor.
#   2. "F7 — per-file coverage floor" — check_per_file_coverage.py against the
#      same coverage.xml.
# Run under `uv run` (the canonical launcher, matching scripts/safe-commit.sh —
# the proven local↔CI parity carrier) so the synced project env is used and the
# tc_fitness engine that F7 imports is importable. Do NOT weaken this back to a
# bare `pytest` subset — that reopens the coverage/F7 gap this target closes.
test-ci:
	uv run pytest tests/ \
	  -m "unit or bdd or contract" \
	  --cov=kairix \
	  --cov-report=xml:coverage.xml \
	  --cov-report=term-missing \
	  --cov-fail-under=80 \
	  --junitxml=results-unit.xml \
	  -v --tb=short
	uv run python3 scripts/checks/check_per_file_coverage.py coverage.xml

# Security
security:
	detect-secrets scan --baseline .secrets.baseline
	python3 -m bandit -r kairix/ -ll --quiet
	bash scripts/pre-commit-confidential-check.sh

# Full pre-commit gate — the LITERAL CI gate (SGO-105). `lint test-ci security`
# reproduces CI Stage 2 (ruff + format + mypy strict, then unit+bdd+contract
# with the 80% repo coverage floor AND the F7 per-file floor) so green-local
# ⇒ green-CI by construction. Previously `check` ran `test-all` (bare pytest,
# no --cov/--cov-fail-under/F7), so a green `make check` could still fail CI on
# coverage/F7; `test-ci` closes that gap.
check: lint test-ci security

# Gated commit — use: make commit MSG="your message"
commit:
	bash scripts/safe-commit.sh "$(MSG)"

# Clean build artifacts
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache htmlcov coverage.xml
	find services -type d -name dist -exec rm -rf {} + 2>/dev/null || true

# ── Go gates (per docs/architecture/go-integration-plan.md) ───────────────
# Each target discovers every services/<name>/go.mod and runs the gate
# per-module. Empty services/ → no-op (exit 0). Matches the CI workflow
# behaviour so local and CI are aligned.

go-modules:
	@find services -mindepth 2 -maxdepth 2 -name go.mod -exec dirname {} \; 2>/dev/null || true

go-fmt:
	@for m in $$($(MAKE) -s go-modules); do \
	  echo "→ gofmt -s -l $$m"; \
	  diff=$$(cd $$m && gofmt -s -l .); \
	  if [ -n "$$diff" ]; then echo "$$diff"; echo "fix: gofmt -s -w $$m"; exit 1; fi; \
	done

go-vet:
	@for m in $$($(MAKE) -s go-modules); do \
	  echo "→ go vet $$m"; \
	  (cd $$m && go vet ./...) || exit 1; \
	done

go-lint:
	@mods=$$($(MAKE) -s go-modules); \
	if [ -z "$$mods" ]; then echo "(no Go modules — go-lint no-op)"; exit 0; fi; \
	command -v golangci-lint >/dev/null || { echo "golangci-lint not installed — see https://golangci-lint.run/install"; exit 1; }; \
	for m in $$mods; do \
	  echo "→ golangci-lint run $$m"; \
	  (cd $$m && golangci-lint run --config=$(CURDIR)/.golangci.yml --timeout=5m) || exit 1; \
	done

go-test:
	@for m in $$($(MAKE) -s go-modules); do \
	  echo "→ go test -race -cover $$m"; \
	  (cd $$m && go test -race -covermode=atomic -coverprofile=coverage.out ./... && go tool cover -func=coverage.out | tail -1) || exit 1; \
	done

go-build:
	@for m in $$($(MAKE) -s go-modules); do \
	  echo "→ go build $$m"; \
	  (cd $$m && mkdir -p dist && for cmd in cmd/*/; do \
	    name=$$(basename $$cmd); \
	    go build -trimpath -ldflags "-s -w -X main.version=dev" -o "dist/$$name" "./$$cmd" || exit 1; \
	    echo "    built dist/$$name"; \
	  done) || exit 1; \
	done

# Aggregate — local equivalent of the CI go-quality.yml gate
go-check: go-fmt go-vet go-lint go-test go-build
