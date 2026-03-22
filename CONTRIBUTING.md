# Contributing

## Classic Development Setup
1. `uv venv`
2. `source .venv/bin/activate`
3. `uv pip install -e ".[dev]"`

## Local Checks
1. `python -m pytest`
2. `ruff check src tests`
3. `mypy src`
4. `bandit -q -r src`
5. `pip-audit`

## Pull Requests
- Be reasonable and keep changes scoped and documented.
- Add or update tests for behavioral changes.
- Keep API contract for `POST /search` backward compatible unless intentionally versioned.

Thanks.