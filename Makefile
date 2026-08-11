# Workspace-root convenience targets for the screening-room monorepo.
#
# These wrap the everyday commands documented in README.md / CLAUDE.md. The
# quality gates (lint, typecheck, security, test) are intentionally NOT mirrored
# here — they live in `.github/workflows/ci.yml` as the single source of truth.
# Run them with the `uv run ...` commands in the README to stay in lockstep with CI.

# External, standalone scraper repo (a sibling of this monorepo by default).
# Mirrors the ALLOCINE_DIR env var the dashboard uses to locate it.
ALLOCINE_DIR ?= ../Allocine-Showtimes-Scraping

# Extra flags forwarded to orchestrate.py — it is staleness-aware, so without a
# passthrough `make orchestrate` can never force a re-run:
#   make orchestrate ARGS="--force"
#   make orchestrate ARGS="--days 7 --reset"
ARGS ?=

.DEFAULT_GOAL := help
.PHONY: help install hooks run orchestrate update

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Sync the whole workspace into one shared .venv
	uv sync --all-packages

# Installs the hooks; it does not run the gates, so the "CI owns the gates" rule
# above still holds. The --hook-type flags are load-bearing: prek does not read
# `default_install_hook_types`, so a bare `prek install` wires up pre-commit only
# and the commit-msg + pre-push hooks would silently never fire.
hooks: ## Install the git hooks (prek) — run once per clone
	uv tool install --quiet prek
	uv tool run prek install \
		--hook-type pre-commit --hook-type commit-msg --hook-type pre-push

run: ## Launch the Streamlit dashboard
	uv run --no-sync --directory cinema_dashboard streamlit run app.py

orchestrate: ## Refresh stale data in parallel (ARGS="--force" to re-run regardless)
	uv run --no-sync --directory cinema_dashboard python orchestrate.py $(ARGS)

update: ## Pull this monorepo + the external Allocine scraper
	git pull
	git -C $(ALLOCINE_DIR) pull
