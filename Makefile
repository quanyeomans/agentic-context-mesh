.PHONY: lint format check test test-unit test-bdd test-contract test-integration type-check security commit clean

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

# Security
security:
	detect-secrets scan --baseline .secrets.baseline
	python3 -m bandit -r kairix/ -ll --quiet
	bash scripts/pre-commit-confidential-check.sh

# Full pre-commit gate
check: lint test-all security

# Gated commit — use: make commit MSG="your message"
commit:
	bash scripts/safe-commit.sh "$(MSG)"

# Clean build artifacts
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache htmlcov coverage.xml
