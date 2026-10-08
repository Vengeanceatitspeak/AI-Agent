# JARVIS Makefile — CI-style commands
# Usage: make check (runs all lint, type checks, and tests)

.PHONY: help install dev lint format typecheck test test-unit test-integration check clean doctor

PYTHON ?= python3
UV ?= uv

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install core dependencies
	$(UV) pip install -e "."

dev: ## Install with all dev dependencies
	$(UV) pip install -e ".[dev,llm-all]"

lint: ## Run ruff linter
	$(UV) run ruff check src/ tests/

format: ## Format code with ruff
	$(UV) run ruff format src/ tests/

typecheck: ## Run mypy type checker
	$(UV) run mypy src/jarvis/core/ src/jarvis/policy/ src/jarvis/mcp_client/ src/jarvis/config.py

test: ## Run all tests
	$(UV) run pytest tests/ -v

test-unit: ## Run unit tests only
	$(UV) run pytest tests/unit/ -v -m unit

test-integration: ## Run integration tests only
	$(UV) run pytest tests/integration/ -v -m integration

check: lint typecheck test ## Run all checks (lint + types + tests)
	@echo "\n✅ All checks passed!"

clean: ## Clean build artifacts
	rm -rf dist/ build/ *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

doctor: ## Run environment diagnostics
	$(UV) run jarvis doctor
