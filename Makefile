.DEFAULT_GOAL := help
S ?=

.PHONY: help install lint fmt test check lab-up lab-down scenarios-list scenario-up scenario-down

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

install: ## Install dependencies with uv
	uv sync

lint: ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Auto-format and fix lint issues
	uv run ruff check --fix .
	uv run ruff format .

test: ## Run unit tests
	uv run pytest

check: lint test ## Lint, test and validate scenarios
	uv run k8s-sre-agent scenarios validate

lab-up: ## Create the kind cluster, demo app and monitoring stack
	./scripts/lab.sh up

lab-down: ## Destroy the kind cluster
	./scripts/lab.sh down

scenarios-list: ## List failure scenarios
	uv run k8s-sre-agent scenarios list

scenario-up: ## Inject a scenario: make scenario-up S=oom-killed
	./scripts/scenario.sh up $(S)

scenario-down: ## Remove a scenario: make scenario-down S=oom-killed
	./scripts/scenario.sh down $(S)
