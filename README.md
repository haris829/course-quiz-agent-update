# Course Question Agent

Two things over the courses held in a PostgreSQL database:

- **Ask** — a question in plain English, answered live from the material those courses hold, with
  the material it used shown alongside so the answer can be checked.
- **Practice** — a question from a named course, a different one each time, marked server-side.

There is also a **generate** command that writes new multiple-choice questions for a course and
stores them as unreviewed drafts.

---

## Start here

| You want to | Read |
|---|---|
| **Test the system and sign it off** | **[VERIFICATION_GUIDE.md](VERIFICATION_GUIDE.md)** — written for the client and the PMO |
| Run it on your machine | This file, below |
| Know what it will not do, and what is unfinished | [VERIFICATION_GUIDE.md](VERIFICATION_GUIDE.md), §7 and §8 |

**Read the Verification Guide before signing anything off.** It records five findings and eight
open items, and none of the open items is a development task.

---

## Running it

Python 3.11 or later, and a reachable PostgreSQL database.

```bash
pip install -r requirements.txt
python -m qgen init-db        # creates this system's three tables
python -m qgen serve          # the page, at http://127.0.0.1:8010
```

**A working `.env` is included**, so there is no configuration step — it holds the development
database URL and the model key. To point the system somewhere else, edit it, or start from
`.env.example`.

> **This package is confidential because of that file.** It carries live credentials. Do not
> commit it, do not forward it, and **rotate the model key once hand-over is complete** — it is a
> personal developer key, recorded as open item OI-Q3 in the Verification Guide.

Other commands:

```bash
python -m qgen courses                                   # the catalogue, and what each course holds
python -m qgen generate --course "Criminology" --count 5
python -m qgen generate --course LL-45165 --count 20 --answers
```

**With no model key configured, everything refuses cleanly with HTTP 503 and writes nothing.**
It never invents a question or an answer from nowhere.

---

## The page

`python -m qgen serve` → **http://127.0.0.1:8010**. One box, two buttons.

**Answer this** — asks a question and answers it now, from the course material:

```
Q  Which OSI layer routes packets between networks?
A  Layer 3, the Network layer, is responsible for routing packets between networks. It handles
   logical addressing and routing, determining the best path for data to travel…
→  Answered from 2 items of course material from Networking, OSI Model.
```

**Question me on this course** — type a course name and be tested on it. A different question
every time, and never one from another course.

### Where it looks, in order

1. **The database** — material written for these courses.
2. **The course website** — only if the database has nothing, and only if one is configured.
3. Nothing else.

The website is a fallback, never the first stop: the database holds explanations written for
these courses, and going to the site first would answer from marketing copy when a written
explanation was available.

Configure it with one line in `.env` — the site's own search URL, with `{query}` where the
question goes:

```
QGEN_SITE_SEARCH=https://example.com/?s={query}
```

Leave it empty and the site is never contacted. There is no crawler and no guessed URL.

### Where an answer came from is always stated, and always checkable

The page shows the same numbered material the model was given, with the items it cited marked.
Three situations, three different sentences — none of them dressed up as another:

| | |
|---|---|
| It used the material | *Answered from 2 items of course material from Criminology (Postgraduate).* |
| Material was found but did not cover it | *The course material found did not cover this, so the answer is general subject knowledge.* |
| The named course holds nothing on it | *Criminology (Postgraduate) holds nothing on this… and no other course was substituted for it.* |
| The website answered it | *Not in the course material. Answered from the course website (example.com/?s=…).* |

---

## What it reads, and what it writes

**It writes three tables, and only these three:**

| Table | |
|---|---|
| `qgen_runs` | one row per generation: what was asked, what was stored, what was refused and why |
| `qgen_questions` | the stem, the answer key, the provenance, the status |
| `qgen_question_options` | four rows per question, one flagged correct |

**It reads, and never writes:** the course catalogue (`qc_courses`), and the platform's question
bank (`qb_questions`, `qb_topics` and their join tables) as material to answer from. The
read-only coupling to the question bank lives in exactly one file —
[qgen/library.py](qgen/library.py) — so if the bank changes shape, one file breaks and nothing
else does.

**Asking stores nothing.** No table records the question or the answer. Ask the same thing twice
and it runs twice.

---

## Three rules the code holds to

**The answer key never travels with a question.** Every route that hands out a question hands out
the stem and the options and nothing else. One route reveals the key — `/api/practice/answer` —
and only in reply to an answer already committed. A test asserts the string `answer` does not
appear in any question payload.

**Nothing is repaired.** A generated question with three options is refused, not padded to four.
`"answer": "B and C"` is refused, not read as `B`. Every refusal is counted by reason, so a low
yield is diagnosable rather than mysterious. Repairing a malformed question is how a plausible
wrong answer reaches somebody's certificate.

**Every generated question is a `DRAFT`.** A model can produce a question that is fluent,
plausible and wrong. Nothing here writes any other status.

---

## The HTTP interface

| | |
|---|---|
| `POST /api/ask` | the live ask — the answer, plus the material it was given |
| `POST /api/practice` | one question from a named course |
| `POST /api/practice/answer` | mark one answer; the only route that reveals a key |
| `GET /api/courses` | the catalogue, with what each course holds |
| `GET /api/questions?course=…` | questions already stored for a course — no answer key |
| `POST /api/generate` | generate and store questions — no answer key |
| `GET /api/health` | whether a model is configured |

Clickable documentation for all of it is at `http://127.0.0.1:8010/docs` while the server runs.

**There is no authentication on any endpoint.** This is a local tool bound to `127.0.0.1`. It
must not be exposed on a network as it stands — that is open item **OI-Q4** in the Verification
Guide.

---

## The code

```
qgen/
  domain/          pure logic - no database, no HTTP, no model client
    generation.py    the question prompt, and the parser that refuses
    answering.py     keywords, ranking, the answer prompt and parse
    resolution.py    the course-lookup rules and LIKE escaping
    replies.py       reading a model's reply: JSON extraction, whitespace
  catalogue.py     reading the course catalogue
  library.py       finding material to answer from (the only cross-schema read)
  website.py       the course-website fallback (the only file that uses the network)
  practice.py      one question from a course, and the marking of it
  answers.py       the live ask, end to end
  service.py       generation: batching, concurrency, repeat-avoidance
  storage.py       this system's own three tables
  llm.py           AWS Bedrock, with retry and throttle classification
  web.py           the HTTP routes
  cli.py           the command line
  static/          the page
tests/             336 tests
```

The `domain/` package is where the logic most likely to be wrong lives, and it is the part that
needs neither a database nor a network to test.

---

## Tests

```bash
python -m pytest        # 336 tests
```

**218 of the 336 need no database and no network** — the parser, the prompts, the lookup rules,
the ranking, and the model client. The rest connect to `QGEN_DATABASE_URL` inside a transaction
that is always rolled back, so a run leaves the database exactly as it found it. Without a
reachable database those tests skip rather than fail.

`tests/test_portability.py` reads the SQL in this package and fails on two things that pass on
SQLite and break on PostgreSQL: `is_correct = 1`, and engine-specific JSON functions. The gate
also tests itself — one case proves it fires on the bug, another proves it does not fire on a
comment warning about the bug.

---

## The limitation to know about before anything else

**No course in the catalogue has a description.** `description`, `rqf_level` and `subject_area`
are NULL on every row. When *generating*, the model is therefore given a course title — four or
five words — and writes from its own knowledge of the subject. The questions are on topic;
**none is traceable to a lesson.**

Nothing here pretends otherwise: every run prints what it wrote from, and every stored question
carries the same sentence in a `grounding` column.

*Answering* is less affected — it retrieves from real explanations written for these courses,
which is a great deal more than a title.

Closing the gap needs the platform's own course descriptions imported into `qc_courses`. **This
code needs no change when that happens** — the columns are already read and already passed into
the prompt.

---

*Internal — Confidential. Prepared by Consultancy Outfit.*
