"""The command line: list the catalogue, create the tables, generate for a course.

Everything printed after a run is **read back out of the database**, not echoed from what the
model returned. The two differ - repeats are dropped, a surplus is discarded, a batch can fail -
and the number that matters is the one that was stored.
"""

from __future__ import annotations

import argparse
import sys

from . import catalogue, storage
from .config import load_settings
from .db import connect
from .domain.generation import MAX_QUESTIONS_PER_REQUEST
from .errors import QGenError
from .llm import build_llm
from .service import generate


def _courses(args: argparse.Namespace) -> int:
    settings = load_settings()
    with connect(settings.database_url) as conn:
        storage.ensure_schema(conn)
        courses = catalogue.list_courses(conn)
        print(f"{len(courses)} courses in qc_courses\n")
        for course in courses:
            held = storage.count_for_course(conn, course.code)
            # A course with no description generates from its title alone, which is the single
            # most useful thing to know when choosing one, so it is flagged in the listing.
            brief = "description" if course.description else "name only"
            print(f"  {course.code:<12} {course.title}")
            print(f"  {'':<12} generated: {held:<4} source: {brief}")
    return 0


def _init_db(args: argparse.Namespace) -> int:
    settings = load_settings()
    with connect(settings.database_url) as conn:
        storage.ensure_schema(conn)
    print("qgen_runs, qgen_questions, qgen_question_options are present.")
    return 0


def _generate(args: argparse.Namespace) -> int:
    settings = load_settings()
    llm = build_llm(settings)
    if not llm.configured:
        # Said before anything is attempted, rather than as a 503 thirty seconds in.
        print(
            "No model is configured (COACHING_LLM_PROVIDER / _API_KEY / _MODEL).\n"
            "Nothing will be generated and nothing will be written.",
            file=sys.stderr,
        )
        return 3

    with connect(settings.database_url) as conn:
        outcome = generate(
            conn,
            llm,
            course_ref=args.course,
            count=args.count,
            model_name=settings.llm_model,
        )
        stored = storage.read_questions(conn, outcome.question_ids)

        matched = outcome.matched_code or "no course matched"
        print(f"\nCourse reference: {outcome.course_ref!r}")
        print(f"Matched:          {matched}")
        print(f"Title used:       {outcome.course_title}")
        print(f"Written from:     {outcome.grounding}")
        print(f"Asked the model for {outcome.asked_for}; requested {outcome.requested}.")
        print(f"Stored:           {outcome.stored}  (status DRAFT, unreviewed)")
        if outcome.refusals:
            print(f"Refused:          {outcome.refused}")
            for reason, count in outcome.refusals:
                print(f"                    {count} x {reason}")
        if outcome.batch_failures:
            for reason, count in outcome.batch_failures:
                print(f"Provider:         {count} x {reason}")
            if outcome.throttled:
                print("                  (throttled - not a fault in the questions)")
        if outcome.short:
            print(
                f"Short by {outcome.requested - outcome.stored}: "
                f"{outcome.stored} stored against {outcome.requested} requested."
            )

        print()
        for index, question in enumerate(stored, start=1):
            view = storage.admin_view(question) if args.answers else storage.learner_view(question)
            print(f"{index}. [{question.id}] {view['question']}")
            for option in view["options"]:
                mark = " *" if args.answers and option.get("is_correct") else ""
                print(f"     {option['label']}. {option['text']}{mark}")
            if args.answers:
                print(f"     answer: {view['answer']} - {view['explanation'] or ''}")
            print()

        if not args.answers and stored:
            print("(answer key withheld; --answers shows it)")
    return 0


def _export_reference(args: argparse.Namespace) -> int:
    from .seed import DEFAULT_PATH, export

    settings = load_settings()
    with connect(settings.database_url) as conn:
        counts = export(conn, DEFAULT_PATH)
    print(f"written to {DEFAULT_PATH}")
    for table, n in counts.items():
        print(f"  {table:<24} {n} rows")
    return 0


def _load_reference(args: argparse.Namespace) -> int:
    from .seed import DEFAULT_PATH, load

    settings = load_settings()
    with connect(settings.database_url) as conn:
        report = load(conn, DEFAULT_PATH)
    print("loaded into this database:")
    for table, note in report.items():
        print(f"  {table:<24} {note}")
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .web import create_app

    # Built before the server starts, so a missing database URL is a message here rather than a
    # 500 on the first request.
    app = create_app(load_settings())
    print(f"http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m qgen",
        description="Generate multiple-choice questions from the courses in qc_courses.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("courses", help="list the catalogue").set_defaults(run=_courses)
    sub.add_parser("init-db", help="create this package's tables").set_defaults(run=_init_db)
    sub.add_parser(
        "export-reference", help="write the courses and question bank to reference_data.json.gz"
    ).set_defaults(run=_export_reference)
    sub.add_parser(
        "load-reference", help="load reference_data.json.gz into this database (additive)"
    ).set_defaults(run=_load_reference)

    run = sub.add_parser("generate", help="generate questions for one course")
    run.add_argument("--course", required=True, help="course code or name, e.g. 'Criminology'")
    run.add_argument(
        "--count", type=int, default=5, help=f"1-{MAX_QUESTIONS_PER_REQUEST} (default 5)"
    )
    run.add_argument(
        "--answers",
        action="store_true",
        help="print the answer key (administrator view; withheld by default)",
    )
    run.set_defaults(run=_generate)

    serve = sub.add_parser("serve", help="the page, at http://127.0.0.1:8010")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8010)
    serve.set_defaults(run=_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.run(args))
    except QGenError as exc:
        # The status code is printed because it is the operational fact: 503 means wait and try
        # again, 502 means somebody has to look at the prompt.
        print(f"\n{exc.status_code} {exc.code}: {exc.message}", file=sys.stderr)
        if exc.reason:
            print(f"reason: {exc.reason}", file=sys.stderr)
        return 2
