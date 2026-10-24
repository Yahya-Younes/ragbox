from ragbox.llm import same_model
from ragbox.retrieval import fts_query, rrf_merge


def test_fts_query_quotes_every_word_and_drops_syntax():
    q = fts_query('What is "NEAR(x y)" OR revenue* -- AND 2024?')
    assert q == '"what" OR "is" OR "near" OR "or" OR "revenue" OR "and" OR "2024"'
    assert "*" not in q and "(" not in q and "--" not in q


def test_rrf_rewards_documents_found_by_both_methods():
    assert rrf_merge([[1, 2, 3], [3, 4]], top_k=2)[0] == 3


def test_model_names_with_and_without_tag():
    assert same_model("nomic-embed-text", "nomic-embed-text:latest")
    assert not same_model("gemma3:4b", "gemma3:12b")
