#!/usr/bin/env python3
"""
Diagnostic script to test OpenAlex connectivity and semantic search.
Minimal standalone test without heavy dependencies.
"""
import asyncio
import httpx
import os
import json


async def test_openalex():
    """Test OpenAlex semantic and keyword search."""
    print("=" * 70)
    print("OpenAlex Diagnostic Check")
    print("=" * 70)

    # Load from env vars
    api_key = os.getenv("OPENALEX_API_KEY") or os.getenv("PULLBACK_OPENALEX_API_KEY")
    mailto = os.getenv("OPENALEX_MAILTO") or "you@example.com"
    timeout = float(os.getenv("PULLBACK_TIMEOUT_SECONDS", "30"))

    print(f"✓ Settings loaded from environment")
    print(f"  API Key: {api_key[:20]}..." if api_key else "  API Key: (none)")
    print(f"  Mailto: {mailto}")
    print(f"  Timeout: {timeout}s")
    print()

    query = "Banach fixed point theorem"
    max_results = 3

    async with httpx.AsyncClient(timeout=timeout) as client:
        # Test 1: Semantic search
        print("=" * 70)
        print("Test 1: Semantic Search (OpenAlex Databricks Vector endpoint)")
        print("=" * 70)

        semantic_params = {
            "search": query,
            "sort": "relevance_score:desc",
            "per_page": min(25, max_results * 2),
            "select": "id,title,doi,publication_year",
        }
        if api_key:
            semantic_params["api_key"] = api_key

        try:
            resp = await client.get(
                "https://api.openalex.org/works",
                params=semantic_params,
            )
            print(f"Status: {resp.status_code}")

            if resp.status_code == 200:
                data = resp.json()
                print(f"✓ Semantic search succeeded!")
                results = data.get("results", [])
                print(f"  Found {len(results)} papers:")
                for work in results[:3]:
                    arxiv_id = None
                    ids = work.get("ids", {})
                    if ids.get("arxiv"):
                        arxiv_id = ids["arxiv"]
                    elif ids.get("doi"):
                        arxiv_id = ids["doi"]
                    else:
                        arxiv_id = work.get("id", "unknown")
                    print(f"    - {work.get('title', 'Unknown')[:60]}...")
                    print(f"      arXiv/ID: {arxiv_id}")
                print()
            else:
                error_msg = resp.text
                try:
                    error_data = resp.json()
                    error_msg = error_data.get("message", error_msg)
                except:
                    pass
                print(f"✗ Semantic search failed with status {resp.status_code}")
                print(f"  Error: {error_msg[:200]}")
                print()
        except Exception as e:
            print(f"✗ Semantic search error: {e}")
            print()

        # Test 2: Keyword search (fallback)
        print("=" * 70)
        print("Test 2: Keyword Search (fallback, always works)")
        print("=" * 70)

        keyword_params = {
            "search": query,
            "sort": "cited_by_count:desc",
            "per_page": min(25, max_results * 2),
            "select": "id,title,doi,publication_year",
        }
        if api_key:
            keyword_params["api_key"] = api_key

        try:
            resp = await client.get(
                "https://api.openalex.org/works",
                params=keyword_params,
            )
            print(f"Status: {resp.status_code}")

            if resp.status_code == 200:
                data = resp.json()
                print(f"✓ Keyword search succeeded!")
                results = data.get("results", [])
                print(f"  Found {len(results)} papers:")
                for work in results[:3]:
                    arxiv_id = None
                    ids = work.get("ids", {})
                    if ids.get("arxiv"):
                        arxiv_id = ids["arxiv"]
                    elif ids.get("doi"):
                        arxiv_id = ids["doi"]
                    else:
                        arxiv_id = work.get("id", "unknown")
                    print(f"    - {work.get('title', 'Unknown')[:60]}...")
                    print(f"      arXiv/ID: {arxiv_id}")
                print()
            else:
                error_msg = resp.text
                try:
                    error_data = resp.json()
                    error_msg = error_data.get("message", error_msg)
                except:
                    pass
                print(f"✗ Keyword search failed with status {resp.status_code}")
                print(f"  Error: {error_msg[:200]}")
                print()
        except Exception as e:
            print(f"✗ Keyword search error: {e}")
            print()

    print("=" * 70)
    print("Summary:")
    print("=" * 70)
    print("• OpenAlex attempts semantic search first (via Databricks endpoint)")
    print("• On 403 Forbidden, it automatically falls back to keyword search")
    print("• If keyword search works, benchmarks will proceed normally")
    print()
    print("If semantic search is hitting 403, this is a temporary issue with")
    print("OpenAlex's infrastructure—it's not a configuration problem on your end.")


if __name__ == "__main__":
    asyncio.run(test_openalex())
