from mathgent.rerank import TokenOverlapReranker, create_reranker


def test_create_reranker_token_strategy() -> None:
    reranker = create_reranker("token")
    assert isinstance(reranker, TokenOverlapReranker)
    assert 0.0 <= reranker.score("banach fixed point", "fixed point theorem") <= 1.0
