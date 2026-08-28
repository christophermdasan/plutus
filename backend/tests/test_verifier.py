from app.qa.verifier import verify_answer, verify_numeric_consistency, verify_quote

PAGE = """
Item 7. Management's Discussion and Analysis

Total revenue for fiscal year 2023 was $5.2 million, compared to
$4.8 million in the prior year, an increase of 8.3%.

Goodwill impairment of $1.1 million was recorded during the third quarter.
"""

SEGMENT_PAGE = """Note 11: Segment Information
(in millions)
FY2023 Revenue
FY2022 Revenue
Warehouse Automation
$139.0
$118.7

[TABLE]
(in millions) | FY2023 Revenue | FY2022 Revenue
Warehouse Automation | $139.0 | $118.7"""


# --- quote presence -------------------------------------------------------


def test_an_exact_quote_is_found():
    assert verify_quote(PAGE, "Total revenue for fiscal year 2023 was $5.2 million") is True


def test_whitespace_differences_do_not_break_a_match():
    quote = "Total revenue for fiscal year 2023 was   $5.2 million,\ncompared to $4.8 million"
    assert verify_quote(PAGE, quote) is True


def test_a_quote_that_is_not_on_the_page_is_rejected():
    assert verify_quote(PAGE, "Net income attributable to shareholders was $9.9 million") is False


def test_a_quote_with_an_altered_digit_is_rejected():
    # textually near-identical to a real sentence, but the figure was changed:
    # exactly the hallucination shape this exists to catch
    assert verify_quote(PAGE, "Total revenue for fiscal year 2023 was $5.9 million") is False


# --- numeric consistency --------------------------------------------------


def test_a_figure_present_in_the_quote_passes():
    quote = "Goodwill impairment of $1.1 million was recorded during the third quarter."
    assert verify_numeric_consistency("$1.1 million", quote, question="") is True


def test_a_figure_absent_from_the_quote_is_rejected():
    quote = "Goodwill impairment of $1.1 million was recorded during the third quarter."
    assert verify_numeric_consistency("$2.4 million", quote, question="") is False


def test_a_non_numeric_answer_passes_trivially():
    quote = "Goodwill impairment of $1.1 million was recorded during the third quarter."
    assert verify_numeric_consistency("Yes, an impairment was recorded.", quote, question="") is True


def test_a_year_echoed_from_the_question_is_not_treated_as_a_claim():
    # The user asked about FY2023/FY2022, so those years are context the
    # answer is repeating back - not new figures needing proof.
    question = "What is the Warehouse Automation revenue in FY2023 and FY2022?"
    answer = "Warehouse Automation revenue was $139.0 million in FY2023 and $118.7 million in FY2022."
    quote = "Warehouse Automation | $139.0 | $118.7"

    assert verify_numeric_consistency(answer, quote, question=question) is True


def test_a_year_labelling_a_verified_figure_is_accepted_from_the_page():
    # Even when the question never named a year, a year that labels an
    # already-verified figure and appears on the cited page is a label, not
    # an independent claim. The money still has to be in the quote.
    answer = "Warehouse Automation revenue was $139.0 million in FY2023."
    quote = "Warehouse Automation | $139.0 | $118.7"

    assert (
        verify_numeric_consistency(answer, quote, question="", page_text=SEGMENT_PAGE) is True
    )


def test_a_year_not_present_anywhere_is_still_rejected():
    answer = "Warehouse Automation revenue was $139.0 million in FY2019."
    quote = "Warehouse Automation | $139.0 | $118.7"

    assert (
        verify_numeric_consistency(answer, quote, question="", page_text=SEGMENT_PAGE) is False
    )


def test_a_year_that_is_itself_the_answer_must_be_in_the_quote():
    # No other figure to anchor it, so the year IS the claim and gets no
    # leniency - otherwise "when does the loan mature?" could be answered
    # with any year that happens to appear on the page.
    page = "The term loan matures on June 30, 2027 and the lease expires in 2031."
    assert verify_numeric_consistency("It matures in 2031.", "matures on June 30, 2027", question="", page_text=page) is False
    assert verify_numeric_consistency("It matures in 2027.", "matures on June 30, 2027", question="", page_text=page) is True


def test_a_fabricated_figure_is_still_rejected_even_if_it_appears_on_the_page():
    # The strict rule for money is unchanged: the amount must be in the
    # quote, not merely somewhere on the page.
    answer = "Warehouse Automation revenue was $45.6 million."
    quote = "Warehouse Automation | $139.0 | $118.7"

    assert (
        verify_numeric_consistency(answer, quote, question="", page_text=SEGMENT_PAGE) is False
    )


# --- end to end -----------------------------------------------------------


def test_a_fully_supported_answer_passes():
    result = verify_answer(
        page_text=PAGE,
        quote="Goodwill impairment of $1.1 million was recorded during the third quarter.",
        answer="$1.1 million",
        question="What goodwill impairment was recorded?",
    )
    assert result.passed is True


def test_an_answer_whose_quote_is_not_on_the_page_fails():
    result = verify_answer(
        page_text=PAGE,
        quote="Net income attributable to shareholders was $9.9 million",
        answer="$9.9 million",
        question="What was net income?",
    )
    assert result.passed is False
    assert "not found on page" in result.reason


def test_an_answer_stating_a_figure_absent_from_its_own_quote_fails():
    result = verify_answer(
        page_text=PAGE,
        quote="Total revenue for fiscal year 2023 was $5.2 million",
        answer="$6.7 million",
        question="What was total revenue?",
    )
    assert result.passed is False
    assert "number" in result.reason


def test_the_multi_year_segment_question_now_verifies():
    # The regression this whole change exists for.
    result = verify_answer(
        page_text=SEGMENT_PAGE,
        quote="Warehouse Automation | $139.0 | $118.7",
        answer="Warehouse Automation revenue was $139.0 million in FY2023 and $118.7 million in FY2022.",
        question="What is the Warehouse Automation revenue in FY2023 and FY2022?",
    )
    assert result.passed is True


# --- real-filing table quoting ---------------------------------------------

# Serialized SEC table rows put the currency symbol in its own cell, exactly
# as the source table does. Taken from Apple's FY2025 10-K, page 32.
REAL_TABLE_PAGE = """Segment Operating Performance
[TABLE]
Americas | $ | 178,491 |  |  | 8 | % |  | $ | 167,045
Greater China | 64,377 |  |  | (4) | % |  | 66,952
Total net sales | $ | 416,161 |  | 6 | % |  | $ | 391,035 |  | 2 | % |  | $ | 383,285"""


def test_a_quote_spanning_table_cell_separators_is_accepted():
    # The model writes "$416,161"; the page holds "$ | 416,161" because the
    # currency symbol is a separate column. Rejecting this throws away a
    # correct, genuinely-supported answer.
    assert verify_quote(REAL_TABLE_PAGE, "$416,161") is True
    assert verify_quote(REAL_TABLE_PAGE, "Total net sales $416,161") is True


def test_a_percentage_written_without_its_cell_gap_is_accepted():
    assert verify_quote(REAL_TABLE_PAGE, "6%") is True


def test_a_figure_absent_from_the_table_is_still_rejected():
    # The leniency must not become "any number is fine".
    assert verify_quote(REAL_TABLE_PAGE, "$416,999") is False
    assert verify_quote(REAL_TABLE_PAGE, "$500,000") is False


def test_a_full_table_row_still_matches_verbatim():
    assert verify_quote(REAL_TABLE_PAGE, "Greater China | 64,377") is True


def test_end_to_end_on_a_real_table_row():
    result = verify_answer(
        page_text=REAL_TABLE_PAGE,
        quote="$416,161",
        answer="$416,161 million",
        question="What were total net sales in fiscal 2025?",
    )
    assert result.passed is True


# --- currency-symbol formatting -------------------------------------------

# Apple FY2025 page 32: the currency symbol is hoisted into the column
# header, so individual cells carry bare numbers.
BARE_NUMBER_PAGE = """Segment Operating Performance
[TABLE]
(in millions) | 2025 | Change | 2024
Greater China | 64,377 |  |  | (4) | % |  | 66,952"""


def test_a_currency_symbol_the_model_adds_does_not_defeat_verification():
    # The model writes "$64,377"; the cell holds "64,377" because the "$"
    # lives in the column header. Same digits, same claim.
    assert verify_quote(BARE_NUMBER_PAGE, "$64,377") is True


def test_a_currency_symbol_the_model_drops_does_not_defeat_verification():
    page = "Total net sales | $ | 416,161"
    assert verify_quote(page, "416,161") is True


def test_normalising_currency_does_not_admit_a_wrong_number():
    # The invariant that must hold: only *formatting* is normalised. The
    # digits themselves still have to be on the page.
    assert verify_quote(BARE_NUMBER_PAGE, "$64,999") is False
    assert verify_quote(BARE_NUMBER_PAGE, "$70,000") is False


def test_end_to_end_on_a_bare_number_cell():
    result = verify_answer(
        page_text=BARE_NUMBER_PAGE,
        quote="$64,377",
        answer="$64,377 million",
        question="What were net sales in Greater China in fiscal 2025?",
    )
    assert result.passed is True


def test_end_to_end_still_rejects_a_fabricated_figure_on_a_bare_number_page():
    result = verify_answer(
        page_text=BARE_NUMBER_PAGE,
        quote="$64,377",
        answer="$99,999 million",
        question="What were net sales in Greater China?",
    )
    assert result.passed is False


def test_a_clause_comma_is_not_read_as_part_of_a_number():
    """The regression that rejected correct answers on punctuation alone.

    A comma is a thousands separator only when three digits follow it. Read
    greedily it also swallowed the comma ending a clause, so an answer saying
    "December 31, 2022, with..." produced the token "2022," while the source
    it quoted verbatim produced "2022" - and the answer was refused for
    containing a figure its own quote supposedly lacked.
    """
    page = (
        "One customer accounted for 16% of our consolidated net revenue for the "
        "year ended December 31, 2022. Sales to this customer consisted of "
        "products from all reportable segments."
    )
    quote = "One customer accounted for 16% of our consolidated net revenue for the year ended December 31, 2022."
    answer = (
        "Yes, one customer accounted for 16% of consolidated net revenue for the "
        "year ended December 31, 2022, across all segments."
    )

    result = verify_answer(page_text=page, quote=quote, answer=answer,
                           question="Did AMD report customer concentration in FY22?")
    assert result.passed, result.reason


def test_thousands_separators_are_still_read_as_one_number():
    """The narrower rule must not break the figures that matter most."""
    page = "Purchases of property, plant and equipment (PP&E) $ (1,577) $ (1,373)"
    quote = "Purchases of property, plant and equipment (PP&E) $ (1,577)"
    answer = "Capital expenditure was $1,577 million."
    assert verify_answer(page_text=page, quote=quote, answer=answer,
                         question="What was capex?").passed


def test_a_fabricated_figure_is_still_rejected():
    """Loosening the tokeniser must not loosen the guarantee."""
    page = "Total revenue for fiscal 2023 was $184.6 million."
    quote = "Total revenue for fiscal 2023 was $184.6 million."
    answer = "Total revenue for fiscal 2023 was $999.9 million."
    assert not verify_answer(page_text=page, quote=quote, answer=answer,
                             question="What was total revenue?").passed
