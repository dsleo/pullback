# Deployment Guide

## Vercel Setup

The Pullback is deployed on Vercel. To configure it with API keys and settings:

### Option 1: Via Vercel Dashboard (Recommended)

1. Go to your Vercel project at https://vercel.com/dashboard
2. Navigate to **Settings** → **Environment Variables**
3. Add the following variables (adjust as needed):

#### Required for Search Functionality
- **OPENAI_API_KEY** — Your OpenAI API key (for LLM query planning)
  - Get from: https://platform.openai.com/api-keys
  - Set Scope: Production

- **E2B_API_KEY** — E2B API key (for fetching arXiv LaTeX sources)
  - Get from: https://e2b.dev
  - Set Scope: Production

#### Discovery Providers (Optional)
- **OPENALEX_API_KEY** — For higher rate limits on OpenAlex
  - Get from: https://docs.openalex.org/api
  
- **OPENROUTER_API_KEY** — For additional LLM-based discovery
  - Get from: https://openrouter.ai

#### Orchestration Settings (Optional)
- **PULLBACK_LIBRARIAN_MODEL** — Query planning model
  - Examples: `openai/gpt-4o-mini`, `anthropic/claude-3-haiku`
  - Default: `openai/gpt-4o-mini`
  - Set to `test` to disable LLM calls (no planning)

- **PULLBACK_DISCOVERY_PROVIDERS** — Active providers (comma-separated)
  - Default: `arxiv_api`
  - Options: `openalex`, `zbmath_open`, `arxiv_api`, `semantic_scholar`, `openrouter_search`

- **PULLBACK_AGENTIC** — Enable query replanning (0 or 1)
  - Default: 1 (enabled)

- **PULLBACK_TIMEOUT_SECONDS** — Timeout in seconds
  - Default: 60

#### Reranking Settings (Optional)
- **PULLBACK_RERANKER** — Reranking strategy
  - Options: `token-overlap` (default), `bge`, `colbert`, `openrouter_model`
  - Default: `token-overlap`

### Option 2: Via Vercel CLI

```bash
# Install Vercel CLI
npm i -g vercel

# Login to Vercel
vercel login

# Link to your project (if not already linked)
vercel link

# Add environment variables
vercel env add OPENAI_API_KEY
vercel env add E2B_API_KEY
vercel env add OPENALEX_API_KEY
# ... etc

# Deploy
vercel deploy --prod
```

### Option 3: Via Git (Automated Redeploy)

1. Configure environment variables via the Vercel dashboard once
2. Push changes to `main` branch — Vercel auto-redeploys
3. No need to manually deploy each time

## Configuration Hierarchy

Settings are loaded in this order (later overrides earlier):

1. **config.json** (if present in repo, defines defaults)
2. **Default config** (in `src/pullback/config.py`)
3. **Environment variables** (takes precedence)

For Vercel, only environment variables matter since `config.json` is not included in the deployment.

## Minimal Working Setup

To get the app running on Vercel with basic functionality:

```
OPENAI_API_KEY=sk-...
E2B_API_KEY=...
PULLBACK_DISCOVERY_PROVIDERS=arxiv_api
PULLBACK_RERANKER=token-overlap
PULLBACK_LIBRARIAN_MODEL=openai/gpt-4o-mini
```

This provides:
- arXiv discovery (free, no API key)
- OpenAI-based query planning
- E2B sandbox for LaTeX source fetching
- Token-overlap reranking (no ML model needed)

## Troubleshooting

**"Service temporarily unavailable" error**
- Check that OPENAI_API_KEY is set and valid
- Check Vercel logs: `vercel logs <deployment-url>`
- Try reducing PULLBACK_TIMEOUT_SECONDS if requests are timing out

**Search returns no results**
- Verify PULLBACK_DISCOVERY_PROVIDERS includes at least one valid provider
- Check API keys for configured providers (OPENALEX_API_KEY, etc.)
- Try setting PULLBACK_LIBRARIAN_MODEL=test to skip LLM planning

**E2B failures when fetching sources**
- Verify E2B_API_KEY is set and valid
- Check E2B account quota at https://e2b.dev/dashboard

## Local Development

For local testing with the same config structure:

```bash
cp .env.example .env.local
# Edit .env.local with your API keys
PYTHONPATH=src uvicorn pullback.api:app --reload --env-file .env.local
```
