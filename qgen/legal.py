"""The company's legal reference database, on SQL Server. Read-only.

The second place an answer can come from. The course material in PostgreSQL is first; this is what
that has not got - the law itself, rather than questions written about it.

WHAT IS WORTH READING HERE, AND WHAT IS NOT
--------------------------------------------
The database has thirty tables and most of them cannot answer a question. What was measured, on a
sample of each:

============================ ========= ==========================================================
Table                        Rows      Verdict
============================ ========= ==========================================================
``limitation_periods``       15        **Best.** Precise and citable - "Simple contract, 6 years,
                                       s.5 Limitation Act 1980". Exactly what an assessment tests.
``legal_guidance``           2,121     **Good.** Real titles and summaries from the Home Office,
                                       the Ministry of Justice and others. ``summary`` is 100%
                                       populated.
``all_cases``                219,635   **Partly.** ``case_summary`` is usually the case name and
                                       the judge list repeated, which answers nothing.
                                       ``legal_principle`` is populated on 43% and is the real
                                       content: "The court held that...".
``legislation``              152,053   **Titles only.** ``long_title`` and ``subject_headings``
                                       are empty on every row sampled. A title is not an answer.
``uni_courses``              60,384    **No.** A scraped catalogue of other institutions' degree
                                       listings, under 1% with a description.
============================ ========= ==========================================================

So three tables are read and the rest are left alone. Reading a table of titles would fill the
prompt with things that look like sources and are not.

WHY IT IS SEARCHED THE WAY IT IS
---------------------------------
``LIKE '%term%'`` cannot use an index, and ``all_cases`` has 219,635 rows. Every query is capped
with ``TOP``, the case search is restricted to rows that actually carry a principle, and the
whole thing is wrapped in a timeout: a question is a person waiting, and a slow search must
degrade to "nothing found" rather than hang.

Nothing here writes. The account is read-only and the code would not know what to write.
"""

from __future__ import annotations

from dataclasses import dataclass

from .domain.answering import Snippet

#: Rows to take from each table. Enough that the ranking has something to choose between, few
#: enough that one broad keyword cannot drag back half the database.
PER_TABLE_LIMIT = 25

#: Seconds. A question is a person waiting.
QUERY_TIMEOUT = 20
LOGIN_TIMEOUT = 15

#: Whether to search the 219,635 decided cases. **Off**, and this is a measurement, not caution.
#:
#: ``legal_principle`` is ``NVARCHAR(MAX)`` with no full-text index, so ``LIKE '%term%'`` is a full
#: table scan. ``TOP 25`` only helps when matches are common: "negligence" came back in 0.7
#: seconds, "duty" in 14, and "defamation" - a term with few matches, so the scan runs to the end
#: - did not come back inside two minutes. Bounding the scan with an inner ``TOP ... ORDER BY id``
#: was worse, because sorting that many MAX columns costs more than the scan.
#:
#: A source that answers some questions in a second and hangs on others is worse than one that is
#: not consulted, so the cases are left out. The fix is a full-text index on ``legal_principle``,
#: which this read-only account cannot create - it is a request to whoever owns that database, and
#: turning this back on is then a one-line change.
SEARCH_CASES = False


@dataclass(frozen=True, slots=True)
class LegalSource:
    """Where to reach the reference database. Empty host means it is simply not consulted."""

    host: str = ""
    port: str = "1433"
    database: str = ""
    user: str = ""
    password: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.host and self.database and self.user)


def connect(source: LegalSource):
    """A read-only connection, or ``None`` if one cannot be made.

    Never raises. The reference database being unreachable must not fail a question the course
    material could have answered on its own.
    """
    if not source.configured:
        return None
    try:
        import pymssql
    except ImportError:
        return None
    try:
        return pymssql.connect(
            server=source.host,
            port=str(source.port),
            user=source.user,
            password=source.password,
            database=source.database,
            timeout=QUERY_TIMEOUT,
            login_timeout=LOGIN_TIMEOUT,
            # Without this the driver mangles the typographic quotes these summaries are full of,
            # and "applicant's" reaches the prompt as "applicant?s".
            charset="UTF-8",
        )
    except Exception:
        return None


def _like_clause(column: str, terms: tuple[str, ...]) -> tuple[str, list[str]]:
    """An OR of contains-tests, and the parameters for it.

    ``[`` and ``%`` and ``_`` are escaped: in T-SQL's LIKE they are wildcards, and an unescaped
    ``%`` in a keyword would match every row in the table.
    """
    escaped = [f"%{t.replace('[', '[[]').replace('%', '[%]').replace('_', '[_]')}%" for t in terms]
    return " OR ".join([f"{column} LIKE %s"] * len(terms)), escaped


def find_material(
    source: LegalSource,
    terms: tuple[str, ...],
    *,
    conn=None,
    search_cases: bool = SEARCH_CASES,
) -> list[Snippet]:
    """Passages from the reference database for these keywords.

    Returned as :class:`Snippet`, the same shape the course material uses, so the ranking, the
    prompt and the page treat every source identically - and the reader is told which is which.
    """
    if not terms:
        return []
    owned = conn is None
    connection = conn if conn is not None else connect(source)
    if connection is None:
        return []

    found: list[Snippet] = []
    try:
        cursor = connection.cursor(as_dict=True)
        found += _limitation_periods(cursor, terms)
        found += _guidance(cursor, terms)
        if search_cases:
            found += _cases(cursor, terms)
    except Exception:
        # A failure part way through still returns what was already found. Half an answer's worth
        # of real material beats discarding it because the third query timed out.
        pass
    finally:
        if owned:
            try:
                connection.close()
            except Exception:
                pass
    return found


def _limitation_periods(cursor, terms: tuple[str, ...]) -> list[Snippet]:
    """Fifteen rows, and the most precisely useful thing in the database."""
    where, params = _like_clause("title", terms)
    cursor.execute(
        f"SELECT title, period, statute, clock_from FROM dbo.limitation_periods WHERE {where}",
        params,
    )
    return [
        Snippet(
            source="limitation periods",
            course="Limitation Act reference",
            text=f"{r['title']}: {r['period']}",
            explanation=f"Under {r['statute']}. The clock runs from {r['clock_from']}."
            if r.get("clock_from")
            else f"Under {r['statute']}.",
        )
        for r in cursor.fetchall()
    ]


def _guidance(cursor, terms: tuple[str, ...]) -> list[Snippet]:
    """Official guidance - the Home Office, the Ministry of Justice and others."""
    where, params = _like_clause("summary", terms)
    cursor.execute(
        f"SELECT TOP {PER_TABLE_LIMIT} title, issuing_body, summary "
        f"FROM dbo.legal_guidance WHERE {where}",
        params,
    )
    return [
        Snippet(
            source="legal guidance",
            course=r.get("issuing_body") or "official guidance",
            text=r["title"],
            explanation=r.get("summary"),
        )
        for r in cursor.fetchall()
    ]


def _cases(cursor, terms: tuple[str, ...]) -> list[Snippet]:
    """Decided cases - the principle, not the summary.

    Restricted to rows that carry a ``legal_principle``. The ``case_summary`` column is usually
    the case name and the bench repeated back, which puts words in the prompt and no information.
    """
    # The principle only. Adding `OR case_name LIKE ...` made the optimiser scan all 219,635
    # rows instead of stopping at TOP, and turned a 0.2 second query into one that never came
    # back. A case whose name matches but whose principle does not would not have answered the
    # question anyway.
    where, params = _like_clause("legal_principle", terms)
    cursor.execute(
        f"SELECT TOP {PER_TABLE_LIMIT} case_name, area_of_law, legal_principle "
        f"FROM dbo.all_cases "
        f"WHERE legal_principle IS NOT NULL AND ({where})",
        params,
    )
    return [
        Snippet(
            source="decided cases",
            course=r.get("area_of_law") or "case law",
            text=(r.get("case_name") or "")[:300],
            explanation=r.get("legal_principle"),
        )
        for r in cursor.fetchall()
    ]
