# Course Question Agent — Verification Guide

**For:** Consultancy Outfit · **Version:** 0.1.0 · **Date:** 2026-09-10
**Source code:** `github.com/haris829/course-quiz-agent-update`, branch `main`, commit `c179642`.
**Live deployment:** https://question-agent-production.up.railway.app

A concise, step-by-step guide to verify each stated requirement. Every step gives a plain-English
summary, a sample input, and the expected result. **Every expected result marked *(measured)* was
produced by running the step against a live AWS Bedrock model, a live PostgreSQL database and a
live SQL Server** — none of it is illustrative. Where a check exposed a limitation, it says so.

---

## What this project is (in plain English)

The Course Question Agent answers questions about your courses, and tests people on them.

Ask it something in plain English and it searches what your databases actually hold, puts what it
finds in front of the model, and returns an answer **together with the material that answer rests
on**. Name a course instead and it puts a question to you, marks your answer, and gives you a
different one every time.

It does four things deliberately:

- **It looks before it answers.** The course material comes first, then your legal reference
  database. The model is given what was found, not left to invent from the question alone.
- **It never claims grounding it does not have.** Every answer names the sources it used. Where
  nothing covered the question it says so, in its own first sentence, and answers from general
  knowledge — labelled as such.
- **It never sends an answer key to the page.** The key is read server-side, and only in reply to
  an answer already committed.
- **It refuses rather than repairs.** A generated question the parser will not accept is thrown
  away and counted, never patched up. A repaired question is how a plausible but wrong answer ends
  up on somebody's certificate.

It does not mark certified attempts, issue certificates, or replace your assessment platform's exam
flow. It answers questions, sets practice questions, and writes new ones as drafts.

---

## How to run the verification

Run it locally, or use the deployed instance. The interactive docs give every endpoint a **Try it
out** button, with no tools to install.

```bash
BASE_URL="http://127.0.0.1:8010"                                  # local
# BASE_URL="https://question-agent-production.up.railway.app"     # deployed

# The page:            ${BASE_URL}/
# Interactive docs:    ${BASE_URL}/docs
# The spec:            ${BASE_URL}/openapi.json
```

To run it locally:

```bash
pip install -r requirements.txt
python -m qgen init-db        # creates the three qgen_* tables
python -m qgen serve          # http://127.0.0.1:8010
```

**Authentication.** There is none. This is a local tool bound to `127.0.0.1`. **The deployed URL is
public and unauthenticated** — see Difference 5.

**Environment-dependent steps.** R01–R06 need an AI provider configured
(`COACHING_LLM_PROVIDER=bedrock`, `COACHING_LLM_API_KEY`, `COACHING_LLM_MODEL`); without one every
route returns a clean `503` and writes nothing (R10). R05 needs the SQL Server reference database
(`QGEN_MSSQL_*`). Everything else runs with no extra setup — **222 of the 345 tests need neither a
database nor a network.**

---

## R01 — Answers a question from what the databases hold

**Endpoint:** `POST /api/ask`

**Purpose:** A question in plain English is answered from material that actually exists in your
databases, not from the model's imagination. *(Verifies: an answer is produced; it is drawn from
stored material; the material is returned with it.)*

**Sample data:**

```bash
curl -X POST "${BASE_URL}/api/ask" -H "Content-Type: application/json" \
  -d '{"question": "What must a doctor disclose for consent to be valid?"}'
```

**Expected (measured):** `200 OK` in **4.8 s**.

> *"Following Montgomery v Lanarkshire Health Board [2015], a doctor must disclose any risk that a
> reasonable person in the patient's position would regard as significant. There is also a
> subjective element… This replaced the earlier doctor-centred Bolam approach…"*
>
> → *Answered from 3 items of course question bank, generated for this course — Medical Law MA
> (Postgraduate).*

**The answer was checked against the rows it cites.** Row 2 of the material reads *"The Supreme
Court in Montgomery held that the test for informed consent is whether a reasonable person in the
patient's position would regard the risk as significant, combined with a subjective element…"* Every
claim in the answer is in the source. Nothing was added.

Four questions were run and all four answered from course material:

| Question | Time | Cited |
|---|---|---|
| What must a doctor disclose for consent to be valid? | 4.8 s | Medical Law MA |
| What is collective efficacy in criminology? | 5.3 s | Criminology (Postgraduate) |
| Which OSI layer routes packets between networks? | 5.2 s | Networking, OSI Model |
| What is the limitation period for a simple contract claim? | 4.2 s | LLB Graduate Law, Law Conversion LLM, Legal Practice (LPC) |

---

## R02 — Says where the answer came from, checkably

**Endpoint:** `POST /api/ask`

**Purpose:** "Grounded in your material" is something you can verify, not something you take on
trust. The response carries the same numbered material the model was given, with the cited items
flagged. *(Verifies: material returned alongside the answer; citations name real items; the
provenance names the actual source.)*

**Sample data:** any `/api/ask` call — read the `material` array and the `provenance` string.

**Expected (measured):** `material` carries up to 8 items, each with `n`, `source`, `course`,
`text`, `explanation` and `used`. The page's **"Show the items it was given"** panel displays the
same list, with the used ones marked.

**The provenance is derived from the citations, never from which searches ran.** That is a fix, not
a design that came out right first time: an earlier version set a flag when the legal reference
database was merged into the material, and then reported an answer built *entirely* from Medical Law
course questions as having come from the legal reference database. Where an answer came from is the
one thing on that page that has to be true, so it is now read back off the citations. A test pins it.

---

## R03 — Admits when nothing covers the question

**Endpoint:** `POST /api/ask`

**Purpose:** A system that quietly answers from general knowledge while implying course grounding
is worse than one that answers nothing. *(Verifies: no material invented; no unrelated course
substituted; the answer itself admits the gap.)*

**Sample data:**

```bash
curl -X POST "${BASE_URL}/api/ask" -H "Content-Type: application/json" \
  -d '{"question": "How do I bake sourdough bread at home?"}'
```

**Expected (measured):** `grounded: false`, `material: []`, and:

> *"Nothing in the material covers this. The answer is general subject knowledge."*

The answer's own first sentence says the same — *"The course material does not cover this topic…"* —
before going on to answer helpfully. The same held for *"Explain photosynthesis in chloroplasts"*.

**This is the single most useful behaviour to demonstrate.** It answers, it is useful, and it does
not pretend.

---

## R04 — Scoped to the course you named, with no substitution

**Endpoint:** `POST /api/ask`, `POST /api/practice`

**Purpose:** Naming a course means that course. Answering a Criminology question out of Medical Law
and reporting it as course material would leave the reader no way to tell. *(Verifies: a named
course is recognised; only its material is used; an empty result is reported rather than filled from
elsewhere; an ambiguous name is refused.)*

**Sample data:**

```bash
# A question the named course holds nothing on
curl -X POST "${BASE_URL}/api/ask" -H "Content-Type: application/json" \
  -d '{"question": "In Criminology (Postgraduate), what is the OSI model?"}'

# An ambiguous course name
curl -X POST "${BASE_URL}/api/practice" -H "Content-Type: application/json" \
  -d '{"course": "International"}'
```

**Expected (measured):** the first returns `course: "Criminology (Postgraduate)"` and
`material: []` — **not one of the many Networking items was substituted** — with:

> *"Criminology (Postgraduate) holds nothing on this. The answer is general subject knowledge, and
> no other course was substituted for it."*

The second returns `422`:

> *"No single course matches 'International'. Four courses contain 'International', for instance —
> name one of them."*

**How a course is resolved.** Three attempts, narrowest first, each case-insensitive, each requiring
**exactly one** match: the catalogue code, then the exact title, then a title containing what you
sent. Ambiguity resolves to nothing rather than a guess. A reference under five characters is not
partially matched at all — "Law" is in most of the catalogue. `%` and `_` are escaped, because one
course is genuinely titled `100% Practical_Skills` and an unescaped `%` matches everything.

---

## R05 — Your legal reference database as a second source

**Endpoint:** `POST /api/ask` with `QGEN_MSSQL_*` configured

**Purpose:** Where the course material has nothing, the company's own legal database is searched —
the law itself, rather than questions written about it. *(Verifies: the SQL Server is read; its rows
reach the answer; they are labelled as what they are.)*

**Sample data:**

```bash
curl -X POST "${BASE_URL}/api/ask" -H "Content-Type: application/json" \
  -d '{"question": "What is the limitation period for a simple contract claim?"}'
```

**Expected (measured), on the deployed instance:** `200 OK` in **4.0 s**.

> *"For a simple contract claim, the limitation period is 6 years. The clock starts running from
> the date of breach."*
>
> → *Answered from the company's legal reference database (**limitation periods**).*

That answer came out of `dbo.limitation_periods` on `loophole-larry-db.database.windows.net`, live.
Measured search time for the tables that are read: **~1.2 s**.

### What is read from that database, and what is not

All 30 tables were profiled on a sample of each before anything was wired in:

| Table | Rows | Read? | Why |
|---|---|---|---|
| `limitation_periods` | 15 | **Yes** | Precise and citable — *"Simple contract, 6 years, s.5 Limitation Act 1980"*. Exactly what an assessment tests |
| `legal_guidance` | 2,121 | **Yes** | `summary` populated on **100%** — Home Office, Ministry of Justice, real content |
| `all_cases` | 219,635 | **No** — see below | `legal_principle` on 43% is real; `case_summary` is mostly the case name and bench repeated |
| `legislation` | 152,053 | No | `long_title` and `subject_headings` **empty on every row sampled**. A title is not an answer |
| `uni_courses` | **60,384** | No | Scraped listings of *other institutions'* degrees, **under 1% carry a description** |

**On the 60,384 courses.** They are a name list, not content. Generating from a row of it would be
generating from a course title alone — the same limitation as Difference 1, 60,384 times over.

**Why the 219,635 cases are not searched — measured, not assumed.** `legal_principle` is
`NVARCHAR(MAX)` with no full-text index, so `LIKE` is a full table scan and `TOP` only
short-circuits when matches are common:

```
"negligence"     0.7s     (common - TOP 25 fills early)
"duty"          14.1s
"defamation"  > 120s      (rare - the scan runs to the end)
```

Bounding it with an inner `TOP … ORDER BY id` was **worse** — sorting that many MAX columns costs
more than the scan. A source that answers in a second sometimes and hangs other times is worse than
one not consulted. **The fix is a full-text index on `legal_principle`**, which the `larry_readonly`
account cannot create; it is a request to whoever owns that database, and turning it back on is then
a one-line change (`SEARCH_CASES` in `qgen/legal.py`).

---

## R06 — A different practice question every time, from that course only

**Endpoint:** `POST /api/practice`

**Purpose:** Name a course and be tested on it, with a different question each time, and never one
from a neighbouring course. *(Verifies: distinct questions; the count of what remains; strict course
scoping; live generation when the course runs out.)*

**Sample data:**

```bash
curl -X POST "${BASE_URL}/api/practice" -H "Content-Type: application/json" \
  -d '{"course": "Criminology", "exclude": []}'
# then pass each ref you receive back in "exclude"
```

**Expected (measured):** every draw distinct, the counter falling, all from the one course:

```
1. [qgen-5]      Criminology (Postgraduate)  left 27/28
2. [qb-71831203] Criminology (Postgraduate)  left 26/28
3. [qb-def2c903] Criminology (Postgraduate)  left 25/28
```

**Verified exhaustively:** all 27 questions then in the pool were drawn one after another. Every
reference distinct; **every single one from Criminology (Postgraduate)** and none from any other
course. On the 28th draw the pool was empty and the system **wrote a fresh question live** for that
course — *"A feminist criminologist argues that traditional theories of…"* — and stored it as a
draft, rather than reaching into a neighbouring course.

**Why references look like `qgen-5` and `qb-71831203…`.** The two sources key their rows
independently and not even in the same type: `qgen_questions.id` is a `bigint`, `qb_questions.id` is
a 32-character hex string. Naming the table means a reference cannot be read against the wrong one —
which matters most at marking time, where reading the wrong row means marking against the wrong key.
Getting this wrong was a real bug, and PostgreSQL refused it outright rather than silently
mismatching: `operator does not exist: character varying = integer`.

---

## R07 — The answer key never reaches the page

**Endpoint:** `POST /api/practice`, `POST /api/practice/answer`

**Purpose:** The key is read server-side and only in reply to an answer already committed.
*(Verifies: no key in any question payload; marking is correct; the key is revealed only on
answering.)*

**Sample data:**

```bash
Q=$(curl -s -X POST "${BASE_URL}/api/practice" -H "Content-Type: application/json" \
      -d '{"course": "Criminology"}')
echo "$Q" | grep -c '"answer"'          # expect 0

curl -X POST "${BASE_URL}/api/practice/answer" -H "Content-Type: application/json" \
  -d '{"ref": "<ref from above>", "label": "A"}'
```

**Expected (measured):** the question payload contains `ref`, `question`,
`options[{label,text}]`, `course`, `status`, `source` — **no `answer`, no `is_correct`, no
`explanation`.** Submitting each of A, B, C and D against one question returned
`correct=false, false, true, false` with `answer: B` and `status: DRAFT`.

A test serialises the whole payload and asserts the string `answer` does not appear, so a field
added later cannot leak the key past it.

**Known limit, stated plainly.** Submitting all four options in turn *will* find the key, because
each submission is marked independently and there is no attempt state. Acceptable for practice and
**not acceptable for anything that certifies a person** — see Difference 3.

---

## R08 — Generated questions are refused, never repaired

**Endpoint:** `POST /api/generate`, and `python -m qgen generate`

**Purpose:** A question the parser cannot vouch for is thrown away and counted by reason, never
patched up. *(Verifies: malformed questions rejected; refusals counted and explained; good questions
in the same reply still survive.)*

**Sample data:**

```bash
python -m qgen generate --course "Criminology" --count 3
```

**Expected (measured):**

```
Course reference: 'Criminology'
Matched:          LL-34590
Written from:     the course name only (the catalogue row has no description)
Asked the model for 4; requested 3.
Stored:           3  (status DRAFT, unreviewed)
```

A question is refused if it is not valid JSON, carries no `questions` array, has other than four
options, has an empty or duplicated option, names an answer that is not one of A–D, or repeats one
already asked for that course. Every refusal is counted **by reason**, so a low yield is diagnosable
rather than mysterious.

**`"answer": "B and C"` is refused, not read as `B`.** That was a real defect found during the
build: the parser took the first character, silently turning a two-answer question into a one-answer
question with a key nobody chose. 32 parser tests, the majority of them refusals.

**Timings (measured):** 3 questions in **16 s** (one batch); 20 questions in **52 s** (two
concurrent batches); 2 questions in **8 s** over HTTP. Concurrency is capped at 5. A course with
history is asked for ~40% more than needed, because repeats are dropped and nothing backfills them.

---

## R09 — Every generated question is an unreviewed draft

**Endpoint:** none — a property of what generation writes

**Purpose:** A model can produce a question that is fluent, plausible and wrong. Nothing it wrote
should reach a learner until a person has read it. *(Verifies: nothing generated is anything but a
draft; every stored question is well formed.)*

**Sample data:**

```sql
SELECT status, COUNT(*) FROM qgen_questions GROUP BY status;

SELECT question_id FROM qgen_question_options
  GROUP BY question_id
 HAVING COUNT(*) <> 4 OR SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) <> 1;
```

**Expected (measured):**

```
qgen_questions        31        non-DRAFT   0
qgen_question_options 124       malformed   [] (none)
distinct stems        31 of 31
```

Every stored question has exactly four options and exactly one correct. Every one is `DRAFT`.

**And the reason it matters, from this very database.** Question 4 reads *"associated with
**Wolfang's** birth cohort research"*. The criminologist is **Wolfgang**. A small error in an
otherwise sound question — and exactly the point: no automated check in this system would ever catch
it. The 31 generated questions are **awaiting human review**.

**The question is also frozen.** The stem, options and key are written onto the row, not referenced.
If a question could be edited later, every past result would silently change meaning: someone who
passed in March could be shown different questions in June.

---

## R10 — Failures reported honestly

**Endpoint:** all of them

**Purpose:** A caller can tell "retry this" from "someone needs to look at this". *(Verifies:
correct status codes; a throttle is not reported as a prompt fault.)*

**Sample data:**

```bash
# No model configured
COACHING_LLM_API_KEY= python -m qgen serve --port 8011
curl -X POST "http://127.0.0.1:8011/api/ask" -H "Content-Type: application/json" \
  -d '{"question": "What is mens rea?"}'

# An impossible request
curl -X POST "${BASE_URL}/api/ask" -H "Content-Type: application/json" -d '{"question": "   "}'
```

**Expected (measured):**

| Status | Code | Meaning |
|---|---|---|
| `422` | `INVALID_REQUEST` | empty question, question over 1000 chars, count outside 1–50, ambiguous course |
| `404` | — | a practice reference that names nothing |
| `503` | `GENERATION_UNAVAILABLE` | provider unreachable, timed out, **or throttled** — retryable; nothing was written |
| `502` | `GENERATION_FAILED` | the provider answered but would not follow the output contract — a person should read the prompt |

With no key: `/api/health` reports `model_configured: false`, `/api/ask` returns **503** with
`retryable: true`, and the CLI refuses before writing anything with **exit code 3**.

**The 503/502 distinction is load-bearing.** A rate limit is a 503 and retryable. It is not a fault
in the prompt, and reporting it as one sends an operator to debug something that was working. A
throttled request is retried three times with a widening pause, honouring `Retry-After`.

**A provider error body is never forwarded** — it can echo the prompt back, and the prompt is not
something to put in an API response.

---

## R11 — PostgreSQL, and it stays that way

**Purpose:** Everything is stored in your PostgreSQL, and nothing SQLite-only can creep in.
*(Verifies: the portability gates; the transactional test discipline; tolerance of either catalogue
shape.)*

**Sample data:**

```bash
python -m pytest tests/test_portability.py
```

**Expected (measured):** **49 passed.** The gate reads the SQL in the package and fails on two
things that pass on SQLite and break on PostgreSQL:

- `is_correct = 1` — SQLite stores booleans as integers and accepts it; PostgreSQL fails with
  `operator does not exist: boolean = integer`. **This exact bug shipped in the sibling project.**
- engine-specific JSON and aggregate functions — JSON is assembled in Python instead.

**The gate tests itself.** One case proves it fires on the bug; another proves it does *not* fire on
a comment warning about the bug — because a gate that fails on its own documentation teaches people
to delete the documentation.

**Tests that touch the database run inside a transaction that is always rolled back**, DDL included.
Counts were identical before and after a full suite run.

**It reads a catalogue in either shape.** The deployed `qc_courses` was made by an earlier import
and has only a code and a title. Selecting a column it has not got failed the whole query with
`UndefinedColumn` — which is exactly what the first Railway deploy did, on every catalogue read. The
columns are now checked for and selected only where present, with a test that drops them to prove it.

---

## R12 — Deployed, and verified on the deployment

**Purpose:** It runs somewhere other than the machine it was built on. *(Verifies: a public URL
answers; the model works from there; the reference database is reachable from there; a fresh clone
of the repository runs.)*

**Sample data:**

```bash
curl "https://question-agent-production.up.railway.app/api/health"
curl -X POST "https://question-agent-production.up.railway.app/api/ask" \
  -H "Content-Type: application/json" \
  -d '{"question": "Which OSI layer routes packets between networks?"}'
```

**Expected (measured):** `{"model_configured": true}`, and an answer in **3.6 s** — *"Layer 3, the
Network layer…"* → *Answered from 2 items of course material from Networking, OSI Model.* The
limitation-period question (R05) also answered live from SQL Server in **4.0 s**.

Deployed as a **new `question-agent` service** in the existing `courses-quiz-agent` Railway project.
The existing `quiz-agent` service was not touched.

**Verified from a fresh `git clone`**, not from the working copy:

| | |
|---|---|
| Clone, no credentials | **222 passed, 123 skipped** |
| Clone, with credentials | **345 passed** |
| Lint on the clone | All checks passed |
| App runs from the clone | `33 courses in qc_courses` |
| `.env` present in the clone | **No** — credentials never reached GitHub |

**Building it caught a real defect:** `requirements.txt` listed only `psycopg` and `httpx`, but the
code imports `fastapi` and `pydantic` and the server needs `uvicorn`. Invisible locally because
those were already installed; fatal on any clean machine.

---

## Global Definition of Done

| Check | How to verify | Expected |
|---|---|---|
| Full suite | `python -m pytest` | **345 passed** |
| No database, no network | `QGEN_DATABASE_URL= python -m pytest` | **222 passed, 123 skipped** |
| Lint | `python -m ruff check .` | `All checks passed!` |
| Database portability | `python -m pytest tests/test_portability.py` | **49 passed** |
| Parser refuses | `python -m pytest tests/test_parser.py` | **32 passed**, mostly refusals |
| No key to the page | R07 | no `answer` in any question payload |
| Nothing generated is deliverable | R09 | every generated question `DRAFT` |
| Provenance is true | R02 | derived from citations, not from which searches ran |
| Course scoping | R04 | no substitution; ambiguity refused |
| Deployment | R12 | live URL answers; a fresh clone runs |

**Where the 345 tests are:**

| File | Tests | Covers |
|---|---|---|
| `test_portability.py` | 49 | The SQL gates — reads the source, not the database |
| `test_answering.py` | 39 | Keywords, ranking, the answer prompt and parse |
| `test_storage.py` | 38 | Freezing, drafts, history, the whole generation run |
| `test_web.py` | 34 | Every route, including the key-leak assertions |
| `test_parser.py` | 32 | Mostly what the question parser **refuses** |
| `test_llm.py` | 31 | Retry, throttling, how a failure is classified |
| `test_ask.py` | 26 | Retrieval and the live ask |
| `test_practice.py` | 25 | Course scoping, no-repeats, marking |
| `test_prompt.py` | 21 | The generation prompt, batching, headroom |
| `test_resolution.py` | 20 | Course lookup rules and LIKE escaping |
| `test_website.py` | 15 | The website fallback, network faked |
| `test_service.py` | 15 | Batching, concurrency, partial failure |

---

## Note for sign-off — how this differs from the brief

Seven points where the delivered agent differs from what was described, or where a decision was
taken that should be confirmed rather than assumed.

| # | Brief said | Delivered | Why |
|---|---|---|---|
| 1 | Generate from "the course's content" | Generates from the course **name**. `description`, `rqf_level` and `subject_area` are **NULL on all 33 rows** of `qc_courses` | We were not given lesson content. The **answering** side is far less affected — it retrieves from 706 real explanations written for these courses. But no *generated* question can be traced to a particular lesson. **This remains the most significant gap.** Your platform's own `public.courses` has descriptions; read access closes it, and those columns are already read, so no code change is needed |
| 2 | "answer from the website which is in the database" | The website URL is **not in the database**. Every URL-ish column, then every text column in every table, was searched; the only `http` hits are question text *about* HTTPS | Built anyway, reading the site from one config line (`QGEN_SITE_SEARCH`). Paste a URL and it works immediately. Empty today, so the site is never contacted |
| 3 | Not specified | Marking is **per question**, and submitting all four options will find the key | Acceptable for practice, **not for certification**. A real sitting needs one submission per question against a recorded attempt — UC-03 already has that. Do not reuse these endpoints for a graded assessment without it |
| 4 | Not specified | Every generated question is a **`DRAFT`**; practice also serves the **676 unreviewed drafts** in `qb_questions` (against 30 `ACTIVE`) | The status is shown on every question rather than hidden. Your platform's own rule is that only `ACTIVE` reaches a learner — **whether practice may draw on unreviewed questions is your decision, not ours** |
| 5 | Not specified | **No authentication on any endpoint**, and the Railway URL is public | Fine on `127.0.0.1`; on a public URL anyone who finds it can spend your Bedrock budget. A token check is a small change |
| 6 | "60,000 courses" as the source | **Not used.** 60,384 rows, under 1% with a description | A scraped catalogue of *other institutions'* degree listings. Using it would give a course title and nothing else, 60,384 times over. `limitation_periods` and `legal_guidance` from the same database *are* used, because they carry real content |
| 7 | Not specified | The **219,635 decided cases are not searched** | Measured: 0.7 s for a common term, over two minutes for a rare one, because `legal_principle` has no full-text index. **A full-text index on that column, created by whoever owns `larry-legal`, turns this back on with a one-line change** |

**Two known weaknesses, stated rather than buried.**

**Retrieval is literal.** *"What is social disorganisation theory?"* comes back grounded in
Criminology; *"Why do run-down areas have more crime?"* — the same topic in different words — finds
nothing and answers from general knowledge. It fails safe, but a learner asking in their own words
gets less of their course than one who already knows the terminology. PostgreSQL full-text search
with stemming would catch most of it.

**Ranking measures overlap, not authority.** *"How long do I have to bring a defamation claim?"*
scores the statutory line at one — it can only match "defamation" — while chatty course questions
score three on "long", "bring" and "claim". Short sources are now judged on one match, sort ahead on
ties, and two places are reserved for them; but the real fix is term weighting, so that "defamation"
counts for more than "claim". The reason is written where the next person will find it.

**A note on the deployed data.** The Railway PostgreSQL currently holds **1 course**, not 33 —
Networking, OSI, Transport Protocols and Information Security answer from course material there, and
everything else falls to general knowledge or the legal reference database. Loading the full
catalogue needs either the Railway Postgres TCP proxy enabled, or the GitHub repository made private
so the export can ship with the deploy. Locally, all 33 courses and 706 questions are present.

**Before any deployment reachable by anyone else:** the AI provider key currently in use is a
**personal developer key** and should be rotated. The SQL Server account is read-only, which is
correct, but its password is in this package's `.env` — treat the package as confidential.

---

## Sign-off

- [ ] R01–R12 verified against the samples above
- [ ] Global DoD confirmed (345 tests, 222 with no dependencies, lint, portability gates)
- [ ] Difference 1 — generation from course **name** rather than lesson content accepted, or read access to `public.courses` to be provided
- [ ] Difference 2 — the course website is not in the database; a URL to be supplied, or the fallback left off
- [ ] Difference 3 — marking is per-question and not suitable for certification, accepted
- [ ] Difference 4 — practice may draw on 676 unreviewed drafts, accepted or restricted to `ACTIVE`
- [ ] Difference 5 — the deployed URL is public and unauthenticated, accepted or a token to be added
- [ ] Difference 6 — the 60,384 scraped course rows are not used, accepted
- [ ] Difference 7 — full-text index on `all_cases.legal_principle` owned by Consultancy Outfit, or case law left out
- [ ] The full catalogue loaded to the Railway database, or the deployment understood as partial
- [ ] The 31 generated questions reviewed by a subject-matter expert before any are activated
- [ ] AI provider key rotated
- [ ] Approved for release — _________________________ Date: __________

---

*Internal — Confidential. Prepared by Consultancy Outfit.*
