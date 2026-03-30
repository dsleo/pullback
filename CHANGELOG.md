# Changelog

Short, human-readable notes about what changed and why.

## [Unreleased]

### 2026-03-30 — Simplification pass
- Removed benchmark/eval harness and related config knobs.
- Simplified discovery chain and provider configs (OpenAlex + OpenAI search only).
- Simplified forager flow (single best header → single block).
- Consolidated settings to a small, stable set in `.env.example`.
- Cleaned tests and docs to match the current minimal surface area.

If you need deeper history, check `git log` for the full record.
