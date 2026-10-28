"""The embedding conversion keeps every value that can be stored as a vector."""

from app.db import embedding_fits_vector, embeddings_match


def test_a_768_array_can_be_stored_as_a_vector():
    assert embedding_fits_vector([0.1] * 768)
    assert embedding_fits_vector("[" + ",".join(["0.1"] * 768) + "]")
    assert embedding_fits_vector(None)


def test_a_short_or_empty_embedding_is_refused():
    assert embedding_fits_vector([0.1] * 767) is False
    assert embedding_fits_vector([]) is False
    assert embedding_fits_vector("not-a-vector") is False
    assert embedding_fits_vector("[1, 2]") is False


def test_float32_rounding_still_counts_as_the_same_embedding():
    original = [0.123456789] * 768
    stored = [float(format(value, ".8g")) for value in original]
    assert embeddings_match(original, stored)


def test_a_different_number_does_not_match():
    original = [0.25] * 768
    changed = [0.25] * 768
    changed[0] = 0.9
    assert embeddings_match(original, changed) is False
