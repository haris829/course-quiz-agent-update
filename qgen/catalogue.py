"""Reading ``qc_courses``. Read-only, deliberately.

The catalogue belongs to another capability. This package reads it and writes its own tables
(``qgen_*``); it never writes a course row, and it never touches the sibling project's ``qb_*``
or ``qz_*`` tables. One-way traffic is what makes the dependency reviewable - and it means a
mistake here cannot corrupt the thing everything else reads.

The lookup rules live in :mod:`qgen.domain.resolution`, which has no database in it. This file is
the three queries that feed them.
"""

from __future__ import annotations

import psycopg

from .domain.generation import CourseBrief
from .domain.resolution import (
    CourseRow,
    choose,
    escape_like,
    normalise_reference,
    wants_partial_match,
)

#: Always present. A catalogue row without these is not a course.
_REQUIRED = ("code", "title")

#: Present only where the catalogue has been through the platform's later import. Selected when
#: they exist, so the questions improve the day that import runs, with no code change.
#:
#: They are checked for rather than assumed because the same table exists in two shapes. The
#: deployed database was created by the earlier import and has the title and nothing else;
#: selecting a column it has not got fails the whole query with ``UndefinedColumn`` - which is
#: exactly what happened on the first deploy. Reading defensively is cheaper than requiring every
#: environment to be migrated in step, and far cheaper than altering a table another service owns.
_OPTIONAL = ("description", "rqf_level", "subject_area")


def _columns(conn: psycopg.Connection) -> str:
    """The SELECT list this database can actually satisfy."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'qc_courses' AND column_name = ANY(%s)",
            (list(_OPTIONAL),),
        )
        present = {record["column_name"] for record in cur.fetchall()}
    return ", ".join([*_REQUIRED, *(c for c in _OPTIONAL if c in present)])


def _row(record: dict) -> CourseRow:
    return CourseRow(
        code=record["code"],
        title=record["title"],
        description=record.get("description"),
        rqf_level=record.get("rqf_level"),
        subject_area=record.get("subject_area"),
    )


def list_courses(conn: psycopg.Connection) -> list[CourseRow]:
    """Every course, by title.

    Ordered by title rather than by code because a person choosing one reads the name; nobody
    outside this system knows that ``LL-34590`` is Criminology.
    """
    columns = _columns(conn)
    with conn.cursor() as cur:
        cur.execute(f"SELECT {columns} FROM qc_courses ORDER BY title")
        return [_row(record) for record in cur.fetchall()]


def find_course(conn: psycopg.Connection, course_ref: str) -> CourseRow | None:
    """The one course this reference names, or ``None`` if none or more than one does.

    Three queries, narrowest first. The third is skipped entirely for a short reference, and its
    ``LIMIT 2`` is the point of the query: the only thing worth knowing is whether exactly one row
    matches, and fetching the other thirty to discover that would be waste.
    """
    reference = normalise_reference(course_ref)
    if not reference:
        return None
    lowered = reference.lower()
    columns = _columns(conn)

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {columns} FROM qc_courses WHERE lower(code) = %s LIMIT 2", (lowered,)
        )
        by_code = [_row(record) for record in cur.fetchall()]

        cur.execute(
            f"SELECT {columns} FROM qc_courses WHERE lower(title) = %s LIMIT 2", (lowered,)
        )
        by_title = [_row(record) for record in cur.fetchall()]

        by_partial: list[CourseRow] = []
        if wants_partial_match(reference):
            cur.execute(
                f"SELECT {columns} FROM qc_courses "
                "WHERE lower(title) LIKE %s ESCAPE '\\' LIMIT 2",
                (escape_like(lowered),),
            )
            by_partial = [_row(record) for record in cur.fetchall()]

    return choose(by_code=by_code, by_title=by_title, by_partial=by_partial)


def brief_for(course_ref: str, course: CourseRow | None) -> CourseBrief:
    """The brief to generate from: the matched course, or the caller's own words.

    A miss is not an error. The caller asked for questions on a subject and named a course as the
    way of saying which; if the name does not resolve, the subject still stands. What must not
    happen is the miss going unmentioned, so :attr:`CourseBrief.grounding` records which of the
    two this is and every report repeats it.
    """
    if course is None:
        return CourseBrief(code=None, name=normalise_reference(course_ref))
    return CourseBrief(
        code=course.code,
        name=course.title,
        description=course.description,
        rqf_level=course.rqf_level,
        subject_area=course.subject_area,
    )
