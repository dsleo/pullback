# Contributing

## Setup

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env.local  # fill in your keys
```

## Tests

```bash
pytest               # all tests
pytest -k "test_foo" # specific test
```

For tests that hit real APIs, set `MATHGENT_LIBRARIAN_MODEL=test` to avoid LLM calls.

## Code Quality

```bash
ruff check src/ tests/   # lint
ruff format src/ tests/  # format
mypy src/                # type check
```

## Pull Requests

- One logical change per PR
- Tests must pass (`pytest`)
- No secrets in code or config files — use `.env.local` (gitignored)
