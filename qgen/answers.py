"""Asking a question and getting an answer back, live.

Four steps, and the order is the point: work out which course is being asked about, find the
material, put the material in front of the model, and report what the answer rested on.

Nothing is generated ahead of time and nothing is stored. A question is asked, the database is
read, the model is called, the answer comes back. Ask the same thing twice and it is asked twice.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from . import catalogue, legal, library, website
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

#: The reference database is searched on every question, not only when the course material is
#: empty, and the two are merged and ranked together.
#:
#: Two thresholds were tried and both were wrong. Searching only on *zero* course material meant
#: "How long do I have to bring a defamation claim?" never reached it, because a handful of
#: loosely related course questions counted as coverage - while the reference database holds the
#: exact answer, "Defamation: 1 year, s.4A Limitation Act 1980". Raising the threshold just moved
#: the line to a different set of questions it got wrong.
#:
#: Judging relevance before the ranking has run is guesswork. The search costs about a second,
#: the ranking already decides what earns a place in the prompt, and letting it see everything is
#: both simpler and better. If that second ever matters, cache it - do not reintroduce a guess.


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
    def cited(self) -> tuple[Snippet, ...]:
        """The material the model actually said it used."""
        return tuple(self.material[index - 1] for index in self.used)

    @property
    def provenance(self) -> str:
        """One sentence for the reader. Never claims more than happened.

        Derived from what was **cited**, never from which searches happened to run. An earlier
        version set a flag when the reference database was merged into the material, and then
        reported an answer built entirely from course questions as having come from the
        reference database. Where an answer came from is the one thing on this page that has to
        be true, so it is read back off the citations rather than tracked alongside them.
        """
        if self.citations_unavailable:
            return (
                f"{len(self.material)} items were used to write this, but the model did not say "
                "which."
            )
        if self.used:
            kinds = ", ".join(sorted({snippet.source for snippet in self.cited}))
            courses = sorted({s.course for s in self.cited if s.course})
            where = f" - {', '.join(courses)}" if courses else ""
            return f"Answered from {len(self.used)} items of {kinds}{where}."

        # Nothing was cited. Say what was looked in, so "we have nothing on this" is separable
        # from "we did not look".
        searched = ", ".join(sorted({snippet.source for snippet in self.material}))
        if searched:
            return (
                f"Nothing in what was found ({searched}) covered this, so the answer is general "
                "subject knowledge."
            )
        if self.course_title:
            return (
                f"{self.course_title} holds nothing on this. The answer is general subject "
                "knowledge, and no other course was substituted for it."
            )
        return "Nothing in the material covers this. The answer is general subject knowledge."


def ask(
    conn: psycopg.Connection,
    llm: QuestionLLM,
    *,
    question: str,
    course_ref: str | None = None,
    site_search: str = "",
    legal_source: legal.LegalSource | None = None,
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

    if legal_source is not None and legal_source.configured:
        # The company's legal reference database. Searched second and merged, not substituted:
        # the course material is written for these courses and keeps its place, while a precise
        # statutory answer the courses do not carry is worth having alongside it.
        from_legal = legal.find_material(legal_source, terms)
        if from_legal:
            merged = rank([*found, *from_legal], terms, limit=MAX_SNIPPETS)
            if any(s in [x.source for x in merged] for s in ("limitation periods", "legal guidance")):
                source = "legal database"
            material = merged

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
