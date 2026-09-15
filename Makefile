# The single source of every gate. CI runs these same targets; see AGENTS.md.

PYTHONS := 3.11 3.14

.PHONY: check lint format format-check test sync census conformance-docs conformance-nightly

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

# Regenerate docs/conformance/ from a run of the conformance corpus. The gates fail while the
# committed files differ from a fresh run (see tests/conformance/report.py).
conformance-docs:
	PYTHONPATH=tests uv run --locked python tests/conformance/report.py

# The conformance corpus at TPC-H scale factor 1, run nightly (.github/workflows/nightly.yml).
conformance-nightly:
	METRICBRIDGE_TPCH_SCALE=1 uv run --locked pytest tests/conformance
