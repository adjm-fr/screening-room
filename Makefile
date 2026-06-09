# Workspace-root convenience targets for the screening-room monorepo.
#
# These wrap the everyday commands documented in README.md / CLAUDE.md. The
# quality gates (lint, typecheck, security, test) are intentionally NOT mirrored
# here — they live in `.github/workflows/ci.yml` as the single source of truth.
# Run them with the `uv run ...` commands in the README to stay in lockstep with CI.

# External, standalone scraper repo (a sibling of this monorepo by default).
# Mirrors the ALLOCINE_DIR env var the dashboard uses to locate it.
ALLOCINE_DIR ?= ../Allocine-Showtimes-Scraping

.DEFAULT_GOAL := help
.PHONY: help install run orchestrate update

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Sync the whole workspace into one shared .venv
	uv sync --all-packages

run: ## Launch the Streamlit dashboard
	uv run --no-sync --directory cinema_dashboard streamlit run app.py

orchestrate: ## Refresh stale data (runs both scrapers in parallel)
	uv run --no-sync --directory cinema_dashboard python orchestrate.py

update: ## Pull this monorepo + the external Allocine scraper
	git pull
	git -C $(ALLOCINE_DIR) pull
