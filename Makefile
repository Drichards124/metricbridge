# The single source of every gate. CI runs these same targets; see AGENTS.md.

PYTHONS := 3.11 3.14

.PHONY: check lint format format-check test sync census

check: lint format-check test

lint:
	uv run --locked ruff check

format:
	uv run --locked ruff format

format-check:
	uv run --locked ruff format --check

test:
	@for py in $(PYTHONS); do \
		echo "== Python $$py"; \
		uv run --locked --python $$py pytest || exit 1; \
	done

sync:
	uv sync --locked

# Feature census of public semantic manifests (network; see scripts/manifest_census.py).
census:
	uv run --locked python scripts/manifest_census.py
