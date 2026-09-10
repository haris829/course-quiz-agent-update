"""The HTTP surface, and the page that uses it.

Two things a person can do - ask a question and be tested on a course - plus the reads behind
them and the generation route from the original brief.

**The answer key never travels with a question.** Every route that hands out a question hands out
stem and options and nothing else. The key lives on the server and one route reveals it,
``/api/practice/answer``, and only in reply to an answer already committed. A page that received
the key up front would be one "view source" away from being an answer sheet.

That still makes the marking route an oracle if you submit every option in turn, which is fine
for a local authoring tool and would not be for a certificate. A real sitting needs an attempt
with one submission per question - the platform already has that in UC-03, and this is
deliberately not a second one.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import catalogue, legal, practice, storage
from .answers import ask
from .config import Settings, load_settings
from .db import connect
from .domain.generation import MAX_QUESTIONS_PER_REQUEST
from .errors import GenerationFailed, InvalidRequest, QGenError
from .llm import QuestionLLM, build_llm
from .service import generate

STATIC = Path(__file__).resolve().parent / "static"


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    #: Optional. The question is usually enough to say which course it belongs to, and plenty of
    #: questions belong to several.
    course: str | None = Field(default=None, max_length=200)


class PracticeRequest(BaseModel):
    course: str = Field(min_length=1, max_length=200)
    #: What this sitting has already been shown. Held by the page, not in a table: it is the
    #: state of one screen and it means nothing an hour later.
    exclude: list[str] = Field(default_factory=list, max_length=500)


class MarkRequest(BaseModel):
    ref: str = Field(min_length=3, max_length=40)
    label: str = Field(min_length=1, max_length=4)


class GenerateRequest(BaseModel):
    course: str = Field(min_length=1, max_length=200)
    count: int = Field(default=5, ge=1, le=MAX_QUESTIONS_PER_REQUEST)


def create_app(settings: Settings | None = None, llm: QuestionLLM | None = None) -> FastAPI:
    """The application.

    ``settings`` and ``llm`` are arguments rather than module globals so the tests can run the
    real routes against a fake model. Nothing else about the app changes between the two.
    """
    resolved = settings or load_settings()
    model = llm or build_llm(resolved)

    app = FastAPI(title="qgen", description="Question generation from the course catalogue")

    def db():
        with connect(resolved.database_url) as conn:
            storage.ensure_schema(conn)
            yield conn

    @app.exception_handler(QGenError)
    def _qgen_error(request, exc: QGenError):
        # The status is the operational fact: 503 means wait, 502 means read the prompt. The
        # reason token goes with it; the provider's own body never does.
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "retryable": exc.retryable},
        )

    @app.post("/api/ask")
    def ask_question(body: AskRequest, conn=Depends(db)):
        """Answer one question, live, from the material in the database.

        Nothing is generated in advance and nothing is stored. The material the model was given
        is returned alongside the answer, so "grounded in the course" is something the reader
        can check rather than take on trust.
        """
        outcome = ask(
            conn, model, question=body.question, course_ref=body.course,
            site_search=resolved.site_search,
            legal_source=legal.LegalSource(
                host=resolved.legal_host, port=resolved.legal_port,
                database=resolved.legal_database, user=resolved.legal_user,
                password=resolved.legal_password,
            ),
        )
        return {
            "question": outcome.question,
            "answer": outcome.answer,
            "course": outcome.course_title,
            "course_code": outcome.course_code,
            "grounded": outcome.grounded,
            "source": outcome.source,
            "provenance": outcome.provenance,
            "used": list(outcome.used),
            "material": [
                {
                    "n": index,
                    "source": snippet.source,
                    "course": snippet.course,
                    "text": snippet.text,
                    "explanation": snippet.explanation,
                    "used": index in outcome.used,
                }
                for index, snippet in enumerate(outcome.material, start=1)
            ],
        }

    @app.post("/api/practice")
    def practice_question(body: PracticeRequest, conn=Depends(db)):
        """One question from the named course. A different one each time it is called.

        Strictly that course. When its questions are exhausted, a fresh one is written live for
        it - never borrowed from a neighbouring course, because nothing on the page could then
        tell the reader which course they were being tested on.
        """
        matched = catalogue.find_course(conn, body.course)
        if matched is None:
            raise InvalidRequest(
                f"No single course matches {body.course!r}. "
                "Four courses contain 'International', for instance - name one of them.",
                reason="COURSE_NOT_RESOLVED",
            )

        draw = practice.pick(
            conn, code=matched.code, title=matched.title, exclude=set(body.exclude)
        )
        question, pool = draw.question, draw.pool
        generated = False
        if question is None:
            # The course is used up. Writing a new one keeps the pool growing and keeps the
            # question inside the course; it is stored as a draft like every other.
            outcome = generate(
                conn, model, course_ref=matched.code, count=1, model_name=resolved.llm_model
            )
            if not outcome.question_ids:
                raise GenerationFailed(reason="NO_QUESTION_GENERATED")
            question = practice.read(
                conn, f"{practice.OWN}-{outcome.question_ids[0]}", matched.title
            )
            generated = True
            pool += 1  # the one just written

        return {
            "course": matched.title,
            "course_code": matched.code,
            "remaining": draw.remaining,
            "generated": generated,
            "pool": pool,
            **question.as_dict(),
        }

    @app.post("/api/practice/answer")
    def practice_answer(body: MarkRequest, conn=Depends(db)):
        """Mark one practice answer. The key is read here and nowhere else."""
        result = practice.mark(conn, body.ref, body.label)
        if result is None:
            raise HTTPException(status_code=404, detail="no such question")
        return {
            "correct": result.correct,
            "answer": result.answer,
            "explanation": result.explanation,
            "status": result.status,
        }

    @app.get("/api/courses")
    def courses(conn=Depends(db)):
        """The catalogue, with what each course already holds.

        ``has_description`` is the one fact that matters when choosing: today it is false on
        every row, and the page says so rather than letting somebody assume otherwise.
        """
        return [
            {
                "code": course.code,
                "title": course.title,
                "has_description": bool(course.description),
                "generated": storage.count_for_course(conn, course.code),
            }
            for course in catalogue.list_courses(conn)
        ]

    @app.get("/api/questions")
    def existing(course: str, limit: int = 20, conn=Depends(db)):
        """What this topic already has, without calling a model.

        The default action on the page, because reading back what exists costs nothing and
        generating costs money and a minute.
        """
        matched = catalogue.find_course(conn, course)
        brief = catalogue.brief_for(course, matched)
        held = storage.questions_for_course(
            conn, code=brief.code, ref=brief.name, limit=max(1, min(limit, 100))
        )
        return {
            "matched": brief.code,
            "title": brief.name,
            "grounding": brief.grounding,
            "questions": [storage.learner_view(question) for question in held],
        }

    @app.post("/api/generate")
    def create(body: GenerateRequest, conn=Depends(db)):
        outcome = generate(
            conn,
            model,
            course_ref=body.course,
            count=body.count,
            model_name=resolved.llm_model,
        )
        stored = storage.read_questions(conn, outcome.question_ids)
        return {
            "matched": outcome.matched_code,
            "title": outcome.course_title,
            "grounding": outcome.grounding,
            "requested": outcome.requested,
            "asked_for": outcome.asked_for,
            "stored": outcome.stored,
            "refused": outcome.refused,
            # Reported, not hidden: a low yield has to be diagnosable from the page.
            "refusals": [{"reason": reason, "count": n} for reason, n in outcome.refusals],
            "provider_failures": [
                {"reason": reason, "count": n} for reason, n in outcome.batch_failures
            ],
            "throttled": outcome.throttled,
            "short": outcome.short,
            "questions": [storage.learner_view(question) for question in stored],
        }

    @app.get("/api/health")
    def health():
        """Whether a model is configured, so the page can say so before anyone waits on it."""
        return {"model_configured": model.configured}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app
