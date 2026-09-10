"""Retrieval and the live ask, against the real database with a fake model.

What these pin down is provenance: that material really is found and really is put in front of
the model, and that an answer with nothing behind it says so instead of borrowing the authority
of a course it never used.
"""

from __future__ import annotations

import json

import pytest

from qgen import library
from qgen.answers import ask
from qgen.domain.answering import keywords
from qgen.errors import GenerationUnavailable, QGenError
from tests.fakes import AnsweringLLM, answer_reply

CRIMINOLOGY = "LL-34590"


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def test_material_is_found_in_the_platform_question_bank(conn):
    found = library.find_material(conn, keywords("Which OSI layer routes packets?"))

    assert found
    assert any("osi" in snippet.body.lower() for snippet in found)


def test_material_carries_the_course_it_belongs_to(conn):
    found = library.find_material(conn, ("criminology", "disorganisation"))

    assert any(snippet.course for snippet in found)


def test_no_keywords_finds_nothing_rather_than_everything(conn):
    assert library.find_material(conn, ()) == []


def test_a_keyword_nothing_mentions_finds_nothing(conn):
    assert library.find_material(conn, ("zzzzunlikelyterm",)) == []


def test_a_percent_sign_in_a_keyword_matches_only_a_literal_percent_sign(conn):
    """It is a LIKE wildcard. Unescaped it returns the whole library as a "match"; escaped it
    returns the rows that genuinely contain one, which for a legal question bank is the ones
    quoting a percentage."""
    everything = library.find_material(conn, ("the",))
    found = library.find_material(conn, ("%",))

    assert all("%" in snippet.body for snippet in found)
    assert len(found) < len(everything)


def test_material_from_the_named_course_is_preferred(conn):
    found = library.find_material(
        conn, ("theory",), course_title="Criminology (Postgraduate)", course_code=CRIMINOLOGY
    )

    if found and any(s.course == "Criminology (Postgraduate)" for s in found):
        assert found[0].course == "Criminology (Postgraduate)"


def test_a_question_naming_a_course_is_recognised(conn):
    assert library.guess_course(conn, "In Medical Law MA (Postgraduate), what is consent?") == (
        "LL-45165",
        "Medical Law MA (Postgraduate)",
    )


def test_a_question_naming_no_course_is_not_forced_into_one(conn):
    """"What is mens rea?" belongs to several courses at once, so it is pinned to none."""
    assert library.guess_course(conn, "What is mens rea?") is None


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------


def test_the_material_found_is_put_in_front_of_the_model(conn):
    llm = AnsweringLLM(answer_reply("Grounded answer.", (1,)))

    outcome = ask(conn, llm, question="Which OSI layer routes packets between networks?")

    assert outcome.material
    prompt = llm.prompts[0]
    assert "Which OSI layer routes packets between networks?" in prompt
    assert "[1]" in prompt


def test_an_answer_that_cites_material_is_reported_as_grounded(conn):
    outcome = ask(
        conn,
        AnsweringLLM(answer_reply("Grounded answer.", (1,))),
        question="Which OSI layer routes packets?",
    )

    assert outcome.grounded is True
    assert "Answered from 1 items of course material" in outcome.provenance


def test_an_answer_citing_nothing_is_reported_as_general_knowledge(conn):
    outcome = ask(
        conn,
        AnsweringLLM(json.dumps({"answer": "From general knowledge.", "used": []})),
        question="Which OSI layer routes packets?",
    )

    assert outcome.grounded is False
    assert "general subject knowledge" in outcome.provenance


def test_when_no_material_exists_the_answer_says_the_courses_hold_nothing_on_it(conn):
    # Nothing in a library of legal and networking courses mentions either word. A question like
    # "the offside rule in association football" would not do - "rule" and "association" are
    # both all over the legal material, and it would find plenty.
    outcome = ask(conn, AnsweringLLM(), question="Explain photosynthesis in chloroplasts")

    assert outcome.material == ()
    assert outcome.provenance == (
        "There is no course material on this. The answer is general subject knowledge."
    )


def test_a_reply_that_was_not_json_says_the_citations_are_unknown_not_none(conn):
    outcome = ask(conn, AnsweringLLM("Layer 3, the network layer."), question="Which OSI layer routes?")

    assert outcome.answer == "Layer 3, the network layer."
    assert outcome.citations_unavailable is True
    assert "did not say which" in outcome.provenance


def test_a_question_naming_a_course_is_answered_as_that_course(conn):
    outcome = ask(conn, AnsweringLLM(), question="In Medical Law MA (Postgraduate), what is consent?")

    assert outcome.course_code == "LL-45165"
    assert "Medical Law" in outcome.course_title


def test_a_course_can_be_named_separately_from_the_question(conn):
    outcome = ask(conn, AnsweringLLM(), question="What is consent?", course_ref="Medical Law")

    assert outcome.course_code == "LL-45165"


def test_a_course_reference_that_matches_nothing_widens_the_search_rather_than_failing(conn):
    outcome = ask(conn, AnsweringLLM(), question="Which OSI layer routes packets?", course_ref="Nope")

    assert outcome.course_code is None
    assert outcome.answer


@pytest.mark.parametrize("question", ["", "   ", None])
def test_an_empty_question_is_refused_before_any_model_is_called(conn, question):
    llm = AnsweringLLM()

    with pytest.raises(QGenError):
        ask(conn, llm, question=question)

    assert llm.prompts == []


def test_an_absurdly_long_question_is_refused(conn):
    with pytest.raises(QGenError):
        ask(conn, AnsweringLLM(), question="duty " * 400)


def test_a_provider_outage_keeps_its_status(conn):
    with pytest.raises(GenerationUnavailable) as caught:
        ask(conn, AnsweringLLM(GenerationUnavailable(reason="HTTP_429")), question="What is duty?")

    assert caught.value.status_code == 503


def test_nothing_is_written_by_asking(conn):
    """Asking reads. It does not store the question, the answer, or anything else."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM qgen_questions")
        before = cur.fetchone()["n"]

    ask(conn, AnsweringLLM(), question="Which OSI layer routes packets?")

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM qgen_questions")
        assert cur.fetchone()["n"] == before


# ---------------------------------------------------------------------------
# The website fallback - second, never first
# ---------------------------------------------------------------------------


def test_the_site_is_not_consulted_when_the_database_has_material(conn, monkeypatch):
    """The database holds material written for these courses. Going to the website first would
    answer from marketing copy when a written explanation was available."""
    called = []
    monkeypatch.setattr(
        "qgen.website.find_material", lambda *a, **k: called.append(1) or []
    )

    outcome = ask(
        conn,
        AnsweringLLM(answer_reply("Grounded.", (1,))),
        question="Which OSI layer routes packets between networks?",
        site_search="https://example.com/?s={query}",
    )

    assert outcome.source == "database"
    assert called == []


def test_the_site_is_consulted_when_the_database_has_nothing(conn, monkeypatch):
    from qgen.domain.answering import Snippet

    monkeypatch.setattr(
        "qgen.website.find_material",
        lambda *a, **k: [
            Snippet(
                source="the course website",
                course="https://example.com/?s=photosynthesis",
                text="Photosynthesis in chloroplasts converts light energy into chemical energy, "
                "storing it in glucose, and this passage is long enough to be treated as real "
                "content rather than as navigation furniture on the page.",
            )
        ],
    )

    outcome = ask(
        conn,
        AnsweringLLM(answer_reply("From the site.", (1,))),
        question="Explain photosynthesis in chloroplasts",
        site_search="https://example.com/?s={query}",
    )

    assert outcome.source == "website"
    assert outcome.material
    assert "Not in the course material" in outcome.provenance
    assert "example.com" in outcome.provenance


def test_with_no_site_configured_nothing_is_fetched(conn, monkeypatch):
    called = []
    monkeypatch.setattr("qgen.website.find_material", lambda *a, **k: called.append(1) or [])

    outcome = ask(conn, AnsweringLLM(), question="Explain photosynthesis in chloroplasts")

    assert called == []
    assert outcome.source == "database"
    assert "no course material on this" in outcome.provenance


def test_a_site_that_has_nothing_either_falls_back_to_saying_so(conn, monkeypatch):
    monkeypatch.setattr("qgen.website.find_material", lambda *a, **k: [])

    outcome = ask(
        conn,
        AnsweringLLM(),
        question="Explain photosynthesis in chloroplasts",
        site_search="https://example.com/?s={query}",
    )

    assert outcome.material == ()
    assert outcome.source == "database"
