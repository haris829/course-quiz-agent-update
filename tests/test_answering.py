"""Finding the right material, asking for the answer, and reading the reply. All pure."""

from __future__ import annotations

import json

import pytest

from qgen.domain.answering import (
    MAX_SNIPPETS,
    SNIPPET_CHARS,
    Snippet,
    build_answer_prompt,
    keywords,
    parse_answer,
    rank,
    required_matches,
    score,
)


def snippet(text: str, explanation: str | None = None, course: str = "Criminology") -> Snippet:
    return Snippet(source="course question bank", course=course, text=text, explanation=explanation)


# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------


def test_the_words_worth_searching_for_are_kept():
    assert keywords("What is collective efficacy?") == ("collective", "efficacy")


def test_common_words_are_dropped():
    assert "what" not in keywords("What is the duty of care?")
    assert "the" not in keywords("What is the duty of care?")
    assert "duty" in keywords("What is the duty of care?")


def test_short_tokens_are_dropped():
    assert keywords("s.2(1) of an Act") == ("act",)


def test_a_repeated_word_is_kept_once_in_the_order_it_appeared():
    assert keywords("negligence and negligence again") == ("negligence", "again")


def test_a_legal_term_that_looks_like_a_stopword_is_not_dropped():
    """"act", "law" and "duty" are real keywords here, whatever a generic stopword list says."""
    for term in ("act", "law", "duty", "not"):
        assert term in keywords(f"the {term} of it") or term == "not"


@pytest.mark.parametrize("text", ["", "   ", "of the a an"])
def test_a_question_with_nothing_to_search_for_yields_no_keywords(text):
    assert keywords(text) == ()


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_a_snippet_is_scored_on_how_many_distinct_keywords_it_contains():
    item = snippet("Duty of care", "Foreseeability establishes a duty of care in negligence.")

    assert score(item, ("duty", "negligence", "foreseeability")) == 3
    assert score(item, ("duty", "consent")) == 1


def test_repeating_one_keyword_does_not_raise_a_score():
    """A snippet saying "negligence" nine times is about one thing."""
    many = snippet("negligence negligence negligence negligence")

    assert score(many, ("negligence", "consent")) == 1


def test_the_explanation_is_searched_as_well_as_the_question():
    item = snippet("Which theory applies?", "Social disorganisation theory, per Shaw and McKay.")

    assert score(item, ("mckay",)) == 1


def test_material_matching_nothing_is_not_returned():
    """Better no material than material that matched on an incidental word."""
    assert rank([snippet("Something else entirely")], ("negligence",)) == []


def test_the_best_match_comes_first():
    weak = snippet("A question about duty")
    strong = snippet("Duty of care and negligence", "Foreseeability, duty, negligence.")

    assert rank([weak, strong], ("duty", "negligence", "foreseeability"))[0] is strong


def test_a_tie_goes_to_the_material_that_arrived_first():
    """The database read is newest first, so a tie goes to the more recent item."""
    newer, older = snippet("duty one"), snippet("duty two")

    assert rank([newer, older], ("duty",)) == [newer, older]


def test_no_more_than_the_cap_is_returned():
    many = [snippet(f"duty number {n}") for n in range(50)]

    assert len(rank(many, ("duty",))) == MAX_SNIPPETS


def test_a_question_with_no_keywords_finds_nothing():
    assert rank([snippet("anything")], ()) == []


def test_a_short_question_needs_only_one_match():
    """"mens rea" is two words and both matter."""
    assert required_matches(("mens", "rea")) == 1
    assert rank([snippet("mens rea explained")], ("mens", "rea")) != []


def test_a_longer_question_needs_two_matches_so_one_stray_word_is_not_enough():
    """The bug this exists for: "explain photosynthesis in chloroplasts" matched criminology
    material on the strength of the word "explain" alone, and the page then called the answer
    grounded in a course it had nothing to do with."""
    terms = ("negligence", "foreseeability", "remoteness")
    # Long, so the two-match floor applies to it. A short one is judged on one match now.
    stray = snippet("A question about negligence in a completely different sense " + "padding " * 40)
    real = snippet("Negligence and remoteness", "Foreseeability limits remoteness of damage.")

    assert required_matches(terms) == 2
    assert rank([stray, real], terms) == [real]


def test_an_instruction_verb_is_not_a_keyword():
    """"Explain" tells you nothing about the subject, and matches every explanation there is."""
    for verb in ("explain", "describe", "define", "compare", "difference", "example"):
        assert verb not in keywords(f"{verb} the duty of care")


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------


def test_the_question_and_the_material_are_both_in_the_prompt():
    prompt = build_answer_prompt("What is duty of care?", [snippet("Duty", "It is owed.")])

    assert "What is duty of care?" in prompt
    assert "[1]" in prompt
    assert "Duty" in prompt and "It is owed." in prompt


def test_the_material_is_numbered_so_the_reply_can_cite_it():
    prompt = build_answer_prompt("q?", [snippet("one"), snippet("two"), snippet("three")])

    assert "[1]" in prompt and "[2]" in prompt and "[3]" in prompt


def test_the_course_is_named_when_it_is_known():
    assert "They are studying: Medical Law" in build_answer_prompt("q?", [], course="Medical Law")


def test_with_no_material_the_model_is_told_to_say_so_rather_than_invent_a_source():
    prompt = build_answer_prompt("What is duty of care?", [])

    assert "no material in the course library" in prompt
    assert "does not cover it" in prompt


def test_a_very_long_explanation_is_truncated():
    prompt = build_answer_prompt("q?", [snippet("stem", "y" * 2000)])

    assert "y" * SNIPPET_CHARS not in prompt or len(prompt) < 2000 + 1500


def test_the_reply_shape_is_shown():
    prompt = build_answer_prompt("q?", [snippet("one")])

    assert '{"answer": "...", "used": [1, 3]}' in prompt


# ---------------------------------------------------------------------------
# The parse
# ---------------------------------------------------------------------------


def test_an_answer_and_its_citations_are_read():
    material = [snippet("one"), snippet("two")]
    reply = json.dumps({"answer": "It is the duty owed.", "used": [2]})

    answer = parse_answer(reply, material)

    assert answer.text == "It is the duty owed."
    assert answer.used == (2,)
    assert answer.grounded is True
    assert answer.citations_unavailable is False


def test_an_empty_citation_list_means_general_knowledge_and_says_so():
    answer = parse_answer(json.dumps({"answer": "From my own knowledge.", "used": []}), [])

    assert answer.grounded is False
    assert answer.citations_unavailable is False


def test_a_citation_pointing_at_material_that_does_not_exist_is_dropped():
    """Item 9 of eight is a reference with nothing behind it, not a ninth item."""
    answer = parse_answer(json.dumps({"answer": "a", "used": [1, 9, 0, -2]}), [snippet("one")])

    assert answer.used == (1,)


def test_a_repeated_citation_is_kept_once():
    answer = parse_answer(json.dumps({"answer": "a", "used": [1, 1]}), [snippet("one")])

    assert answer.used == (1,)


def test_a_non_numeric_citation_is_ignored_rather_than_failing_the_answer():
    answer = parse_answer(json.dumps({"answer": "a", "used": ["one", None, 1]}), [snippet("x")])

    assert answer.used == (1,)


def test_json_in_a_fenced_block_is_still_read():
    fenced = "```json\n" + json.dumps({"answer": "Fenced.", "used": []}) + "\n```"

    assert parse_answer(fenced, []).text == "Fenced."


def test_a_reply_that_is_not_json_is_used_as_the_answer_but_flagged_as_uncited():
    """Prose has no answer key to get wrong, so it is used - but "unknown" is not "none"."""
    answer = parse_answer("The duty is owed to one's neighbour.", [snippet("one")])

    assert answer.text == "The duty is owed to one's neighbour."
    assert answer.citations_unavailable is True
    assert answer.used == ()


@pytest.mark.parametrize("reply", ["", "   ", None])
def test_a_reply_with_nothing_in_it_is_no_answer_at_all(reply):
    assert parse_answer(reply, []) is None


def test_json_with_an_empty_answer_is_no_answer_at_all():
    """Not turned into "I don't know" - that would be putting words in the model's mouth."""
    assert parse_answer(json.dumps({"answer": "   ", "used": [1]}), [snippet("x")]) is None


def test_whitespace_in_an_answer_is_tidied():
    answer = parse_answer(json.dumps({"answer": "Two   words\nover lines.", "used": []}), [])

    assert answer.text == "Two words over lines."


def test_a_short_precise_source_is_not_drowned_out_by_long_vague_ones():
    """The bug this exists for: "How long do I have to bring a defamation claim?" dropped the
    one row that answered it exactly, because a 93-character statutory line cannot match two
    words of a chatty question while eight rambling ones can."""
    terms = keywords("How long do I have to bring a defamation claim?")
    statute = Snippet(
        source="limitation periods",
        course="Limitation Act reference",
        text="Defamation / malicious falsehood: 1 year",
        explanation="Under s.4A LA 1980. The clock runs from publication.",
    )
    rambling = [
        snippet(f"A long question about how to bring a claim, number {n}, " + "padding " * 30)
        for n in range(8)
    ]
    # Every rambling one outscores the statute - they match "long", "bring" and "claim" while it
    # matches only "defamation" - which is exactly why a reserved place is needed.

    ranked = rank([*rambling, statute], terms)

    assert statute in ranked, "the row that answers the question was dropped"
    assert ranked[0] is statute, "a precise line should outrank a paragraph on the same score"


def test_a_long_passage_still_needs_two_matches():
    """The floor must keep doing its job for the case it was written for."""
    terms = ("negligence", "foreseeability", "remoteness")
    stray = snippet("A long passage mentioning negligence once " + "and padding " * 40)

    assert rank([stray], terms) == []
