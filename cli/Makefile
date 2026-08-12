# ==============================================================================
# Aitana CLI — Makefile
# ==============================================================================

install:
	@command -v uv >/dev/null 2>&1 || { echo "uv is not installed. Run: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
	uv sync

test:
	uv sync --dev
	uv run pytest tests/ -v --tb=short

lint:
	uv sync --dev
	uv run ruff check .
	uv run ruff format . --check

format:
	uv sync --dev
	uv run ruff check . --fix
	uv run ruff format .

.PHONY: install test lint format
