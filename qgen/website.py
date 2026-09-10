"""The course website, used only when the database has nothing.

**The only file in this package that reaches the internet.** Keeping that in one place is what
makes it reviewable: a question is answered from the database, and this is the single door to
anywhere else.

The order matters and is not an implementation detail. The database holds material written for
these courses; the website is a fallback for what the database has not got. Reversing them would
answer from marketing copy when a written explanation was available.

WHY A SEARCH URL RATHER THAN A CRAWL
-------------------------------------
A crawler needs a sitemap, a politeness policy, a store and a schedule. A site's own search box
already knows its content, and one configured URL template is the whole integration:

    QGEN_SITE_SEARCH=https://example.com/?s={query}

With nothing configured this module returns nothing and the caller says the site was not
consulted. It never guesses a URL.
"""

from __future__ import annotations

import re
import urllib.parse

import httpx

from .domain.answering import Snippet

#: How long to wait for the site. Short: this runs inside a question a person is waiting on, and
#: a slow site should degrade to "we could not reach it" rather than hang the answer.
TIMEOUT_SECONDS = 12.0

#: How much of a page to read. Enough for the substance of an article, capped so one enormous
#: page cannot fill the prompt.
MAX_PAGE_CHARS = 40_000

#: Shortest run of text worth treating as a passage. Below this it is a nav item or a button.
MIN_PASSAGE_CHARS = 120

_SCRIPT_OR_STYLE = re.compile(r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{2,}")


def to_text(html: str) -> str:
    """Readable text from a page.

    Deliberately crude - no parser dependency. Scripts, styles and the furniture round the edges
    go first, then tags, then the entities that survive. What is left is good enough to search
    and to quote, which is all this needs to do.
    """
    body = _SCRIPT_OR_STYLE.sub(" ", html or "")
    body = _TAG.sub("\n", body)
    for entity, char in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"), ("&rsquo;", "'"), ("&ldquo;", '"'), ("&rdquo;", '"'),
    ):
        body = body.replace(entity, char)
    body = _WHITESPACE.sub(" ", body)
    return _BLANKS.sub("\n\n", body).strip()[:MAX_PAGE_CHARS]


def passages(text: str) -> list[str]:
    """The page split into things worth quoting, the short noise dropped.

    Split on blank lines, not on every line break. A paragraph in the source is usually wrapped
    across several physical lines, and splitting on each one turns a single passage into a dozen
    fragments too short to survive the length filter - so the page reads as empty.
    """
    blocks = (" ".join(block.split()) for block in text.split("\n\n"))
    return [block for block in blocks if len(block) >= MIN_PASSAGE_CHARS]


def search_url(template: str, question: str) -> str:
    """The site's search URL for this question.

    ``{query}`` is replaced with the question, percent-encoded. A template without the
    placeholder is used as-is, which lets a single page be configured as the source.
    """
    if "{query}" not in template:
        return template
    return template.replace("{query}", urllib.parse.quote_plus(" ".join(question.split())))


def fetch(url: str, *, client=None) -> str | None:
    """The text of one page, or ``None`` if it could not be read.

    Never raises. The site being down is not a reason to fail a question that the database
    could not answer either - the caller says the site could not be reached, which is a
    different and more useful thing to tell somebody than an error page.
    """
    try:
        get = client.get if client is not None else httpx.get
        response = get(
            url,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": "course-question-agent/1.0"},
        )
    except Exception:
        return None
    if response.status_code >= 400:
        return None
    return to_text(response.text)


def find_material(
    question: str,
    *,
    search_template: str,
    client=None,
) -> list[Snippet]:
    """Passages from the course website for this question.

    Returned as :class:`Snippet`, the same type the database produces, so the ranking, the
    prompt and the page treat both sources identically - and so the reader sees where each
    passage came from either way.
    """
    if not search_template:
        return []
    url = search_url(search_template, question)
    text = fetch(url, client=client)
    if not text:
        return []
    return [
        Snippet(source="the course website", course=url, text=block)
        for block in passages(text)
    ]
