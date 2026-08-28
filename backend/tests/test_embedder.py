import numpy as np

from app.ingestion.embedder import Embedder


def _cosine(a, b):
    a, b = np.array(a), np.array(b)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def test_embed_returns_a_vector_of_the_configured_dimension():
    embedder = Embedder()
    vector = embedder.embed("Total revenue increased year over year.")
    assert len(vector) == embedder.dimension
    assert all(isinstance(x, float) for x in vector)


def test_embed_batch_returns_one_vector_per_input_in_order():
    embedder = Embedder()
    texts = ["revenue grew", "goodwill impairment charge", "share repurchase program"]

    vectors = embedder.embed_batch(texts)

    assert len(vectors) == 3
    # order must be preserved: batch[i] corresponds to texts[i], which the
    # ingestion pipeline relies on to pair vectors back to passages
    assert _cosine(vectors[1], embedder.embed("goodwill impairment charge")) > 0.99


def test_embed_batch_on_empty_input_returns_empty_list():
    assert Embedder().embed_batch([]) == []


def test_semantically_similar_text_is_closer_than_unrelated_text():
    embedder = Embedder()
    revenue_a = embedder.embed("Total revenue rose eight percent this year.")
    revenue_b = embedder.embed("Net sales increased by eight percent year over year.")
    unrelated = embedder.embed("The board approved a new office lease in Denver.")

    assert _cosine(revenue_a, revenue_b) > _cosine(revenue_a, unrelated)


def test_query_and_passage_embeddings_share_a_vector_space():
    # asymmetric embedding models can use different prefixes for queries vs
    # documents; whatever we do, a question must still land near its answer
    embedder = Embedder()
    query = embedder.embed_query("What was the goodwill impairment charge?")
    passage = embedder.embed("We recorded a goodwill impairment charge of $6.4 million.")
    distractor = embedder.embed("The lease term expires in 2031.")

    assert _cosine(query, passage) > _cosine(query, distractor)
