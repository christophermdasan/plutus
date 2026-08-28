from app.ingestion.chunker import TABLE_MARKER, chunk_pages

SHORT_PAGES = ["page one text", "page two text", "page three text"]


def test_every_passage_is_anchored_to_the_page_it_came_from():
    passages = chunk_pages("doc1", SHORT_PAGES)

    assert [p.page for p in passages] == [1, 2, 3]
    assert all(p.filing_id == "doc1" for p in passages)


def test_short_pages_produce_one_passage_each():
    assert len(chunk_pages("doc1", SHORT_PAGES)) == 3


def test_empty_and_whitespace_only_pages_are_skipped():
    passages = chunk_pages("doc1", ["real content here", "   \n  ", "", "more content"])

    assert len(passages) == 2
    assert [p.page for p in passages] == [1, 4]


def test_no_pages_returns_no_passages():
    assert chunk_pages("doc1", []) == []


def test_a_long_page_is_split_into_multiple_passages_all_on_that_page():
    paragraphs = [f"Paragraph {i} with a reasonable amount of filler text in it." * 6 for i in range(12)]
    long_page = "\n\n".join(paragraphs)

    passages = chunk_pages("doc1", [long_page])

    assert len(passages) > 1
    # splitting must never move content to a different page - citations
    # depend on the page anchor staying true
    assert all(p.page == 1 for p in passages)
    assert [p.ordinal for p in passages] == list(range(len(passages)))


def test_passages_have_stable_unique_ids():
    passages = chunk_pages("doc1", SHORT_PAGES)
    ids = [p.id for p in passages]

    assert len(set(ids)) == len(ids)
    # regenerating from identical input must produce identical ids, so
    # re-ingesting a filing upserts rather than duplicating
    assert ids == [p.id for p in chunk_pages("doc1", SHORT_PAGES)]


def test_a_table_is_never_split_across_passages():
    # financial tables are exactly where the numbers live; splitting one
    # mid-row would strip a figure from its label
    table = TABLE_MARKER + "\n" + "\n".join(f"Line item {i} | ${i}.0 | ${i}.5" for i in range(40))
    prose = "Some introductory prose about the results. " * 30
    passages = chunk_pages("doc1", [f"{prose}\n\n{table}"])

    containing_table = [p for p in passages if TABLE_MARKER in p.text]
    assert len(containing_table) == 1
    assert "Line item 0" in containing_table[0].text
    assert "Line item 39" in containing_table[0].text


def test_every_passage_carries_non_empty_text():
    paragraphs = ["Content paragraph number %d." % i * 20 for i in range(10)]
    passages = chunk_pages("doc1", ["\n\n".join(paragraphs)])
    assert all(p.text.strip() for p in passages)


def test_a_large_table_is_divided_between_rows_with_its_header_repeated():
    """A table too big for the model window has to be split, not kept whole.

    Keeping it whole was the old rule, and it quietly did the opposite of
    what it promised: the embedder truncates at 512 tokens, so on a very
    large table only the opening rows were ever represented and everything
    below was unreachable by search while still being paid for at ingest.
    """
    header = "Segment | FY2023 | FY2022"
    rows = [f"Product line {i} | ${i}.0 | ${i}.5" for i in range(300)]
    page = f"{TABLE_MARKER}\n" + "\n".join([header, *rows])

    passages = chunk_pages("f1", [page])

    assert len(passages) > 1, "an oversized table was not divided"
    for passage in passages:
        assert len(passage.text) < 2000, "a passage still exceeds the model window"
        # Every part must carry the header, or its figures lose their meaning.
        assert header in passage.text
        assert passage.text.startswith(TABLE_MARKER)

    # No row may be lost or cut in half.
    reassembled = "\n".join(p.text for p in passages)
    for i in (0, 150, 299):
        assert f"Product line {i} | ${i}.0 | ${i}.5" in reassembled


def test_a_small_table_is_still_kept_whole():
    """The split only applies past the point the model can read anyway."""
    page = f"{TABLE_MARKER}\nItem | 2023 | 2022\nRevenue | $184.6 | $162.3\nNet income | $21.4 | $18.9"
    passages = chunk_pages("f1", [page])
    assert len(passages) == 1
    assert "Revenue | $184.6" in passages[0].text
    assert "Net income | $21.4" in passages[0].text


def test_an_enormous_paragraph_is_split_between_sentences():
    page = " ".join(f"Sentence number {i} carries a fact worth finding." for i in range(400))
    passages = chunk_pages("f1", [page])

    assert len(passages) > 1
    for passage in passages:
        assert len(passage.text) < 2000
    reassembled = " ".join(p.text for p in passages)
    assert "Sentence number 399 carries a fact worth finding." in reassembled
