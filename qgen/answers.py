"""Asking a question and getting an answer back, live.

Four steps, and the order is the point: work out which course is being asked about, find the
material, put the material in front of the model, and report what the answer rested on.

Nothing is generated ahead of time and nothing is stored. A question is asked, the database is
read, the model is called, the answer comes back. Ask the same thing twice and it is asked twice.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from . import catalogue, library, website
from .domain.answering import (
    MAX_SNIPPETS,
    Snippet,
    build_answer_prompt,
    keywords,
    parse_answer,
    rank,
)
from .domain.replies import collapse
from .errors import GenerationFailed, InvalidRequest
from .llm import QuestionLLM

#: The output budget for one answer. Six sentences and a short citation list; the ceiling exists
#: so a runaway reply cannot cost a fortune, not to shape the answer.
ANSWER_MAX_TOKENS = 1200


@dataclass(frozen=True, slots=True)
class AskOutcome:
    """The answer, and everything needed to say honestly where it came from."""

    question: str
    answer: str
    #: The course the question was read as being about, when it named one.
    course_code: str | None
    course_title: str | None
    #: The material put in front of the model, best first.
    material: tuple[Snippet, ...]
    #: Which of it the model said it used (1-based, into ``material``).
    used: tuple[int, ...]
    citations_unavailable: bool
    #: Where the material came from: "database", "website", or "nothing".
    source: str = "database"

    @property
    def grounded(self) -> bool:
        """Whether the answer rests on material from the courses."""
        return bool(self.used)

    @property
    def provenance(self) -> str:
        """One sentence for the reader. Never claims more than happened."""
        # The website case is tested first, and that ordering is the point. Material fetched
        # from the site is not course material, and reporting it as such would tell a reader
        # their answer came from the syllabus when it came from a web page.
        if self.source == "website" and self.material:
            where = self.material[0].course or "the course website"
            if self.used:
                return f"Not in the course material. Answered from the course website ({where})."
            return (
                f"Not in the course material. The course website was consulted ({where}) but "
                "did not cover it either, so the answer is general subject knowledge."
            )
        if self.citations_unavailable:
            return (
                f"{len(self.material)} items from the course library were used to write this, "
                "but the model did not say which."
            )
        if self.used:
            courses = sorted(
                {
                    self.material[index - 1].course
                    for index in self.used
                    if self.material[index - 1].course
                }
            )
            where = f" from {', '.join(courses)}" if courses else ""
            return f"Answered from {len(self.used)} items of course material{where}."
        if self.material:
            return (
                "The course material found did not cover this, so the answer is general "
                "subject knowledge."
            )
        if self.course_title:
            # Named, because "no material" and "no material *in the course you asked about*"
            # are different facts, and only the second one tells you to widen the question.
            return (
                f"{self.course_title} holds nothing on this. The answer is general subject "
                "knowledge, and no other course was substituted for it."
            )
        return "There is no course material on this. The answer is general subject knowledge."


def ask(
    conn: psycopg.Connection,
    llm: QuestionLLM,
    *,
    question: str,
    course_ref: str | None = None,
    site_search: str = "",
) -> AskOutcome:
    """Answer one question, live.

    ``course_ref`` narrows the search when the asker has said which course they mean. When they
    have not, the question itself is checked for a course name, and failing that the whole
    library is searched - which is the right behaviour for "what is mens rea?", a question that
    belongs to several courses at once.
    """
    text = collapse(question)
    if not text:
        raise InvalidRequest("no question was asked", reason="NO_QUESTION")
    if len(text) > 1000:
        # A ceiling, not a judgement: a thousand characters is a long question, and beyond it
        # the keyword search matches everything and ranks nothing.
        raise InvalidRequest(
            "the question is too long (1000 characters)", reason="QUESTION_TOO_LONG"
        )

    code, title = _course_for(conn, text, course_ref)

    terms = keywords(text)
    found = library.find_material(conn, terms, course_title=title, course_code=code)
    material = rank(found, terms, limit=MAX_SNIPPETS)
    source = "database"

    if not material and site_search:
        # Only now. The database holds material written for these courses; the website is for
        # what it has not got, and consulting it first would answer from marketing copy when a
        # written explanation was available.
        from_site = website.find_material(text, search_template=site_search)
        material = rank(from_site, terms, limit=MAX_SNIPPETS)
        if material:
            source = "website"

    reply = llm.complete(
        build_answer_prompt(text, material, course=title),
        max_tokens=ANSWER_MAX_TOKENS,
    )
    answer = parse_answer(reply, material)
    if answer is None:
        # The model answered with nothing. Not repaired into "I don't know" - that would be this
        # package putting words in its mouth and the asker could not tell the difference.
        raise GenerationFailed(reason="NO_ANSWER_TEXT")

    return AskOutcome(
        question=text,
        answer=answer.text,
        course_code=code,
        course_title=title,
        material=tuple(material),
        used=answer.used,
        citations_unavailable=answer.citations_unavailable,
        source=source,
    )


def _course_for(
    conn: psycopg.Connection, question: str, course_ref: str | None
) -> tuple[str | None, str | None]:
    """The course this question is about: the one named by the asker, or the one it mentions."""
    if course_ref and course_ref.strip():
        matched = catalogue.find_course(conn, course_ref)
        if matched is not None:
            return matched.code, matched.title
        # An unresolved course reference is not fatal. The question still stands, and the search
        # simply widens to the whole library rather than being pinned to a course that does not
        # exist.
        return None, None
    guessed = library.guess_course(conn, question)
    return guessed if guessed else (None, None)
