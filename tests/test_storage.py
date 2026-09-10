"""Against a real PostgreSQL: the catalogue read, storage, and the whole run end to end.

Every test here runs in a rolled-back transaction, so the catalogue is read but never changed
and nothing this file writes survives it.

Three properties earn most of the file: the question is frozen onto its own row, the answer key
never reaches a learner view, and a generated question is a draft.
"""

from __future__ import annotations

import json

import pytest

from qgen import catalogue, storage
from qgen.domain.generation import CourseBrief, GeneratedOption, GeneratedQuestion
from qgen.errors import GenerationFailed, GenerationUnavailable, QGenError
from qgen.service import generate
from tests.fakes import FakeLLM, questions_reply

CRIMINOLOGY = "LL-34590"


@pytest.fixture
def db(conn):
    """This package's tables, present and empty, inside the rolled-back transaction.

    Emptied because these tests are about history: what a course has already been asked changes
    how much is asked for and what is refused. A suite that only passes against a database
    nobody has generated into yet is a suite that stops passing the first time somebody uses the
    thing - which is exactly what happened here, on the first live run.

    The delete is rolled back with everything else, so the real questions are untouched. One
    statement is enough: questions cascade from runs, and options from questions.
    """
    storage.ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM qgen_runs")
    return conn


def question(text: str = "Which limitation period applies?", answer: str = "B"):
    return GeneratedQuestion(
        question_text=text,
        options=tuple(
            GeneratedOption(label=label, text=f"option {label}", is_correct=label == answer)
            for label in ("A", "B", "C", "D")
        ),
        explanation="Because the Act says so.",
    )


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


def test_the_catalogue_can_be_listed(conn):
    courses = catalogue.list_courses(conn)

    assert len(courses) >= 33
    assert any(course.code == CRIMINOLOGY for course in courses)
    # Ordered by title, because a person choosing a course reads the name. Compared
    # case-insensitively: PostgreSQL's collation puts "Chemerinsky" before "CORPORATE" and
    # Python's default sort, which compares code points, does not.
    titles = [c.title for c in courses]
    assert titles == sorted(titles, key=str.casefold)


@pytest.mark.parametrize(
    "reference",
    ["LL-34590", "ll-34590", "  LL-34590 ", "Criminology (Postgraduate)", "criminology"],
    ids=["code", "lower-code", "padded-code", "exact-title", "unique-partial"],
)
def test_a_course_resolves_by_code_by_title_and_by_a_unique_fragment(conn, reference):
    course = catalogue.find_course(conn, reference)

    assert course is not None
    assert course.code == CRIMINOLOGY


@pytest.mark.parametrize("reference", ["International", "Law", "", "   ", "no such course here"])
def test_an_ambiguous_or_missing_reference_resolves_to_nothing(conn, reference):
    """Four courses contain "International". Picking one produces a confident paper about the
    wrong syllabus with nothing in the output to show it happened."""
    assert catalogue.find_course(conn, reference) is None


def test_a_percent_sign_does_not_match_every_course(conn):
    """Unescaped, this is a LIKE wildcard and the lookup reports an ambiguity that is real but
    entirely of its own making."""
    assert catalogue.find_course(conn, "100%") is None


def test_an_unmatched_reference_still_produces_a_brief_that_admits_it(conn):
    brief = catalogue.brief_for("Roman Water Law", None)

    assert brief.code is None
    assert brief.name == "Roman Water Law"
    assert "no course matched" in brief.grounding


def test_a_matched_course_with_no_description_says_so_in_its_grounding(conn):
    """True of all 33 rows today, and the report must not imply otherwise."""
    course = catalogue.find_course(conn, CRIMINOLOGY)

    brief = catalogue.brief_for(CRIMINOLOGY, course)

    assert brief.code == CRIMINOLOGY
    if course.description is None:
        assert brief.grounding == "the course name only (the catalogue row has no description)"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def test_the_schema_can_be_created_twice(db):
    storage.ensure_schema(db)  # the fixture already did it once


def test_a_stored_question_carries_its_own_stem_options_and_key(db):
    brief = CourseBrief(code=CRIMINOLOGY, name="Criminology (Postgraduate)")
    run_id = storage.open_run(
        db, course_ref="Criminology", brief=brief, requested=1, asked_for=1, model="test"
    )

    ids = storage.store_questions(
        db, run_id=run_id, course_ref="Criminology", brief=brief, questions=(question(),)
    )
    stored = storage.read_questions(db, ids)[0]

    # Frozen: the row holds the text, not a pointer at something editable. If a question could be
    # edited later, every past result would silently change meaning.
    assert stored.question_text == "Which limitation period applies?"
    assert [option.text for option in stored.options] == [f"option {x}" for x in "ABCD"]
    assert stored.answer_label == "B"
    assert [option.is_correct for option in stored.options] == [False, True, False, False]


def test_every_generated_question_is_stored_as_a_draft(db):
    brief = CourseBrief(code=CRIMINOLOGY, name="Criminology")
    run_id = storage.open_run(
        db, course_ref="x", brief=brief, requested=1, asked_for=1, model="test"
    )

    ids = storage.store_questions(
        db, run_id=run_id, course_ref="x", brief=brief, questions=(question(),)
    )

    assert storage.read_questions(db, ids)[0].status == "DRAFT"


def test_the_learner_view_contains_no_answer_key_anywhere(db):
    brief = CourseBrief(code=CRIMINOLOGY, name="Criminology")
    run_id = storage.open_run(
        db, course_ref="x", brief=brief, requested=1, asked_for=1, model="test"
    )
    ids = storage.store_questions(
        db, run_id=run_id, course_ref="x", brief=brief, questions=(question(),)
    )
    stored = storage.read_questions(db, ids)[0]

    view = storage.learner_view(stored)

    # Serialised and searched rather than checked key by key, so a field added later cannot leak
    # the key past this test.
    serialised = json.dumps(view)
    assert "is_correct" not in serialised
    assert "answer" not in serialised
    assert "explanation" not in serialised
    assert len(view["options"]) == 4


def test_the_admin_view_does_carry_the_key(db):
    brief = CourseBrief(code=CRIMINOLOGY, name="Criminology")
    run_id = storage.open_run(
        db, course_ref="x", brief=brief, requested=1, asked_for=1, model="test"
    )
    ids = storage.store_questions(
        db, run_id=run_id, course_ref="x", brief=brief, questions=(question(),)
    )

    view = storage.admin_view(storage.read_questions(db, ids)[0])

    assert view["answer"] == "B"
    assert view["status"] == "DRAFT"
    assert [option["is_correct"] for option in view["options"]] == [False, True, False, False]


def test_history_comes_back_newest_first_and_capped(db):
    brief = CourseBrief(code=CRIMINOLOGY, name="Criminology")
    run_id = storage.open_run(
        db, course_ref="x", brief=brief, requested=5, asked_for=5, model="test"
    )
    storage.store_questions(
        db,
        run_id=run_id,
        course_ref="x",
        brief=brief,
        questions=tuple(question(text=f"Question {n}?") for n in range(6)),
    )

    stems = storage.previously_asked(db, brief, limit=3)

    assert stems == ("Question 5?", "Question 4?", "Question 3?")


def test_history_is_kept_apart_from_other_courses(db):
    mine = CourseBrief(code=CRIMINOLOGY, name="Criminology")
    theirs = CourseBrief(code="LL-45165", name="Medical Law")
    run_id = storage.open_run(
        db, course_ref="x", brief=mine, requested=1, asked_for=1, model="test"
    )
    storage.store_questions(
        db, run_id=run_id, course_ref="x", brief=mine, questions=(question(text="Mine?"),)
    )

    assert storage.previously_asked(db, theirs) == ()


def test_two_unmatched_references_do_not_share_a_history(db):
    """Otherwise every unmatched request lands in one bucket and two unrelated subjects are each
    told to avoid the other's questions."""
    one = CourseBrief(code=None, name="Roman Water Law")
    two = CourseBrief(code=None, name="Napoleonic Tax Law")
    run_id = storage.open_run(
        db, course_ref="Roman Water Law", brief=one, requested=1, asked_for=1, model="test"
    )
    storage.store_questions(
        db,
        run_id=run_id,
        course_ref="Roman Water Law",
        brief=one,
        questions=(question(text="Aqueducts?"),),
    )

    assert storage.previously_asked(db, one) == ("Aqueducts?",)
    assert storage.previously_asked(db, two) == ()


# ---------------------------------------------------------------------------
# The whole run
# ---------------------------------------------------------------------------


def test_a_run_resolves_the_course_stores_drafts_and_reports_the_match(db):
    outcome = generate(db, FakeLLM(questions_reply("a?", "b?")), course_ref="Criminology", count=2)

    assert outcome.matched_code == CRIMINOLOGY
    assert outcome.stored == 2
    assert outcome.short is False
    stored = storage.read_questions(db, outcome.question_ids)
    assert all(q.status == "DRAFT" for q in stored)
    assert all(q.course_code == CRIMINOLOGY for q in stored)


def test_a_run_for_an_unmatched_reference_records_no_code_and_says_where_it_came_from(db):
    outcome = generate(db, FakeLLM(questions_reply("a?")), course_ref="Roman Water Law", count=1)

    assert outcome.matched_code is None
    assert "no course matched" in outcome.grounding


def test_a_surplus_beyond_what_was_asked_for_is_discarded(db):
    outcome = generate(
        db, FakeLLM(questions_reply("a?", "b?", "c?", "d?")), course_ref="Criminology", count=2
    )

    assert outcome.stored == 2


def test_a_second_run_is_told_what_the_first_one_asked(db):
    llm = FakeLLM(questions_reply("first?"), questions_reply("second?"))

    generate(db, llm, course_ref="Criminology", count=1)
    outcome = generate(db, llm, course_ref="Criminology", count=1)

    assert outcome.stored == 1
    assert storage.read_questions(db, outcome.question_ids)[0].question_text == "second?"


def test_a_repeat_of_an_earlier_run_is_dropped_rather_than_stored_twice(db):
    llm = FakeLLM(questions_reply("same?"), questions_reply("same?"))

    generate(db, llm, course_ref="Criminology", count=1)

    with pytest.raises(GenerationFailed):
        # Everything the second call returned was a repeat, so nothing survived the parse.
        generate(db, llm, course_ref="Criminology", count=1)


def test_a_course_with_history_is_asked_for_more_than_is_needed(db):
    llm = FakeLLM(questions_reply("first?"))
    generate(db, llm, course_ref="Criminology", count=1)

    outcome = generate(db, FakeLLM(questions_reply("a?", "b?")), course_ref="Criminology", count=1)

    assert outcome.asked_for > outcome.requested


def test_a_short_run_is_reported_as_short_and_not_rounded_up(db):
    outcome = generate(db, FakeLLM(questions_reply("only one?")), course_ref="Criminology", count=5)

    assert outcome.stored == 1
    assert outcome.short is True
    assert outcome.requested == 5


def test_a_throttle_is_reported_as_a_provider_problem_not_as_a_question_problem(db):
    llm = FakeLLM(
        questions_reply(*[f"q{n}?" for n in range(10)]),
        GenerationUnavailable(reason="HTTP_429"),
    )

    outcome = generate(db, llm, course_ref="Criminology", count=20)

    assert outcome.throttled is True
    assert outcome.stored == 10
    assert outcome.short is True


def test_when_the_model_cannot_be_reached_at_all_the_error_keeps_its_status(db):
    with pytest.raises(GenerationUnavailable) as caught:
        generate(db, FakeLLM(GenerationUnavailable(reason="HTTP_429")), course_ref="Criminology", count=1)

    assert caught.value.status_code == 503


def test_when_the_model_returns_nothing_usable_nothing_is_written(db):
    """Not even the run row survives.

    ``generate`` opens the run before it calls the model, and leaves the undoing to the caller's
    rollback - :func:`qgen.db.connect` in production, this savepoint here. A half-written run
    looks exactly like a complete short one to whoever reads the table next.
    """
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM qgen_runs")
        runs_before = cur.fetchone()["n"]

    with pytest.raises(GenerationFailed), db.transaction():
        generate(db, FakeLLM("not json at all"), course_ref="Criminology", count=3)

    assert storage.count_for_course(db, CRIMINOLOGY) == 0
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM qgen_runs")
        assert cur.fetchone()["n"] == runs_before


@pytest.mark.parametrize("count", [0, -1, 51, 1000])
def test_an_impossible_count_is_refused_before_any_model_is_called(db, count):
    llm = FakeLLM()

    with pytest.raises(QGenError):
        generate(db, llm, course_ref="Criminology", count=count)

    assert llm.calls == 0


def test_the_run_row_records_what_was_stored_and_why_the_rest_was_not(db):
    broken = json.dumps(
        {"questions": [{"question": "a?", "options": {"A": "1"}, "answer": "Z"}]}
    )
    outcome = generate(db, FakeLLM(questions_reply("good?"), broken), course_ref="Criminology", count=20)

    with db.cursor() as cur:
        cur.execute("SELECT * FROM qgen_runs WHERE id = %s", (outcome.run_id,))
        run = cur.fetchone()

    assert run["stored"] == 1
    assert run["course_code"] == CRIMINOLOGY
    assert run["status"] == "SHORT"
    assert "option" in (run["refusals"] or "")


def test_the_catalogue_reads_a_table_that_lacks_the_later_columns(conn):
    """The same table exists in two shapes, and the deployed one is the older.

    Selecting a column the table has not got fails the whole query with UndefinedColumn - which
    is what the first Railway deploy did. This proves the read adapts instead.
    """
    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE qc_courses_backup AS SELECT * FROM qc_courses")
        cur.execute("ALTER TABLE qc_courses DROP COLUMN description")
        cur.execute("ALTER TABLE qc_courses DROP COLUMN rqf_level")
        cur.execute("ALTER TABLE qc_courses DROP COLUMN subject_area")

    courses = catalogue.list_courses(conn)
    matched = catalogue.find_course(conn, CRIMINOLOGY)

    assert len(courses) >= 33
    assert matched is not None and matched.code == CRIMINOLOGY
    # Absent, not invented.
    assert matched.description is None
    assert matched.rqf_level is None
    # And the brief still says honestly where the questions would come from.
    assert "no description" in catalogue.brief_for(CRIMINOLOGY, matched).grounding
