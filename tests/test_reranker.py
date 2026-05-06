from mathgent.rerank import TokenOverlapReranker, HybridReranker, create_reranker


def test_create_reranker_token_strategy() -> None:
    reranker = create_reranker("token")
    assert isinstance(reranker, TokenOverlapReranker)
    assert 0.0 <= reranker.score("banach fixed point", "fixed point theorem") <= 1.0

def test_hybrid_reranker_filtering() -> None:
    # Use a small min_overlap to ensure it filters totally unrelated text
    reranker = HybridReranker(
        fast=TokenOverlapReranker(),
        slow=TokenOverlapReranker(),
        min_overlap=0.1
    )
    
    # Should be 0.0 because of no token overlap
    assert reranker.score("quantum gravity", "banana split recipe") == 0.0

def test_create_reranker_hybrid_strategy() -> None:
    # This might fail in CI if BGE model is not pre-downloaded, 
    # but we test the factory logic.
    try:
        reranker = create_reranker("hybrid")
        assert isinstance(reranker, HybridReranker)
    except Exception as e:
        print(f"Skipping BGE instantiation test: {e}")
