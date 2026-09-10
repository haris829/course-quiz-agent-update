"""The website fallback: consulted only when the database has nothing, and never guessed at.

No test here touches the network. The fetch is a fake, because a test that depends on somebody
else's website passes or fails for reasons that have nothing to do with this code.
"""

from __future__ import annotations

import pytest

from qgen import website
from qgen.domain.answering import Snippet

PAGE = """
<html><head><style>.x{color:red}</style><script>var a=1;</script></head>
<body>
<nav>Home About Contact</nav>
<h1>Consent in medical law</h1>
<p>For consent to be valid the patient must be told of any material risk, meaning a risk to
which a reasonable person in the patient&rsquo;s position would attach significance. This is the
test laid down in Montgomery v Lanarkshire Health Board, which replaced the earlier Bolam
approach for the purposes of disclosure.</p>
<p>Short bit.</p>
<footer>Copyright</footer>
</body></html>
"""


class FakeResponse:
    def __init__(self, text: str = PAGE, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class FakeClient:
    """Records what was requested and returns what it was told to."""

    def __init__(self, result=FakeResponse()) -> None:
        self._result = result
        self.urls: list[str] = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


# ---------------------------------------------------------------------------
# Reading a page
# ---------------------------------------------------------------------------


def test_the_readable_text_survives_and_the_furniture_does_not():
    text = website.to_text(PAGE)

    assert "material risk" in text
    assert "var a=1" not in text
    assert "color:red" not in text
    assert "Home About Contact" not in text


def test_entities_are_turned_back_into_characters():
    assert "patient's position" in website.to_text(PAGE)


def test_short_fragments_are_not_treated_as_passages():
    blocks = website.passages(website.to_text(PAGE))

    assert any("Montgomery" in b for b in blocks)
    assert not any(b == "Short bit." for b in blocks)


def test_an_enormous_page_is_truncated():
    huge = "<p>" + ("word " * 200_000) + "</p>"

    assert len(website.to_text(huge)) <= website.MAX_PAGE_CHARS


# ---------------------------------------------------------------------------
# The search URL
# ---------------------------------------------------------------------------


def test_the_question_is_encoded_into_the_template():
    url = website.search_url("https://example.com/?s={query}", "what is  consent?")

    assert url == "https://example.com/?s=what+is+consent%3F"


def test_a_template_without_a_placeholder_is_used_as_a_single_source_page():
    assert website.search_url("https://example.com/faq", "anything") == "https://example.com/faq"


# ---------------------------------------------------------------------------
# Fetching, and failing safely
# ---------------------------------------------------------------------------


def test_a_page_is_fetched_and_returned_as_text():
    client = FakeClient()

    text = website.fetch("https://example.com/x", client=client)

    assert "Montgomery" in text
    assert client.urls == ["https://example.com/x"]


@pytest.mark.parametrize("status", [404, 403, 500, 503])
def test_an_error_page_reads_as_nothing_rather_than_as_content(status):
    """Otherwise a 404 page's "not found" text becomes the answer."""
    client = FakeClient(FakeResponse("<h1>Not found</h1>", status_code=status))

    assert website.fetch("https://example.com/x", client=client) is None


def test_the_site_being_unreachable_is_not_an_error():
    """A question the database could not answer should not also fail on the site being down."""
    client = FakeClient(TimeoutError("no route"))

    assert website.fetch("https://example.com/x", client=client) is None


# ---------------------------------------------------------------------------
# Material
# ---------------------------------------------------------------------------


def test_passages_come_back_as_snippets_naming_the_site():
    client = FakeClient()

    found = website.find_material(
        "what is consent?", search_template="https://example.com/?s={query}", client=client
    )

    assert found
    assert isinstance(found[0], Snippet)
    assert found[0].source == "the course website"
    assert "example.com" in found[0].course


def test_with_no_site_configured_nothing_is_fetched_and_no_url_is_guessed():
    client = FakeClient()

    assert website.find_material("anything", search_template="", client=client) == []
    assert client.urls == []


def test_a_page_with_nothing_readable_yields_no_material():
    client = FakeClient(FakeResponse("<html><body><nav>Menu</nav></body></html>"))

    assert website.find_material(
        "q", search_template="https://example.com/?s={query}", client=client
    ) == []
