.DEFAULT_GOAL := help
.PHONY: help install fmt lint types test test-unit test-integration check migrate downgrade revision run repair ship clean

PYTHON  := .venv/bin/python
VENVBIN := .venv/bin

# Point the integration suite at a database. Override on the command line or
# export it in your shell; without it those tests skip rather than fail.
M4D_TEST_DATABASE_URL ?= postgresql+asyncpg://postgres@127.0.0.1:5432/m4d_test
export M4D_TEST_DATABASE_URL

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Create the virtualenv and install everything
	uv venv --python 3.12 .venv
	uv pip install --python $(PYTHON) -e ".[dev]"

fmt: ## Format the code
	$(VENVBIN)/ruff format .
	$(VENVBIN)/ruff check --fix .

lint: ## Lint without modifying anything
	$(VENVBIN)/ruff check .
	$(VENVBIN)/ruff format --check .

types: ## Type-check under mypy --strict
	$(VENVBIN)/mypy

test: ## Run the whole suite
	$(PYTHON) -m pytest

test-unit: ## Run only the fast, I/O-free tests
	$(PYTHON) -m pytest tests/unit

test-integration: ## Run only the tests that need PostgreSQL
	$(PYTHON) -m pytest tests/integration

check: lint types test ## Everything CI runs

migrate: ## Apply migrations up to head
	$(VENVBIN)/alembic upgrade head

downgrade: ## Revert the most recent migration
	$(VENVBIN)/alembic downgrade -1

revision: ## Autogenerate a revision: make revision m="add widgets"
	@test -n "$(m)" || (echo 'Usage: make revision m="describe the change"'; exit 1)
	$(VENVBIN)/alembic revision --autogenerate -m "$(m)"

run: ## Run the API with reload
	$(VENVBIN)/m4d serve --reload

# Needs ANTHROPIC_API_KEY, or a profile from `ant auth login`. Rewrites source
# files in place: run it on a clean working tree so `git diff` shows the patch.
repair: ## Drive `make check` to green with Claude: make repair [a="--dry-run"]
	$(PYTHON) -m tools.repair $(a)

# Reads factory.toml. `preflight` and `plan` are safe anywhere; `run` needs the
# platform toolchain and is normally the CI runner's job, not a laptop's.
ship: ## Drive an app to a store: make ship a="preflight" | a="plan --platform apple"
	$(PYTHON) -m tools.factory $(or $(a),preflight)

clean: ## Remove caches and build artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml dist build
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +
