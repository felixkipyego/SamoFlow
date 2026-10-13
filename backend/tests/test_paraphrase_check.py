# backend/tests/test_paraphrase_check.py
# Task 3.7.c: the shared-word-run function (hand-built inputs) and the
# real check against the committed golden set. Offline only.
from app.evals.golden_set import DEFAULT_ITEMS_PATH, load_golden_set
from app.evals.paraphrase_check import longest_shared_word_run

MIN_FLAGGED_RUN = 6


def test_no_shared_words_at_all_is_zero():
    run = longest_shared_word_run("how much is shipping", "we accept visa and mastercard")
    assert run.length == 0


def test_exact_six_word_run_is_flagged():
    run = longest_shared_word_run(
        "standard domestic shipping takes 5 to 7 business days for us",
        "Standard domestic shipping takes 5 to 7 business days and costs $4.99",
    )
    assert run.length >= MIN_FLAGGED_RUN


def test_five_word_run_is_not_flagged():
    run = longest_shared_word_run(
        "standard domestic shipping takes 5 days usually",
        "Standard domestic shipping takes 5 business days.",
    )
    assert run.length == 5


def test_match_is_case_insensitive():
    run = longest_shared_word_run(
        "DOES THE WARRANTY COVER THIS ITEM TODAY",
        "does the warranty cover this item",
    )
    assert run.length == 6


def test_match_ignores_surrounding_punctuation():
    run = longest_shared_word_run(
        "do you offer expedited shipping, or not?",
        "We offer expedited shipping. It costs extra.",
    )
    assert run.length == 3  # "offer expedited shipping"


def test_non_contiguous_overlap_does_not_count():
    # Every word appears somewhere in the passage, but never as one
    # unbroken run -- the real "paraphrase" signal this check cares
    # about is a copied PHRASE, not shared vocabulary alone.
    run = longest_shared_word_run(
        "shipping cost is how much for international",
        "the international shipping department sets the cost separately",
    )
    assert run.length <= 2


def test_empty_passage_is_zero():
    assert longest_shared_word_run("some question", "").length == 0


def test_the_matched_words_are_reported():
    run = longest_shared_word_run(
        "when do i get expedited shipping options",
        "We offer expedited shipping options today.",
    )
    assert run.words == ("expedited", "shipping", "options")


def test_committed_golden_set_has_no_item_copying_six_or_more_words_from_its_evidence():
    golden_set = load_golden_set(DEFAULT_ITEMS_PATH)
    violations = []
    for item in golden_set.items:
        if item.category == "greeting":
            # Deliberately exempt -- see items.yaml's own header comment:
            # greeting items are an exact fixed-phrase match by design,
            # not a paraphrase of any corpus content.
            continue
        for source in item.expected_sources:
            run = longest_shared_word_run(item.question, source.evidence_passage)
            if run.length >= MIN_FLAGGED_RUN:
                violations.append((item.id, source.document, run.length, " ".join(run.words)))
    assert not violations, (
        f"question(s) copy {MIN_FLAGGED_RUN}+ words from their own evidence: {violations}"
    )
