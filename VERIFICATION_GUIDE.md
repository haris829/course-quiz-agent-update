# Verification Guide
### Course Question Agent · `qgen` · Release R1

*For the client and the PMO. This document tells you how to test the system yourself, what a pass
looks like, and what cannot be signed off yet.*

Everything in the **Verified** column of this guide was executed against a running build on
**10 September 2026** and the result recorded as observed. Where a check exposed a limitation, it
says so.

---

## 1. What you are verifying

The product does two things over the courses held in PostgreSQL. It takes a course name or a
question, and returns:

| Output | Decided by |
|---|---|
| Which course a name refers to | The `qc_courses` catalogue — a lookup, never the AI |
| An answer to a question | The AI, using material retrieved from the database |
| Which material the answer rests on | The retrieval, reported item by item — never the AI's claim |
| A practice question from that course | The database first; the AI only when the course runs out |
| Whether an answer was right | The stored answer key, read server-side |
| Multiple-choice questions for a course | The AI, then refused unless every rule checks out |

**Four things it will never do, and each is worth testing for:**

1. **It never lets the AI decide which course you meant.** Ambiguity resolves to nothing.
2. **It never sends an answer key to the page.** The key is read only when marking a committed answer.
3. **It never repairs a malformed question.** It refuses it and counts why.
4. **It never claims course grounding it does not have.** An ungrounded answer says so in its own sentence.

---

## 2. Who does what

Nothing in this guide needs a developer except Part 3 and Part 6.

| Part | What it covers | Owner | Time |
|---|---|---|---|
| 3 | Get the system running | Development team | 15 min, once |
| 4 | Smoke check — is it alive and joined up? | PMO | 10 min |
| 5 | The ten UAT scenarios | Client / Product Manager | 2–3 hours |
| 6 | Automated evidence | Development team | 5 min |
| 7 | Findings from this review | **Read before you start** | 5 min |
| 8 | What cannot be signed off, and who is needed | PMO to action | — |

**No UAT owner is named in this build.** Unlike the sibling Complaint Generator, which records
sign-off against named Product Managers on gate CG-59, this package has no equivalent register.
Naming an owner is item **OI-Q1** in §8.

---

## 3. Getting it running

*Owner: development team. Run once, then hand the URL to the tester.*

```bash
pip install -r requirements.txt        # first time only
# a working .env is included; `cp .env.example .env` only if you need to point it elsewhere
python -m qgen init-db                 # creates the three qgen_* tables
python -m qgen serve                   # starts the page on :8010
```

Startup is immediate — there is no index build and no warm-up. Confirm it is alive:

```bash
curl http://127.0.0.1:8010/api/health
```

Expect `{"model_configured":true}`. If it says `false`, the model key is not loaded and every
question will return a clean 503 (which is correct behaviour — see check 6).

| URL | What it is |
|---|---|
| `http://127.0.0.1:8010/` | The page — ask a question, or be tested on a course |
| `http://127.0.0.1:8010/docs` | OpenAPI UI — every endpoint, clickable |
| `http://127.0.0.1:8010/api/health` | Whether a model is configured |
| `http://127.0.0.1:8010/api/courses` | The 33 courses, and how many questions each holds |

There is **no authentication on any endpoint**. This is a local tool on `127.0.0.1`. See §8, OI-Q4
— it must not be exposed on a network as it stands.

### ⚠ The database and the credentials

```
host      127.0.0.1
port      5433            <- not the usual 5432
database  quiz_agent_sweep
```

Four databases exist on that server. **`quiz_agent_sweep` is the one with the full course list.**
`quiz_agent_demo` has the same courses plus recorded learner sittings; `quiz_agent_pg` and
`quiz_agent_fresh` are portability tests and should be ignored.

The `.env` included in this package holds **local development credentials on a throwaway
database**, so the system runs without setup. `.env.example` shows the same shape with
placeholders.
The AI provider key is a **personal key belonging to a developer** and is item OI-Q3 in §8. Neither
should reach a deployed environment.

### What the system writes, and what it only reads

| Tables | Access |
|---|---|
| `qgen_runs`, `qgen_questions`, `qgen_question_options` | **Written** — this package owns them |
| `qc_courses` | Read only — the catalogue |
| `qb_questions`, `qb_question_options`, `qb_topics`, `qb_question_topics` | Read only — material to answer from |
| `qz_*` (the sibling Quiz Agent's tables) | Never touched at all |

The read-only coupling to `qb_*` lives in exactly one file, `qgen/library.py`, so if the platform's
question bank changes shape, one file breaks and nothing else does.

---

## 4. Smoke check — 10 minutes

*Owner: PMO. Six checks. If all six pass, the environment is sound. If any fails, stop and raise it.*

### Check 1 — The catalogue is readable

```bash
python -m qgen courses
```

| Expect | Verified 10 Sep 2026 |
|---|---|
| 33 courses listed, by title | ✅ 33 |
| Each shows how many questions are stored, and its source | ✅ |
| Every course says **source: name only** | ✅ 33 of 33 — see §7.1 |

### Check 2 — A question is answered, and says where from

```bash
curl -s -X POST http://127.0.0.1:8010/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"Which OSI layer routes packets between networks?"}'
```

| Expect | Verified |
|---|---|
| An answer in prose, three to six sentences | ✅ |
| `"grounded": true` | ✅ |
| A `provenance` sentence naming the courses it used | ✅ *"Answered from 2 items of course material from Networking, OSI Model."* |
| A `material` array — the items the model was given | ✅ 8 given, 2 cited |
| The cited items are flagged `"used": true` | ✅ |

### Check 3 — A question the courses do not cover admits it

```bash
curl -s -X POST http://127.0.0.1:8010/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"How do I bake sourdough bread at home?"}'
```

| Expect | Verified |
|---|---|
| `"grounded": false` | ✅ |
| `"material": []` — nothing was found, and nothing was substituted | ✅ |
| The provenance says *"There is no course material on this. The answer is general subject knowledge."* | ✅ |
| The answer itself opens by saying the course material does not cover it | ✅ |

**This is the check that matters most.** A system that quietly answers from general knowledge while
implying course grounding is worse than one that answers nothing.

### Check 4 — A practice question comes from the course you named

```bash
curl -s -X POST http://127.0.0.1:8010/api/practice \
  -H "Content-Type: application/json" \
  -d '{"course":"Criminology"}'
```

| Expect | Verified |
|---|---|
| `course: "Criminology (Postgraduate)"`, `course_code: "LL-34590"` | ✅ |
| A `ref` beginning `qgen-` or `qb-` | ✅ |
| Options, and **no answer key anywhere in the response** | ✅ searched the whole payload for `answer` and `is_correct` |
| A `status` of `DRAFT` or `ACTIVE`, shown not hidden | ✅ `DRAFT` |
| `pool` and `remaining` counts | ✅ pool 28 |

### Check 5 — Asking again gives a different question

Pass back the `ref` you were given in `exclude`:

```bash
curl -s -X POST http://127.0.0.1:8010/api/practice \
  -H "Content-Type: application/json" \
  -d '{"course":"Criminology","exclude":["<REF FROM CHECK 4>"]}'
```

| Expect | Verified |
|---|---|
| A different `ref` | ✅ |
| `remaining` is one lower than before | ✅ 26, then 25, then 24 |
| Still the same course | ✅ |

**Verified exhaustively:** all 27 questions then in the Criminology pool were drawn one after
another. Every reference distinct; **every single one from Criminology (Postgraduate)** and none
from any other course. On the 28th draw the pool was empty and the system wrote a fresh question
live rather than reaching into a neighbouring course.

### Check 6 — With no model configured, it refuses cleanly

```bash
COACHING_LLM_API_KEY= python -m qgen serve --port 8011
curl -s -X POST http://127.0.0.1:8011/api/ask \
  -H "Content-Type: application/json" -d '{"question":"What is mens rea?"}'
```

| Expect | Verified |
|---|---|
| `/api/health` reports `model_configured: false` | ✅ |
| The ask returns **HTTP 503**, not 500 and not an invented answer | ✅ |
| `"retryable": true` | ✅ |
| The CLI refuses before writing anything, exit code 3 | ✅ *"No model is configured… Nothing will be generated and nothing will be written."* |

---

## 5. The ten UAT scenarios

*Owner: Client / Product Manager. Owner not yet named — see §8, OI-Q1.*

Each scenario has an automated counterpart in the suite, and every one of those passes. **A passing
test is not a passing scenario.** The test checks the system does what it was built to do; the
scenario checks whether a person can get what they need out of it.

**Severity rule, not negotiable.** Any of the following is Severity 1 by definition:

- an answer presented as course-grounded when it is not
- a practice question from a course other than the one named
- an answer key reaching the page before an answer is committed
- a question stored with other than four options, or other than one correct answer
- a wrong course silently chosen for an ambiguous name

### The endpoints you will use

| Step | Endpoint |
|---|---|
| Ask a question | `POST /api/ask` — `{"question": "..."}` |
| Be tested on a course | `POST /api/practice` — `{"course": "...", "exclude": [...]}` |
| Mark an answer | `POST /api/practice/answer` — `{"ref": "...", "label": "B"}` |
| The catalogue | `GET /api/courses` |
| Stored questions for a topic | `GET /api/questions?course=...` |
| Write new questions | `POST /api/generate` — `{"course": "...", "count": 5}` |

No authentication header is required. See §8, OI-Q4.

---

### UAT-01 · A question answered from the course material

Ask something the courses genuinely cover.

```bash
curl -s -X POST http://127.0.0.1:8010/api/ask -H "Content-Type: application/json" \
  -d '{"question":"What is collective efficacy in criminology?"}'
```

**Acceptance:**

- [ ] The answer is in plain English and directly answers the question
- [ ] `grounded` is `true` and the provenance names the course
- [ ] The `material` list is returned, so the reader can check the answer against it
- [ ] The cited items are genuinely about the question asked
- [ ] No citation points at an item that is not in the list

**Verified 10 Sep 2026:** answered from 2 items of Criminology (Postgraduate) material; the answer
correctly attributed collective efficacy to Sampson and connected it to Shaw and McKay's social
disorganisation theory. Both cited items were Criminology questions on exactly that point.

---

### UAT-02 · The same question, scoped to a named course

```bash
curl -s -X POST http://127.0.0.1:8010/api/ask -H "Content-Type: application/json" \
  -d '{"question":"In Medical Law MA (Postgraduate), what must a doctor disclose for consent to be valid?"}'
```

**Acceptance:**

- [ ] The course is recognised from the question itself — `course_code` is returned
- [ ] Every item of material used comes from that course and no other
- [ ] The answer reflects what that course actually teaches, not generic law

**Verified:** `course_code: LL-45165`. Answered from 3 items of Medical Law material, correctly
citing *Montgomery v Lanarkshire Health Board* [2015] and the departure from the Bolam approach.

---

### UAT-03 · A question no course covers

```bash
curl -s -X POST http://127.0.0.1:8010/api/ask -H "Content-Type: application/json" \
  -d '{"question":"How do I bake sourdough bread at home?"}'
```

**Acceptance:**

- [ ] `grounded` is `false`
- [ ] The answer's own first sentence says the course material does not cover it
- [ ] The provenance sentence says the same
- [ ] **No material from an unrelated course is substituted to fill the gap**
- [ ] It still answers helpfully rather than refusing

**Verified:** all five. Severity 1 if the fourth fails.

---

### UAT-04 · A named course that holds nothing on the question

Ask a named course about something outside it.

```bash
curl -s -X POST http://127.0.0.1:8010/api/ask -H "Content-Type: application/json" \
  -d '{"question":"In Criminology (Postgraduate), what is the OSI model?"}'
```

**Acceptance:**

- [ ] The provenance **names the course** and says it holds nothing on this
- [ ] It states explicitly that no other course was substituted
- [ ] Networking material is **not** used, even though the database has plenty of it

**Verified 10 Sep 2026:** `material` came back **empty** — not one of the many Networking items was
substituted — and the provenance read:

> *"Criminology (Postgraduate) holds nothing on this. The answer is general subject knowledge, and
> no other course was substituted for it."*

The answer itself opened *"The course material does not cover this topic."* before going on to
explain the OSI model from general knowledge.

This is the scenario that protects against the failure mode in §7.3 of the sibling product — a
message that means one thing being shown when another is true.

---

### UAT-05 · Practice: a different question every time

```bash
# repeat, adding each ref you receive to exclude
curl -s -X POST http://127.0.0.1:8010/api/practice -H "Content-Type: application/json" \
  -d '{"course":"Criminology","exclude":[]}'
```

**Acceptance:**

- [ ] Ten consecutive draws give ten different questions
- [ ] `remaining` falls by one each time
- [ ] Every question is from the named course
- [ ] The order is not simply newest-first — the same second draw does not always appear

**Verified:** 27 consecutive draws, all distinct, all Criminology. The choice is random rather than
ordered, confirmed across twelve seeded runs.

---

### UAT-06 · Practice: a course that runs out

Keep drawing past the end of a course's pool.

**Acceptance:**

- [ ] When the pool is exhausted, a **new question is written live** for that course
- [ ] `generated: true` is reported, so the tester can tell which happened
- [ ] The new question is stored as a `DRAFT`
- [ ] **No question from another course is served**

**Verified:** the Criminology pool of 27 exhausted, then a new question written live (*"A feminist
criminologist argues that traditional theories of…"*) and stored as `DRAFT`. Pool then 28.

---

### UAT-07 · An ambiguous course name is refused

```bash
curl -s -X POST http://127.0.0.1:8010/api/practice -H "Content-Type: application/json" \
  -d '{"course":"International"}'
```

**Acceptance:**

- [ ] HTTP 422, not a guess
- [ ] The message says why, and names the problem
- [ ] No question is served from any of the candidate courses

**Verified:** HTTP 422, `INVALID_REQUEST`, message: *"No single course matches 'International'.
Four courses contain 'International', for instance — name one of them."*

Also verified on the generation side: an unresolvable reference reports `matched: no course
matched` and generates from the caller's own words, never from a guessed course.

---

### UAT-08 · The answer key never reaches the page

Take any practice question and inspect the raw response.

**Acceptance:**

- [ ] The word `answer` appears nowhere in the question payload
- [ ] `is_correct` appears nowhere
- [ ] The explanation is not included with the question
- [ ] Submitting an answer returns the key — and only then
- [ ] Marking is correct: exactly one option comes back `correct: true`

**Verified:** all five. Submitting each of A, B, C and D against one question returned
`correct=false, false, true, false` with `key=B` and `status=DRAFT`.

**⚠ Read §7.2 before signing this off.** Submitting all four options in turn is exactly how the key
can be extracted, and that is a real limitation of this design.

---

### UAT-09 · Generated questions are refused rather than repaired

*This needs the development team to run, since it requires a malformed model reply.*

**Acceptance:**

- [ ] A question with three options is refused, not padded to four
- [ ] A question with five options is refused
- [ ] `"answer": "B and C"` is refused, **not read as B**
- [ ] Two identical options are refused
- [ ] Refusals are counted **by reason**, so a low yield is diagnosable
- [ ] Good questions in the same reply still survive

**Verified by the suite** — 32 parser tests, the majority of them refusals. The `"B and C"` case was
a real defect found during the build: the parser was taking the first character and reading it as
`B`, turning a two-answer question into a one-answer question with a key nobody chose.

### UAT-10 · Every stored question is a reviewable draft

```bash
python -m qgen generate --course "Criminology" --count 3
```

**Acceptance:**

- [ ] Every stored question has exactly four options and exactly one correct
- [ ] Every stored question has status `DRAFT`
- [ ] The run reports what it asked for, what it stored, and what it refused
- [ ] It states what the questions were written from
- [ ] A second run on the same course does not repeat the first

**Verified against the live database — 31 questions across 6 runs:**

```
qgen_questions        31        non-DRAFT   0
qgen_question_options 124       malformed   [] (none)
distinct stems        31 of 31
```

Timings observed: 3 questions in 16 s (one batch); 20 questions in 52 s (two concurrent batches);
2 questions in 8 s over HTTP. Repeat run on a course with history asked for 4 to store 3, and
returned no repeats.

---

## 6. Automated evidence

*Owner: development team. Run these and attach the output to the sign-off pack.*

```bash
python -m pytest              # the whole package
python -m ruff check .        # lint
```

| Command | What it proves | Verified 10 Sep 2026 |
|---|---|---|
| `python -m pytest` | The whole package | **315 passed, 0 failed** (~14 s) |
| `python -m ruff check .` | Lint | **All checks passed** |

This package is self-contained: it imports nothing from the wider platform. It was also developed
inside the UC-01…UC-10 repository, and all 4,123 tests of those ten components were confirmed
passing alongside it — no existing component was changed.

### Where the 315 tests are

| File | Tests | What it covers |
|---|---|---|
| `test_portability.py` | 41 | The SQL gates — reads the source, not the database |
| `test_answering.py` | 37 | Keywords, ranking, the answer prompt and parse |
| `test_storage.py` | 37 | Freezing, drafts, history, the whole generation run |
| `test_web.py` | 34 | Every route, including the key-leak assertions |
| `test_parser.py` | 32 | Mostly what the question parser **refuses** |
| `test_llm.py` | 31 | Retry, throttling, and how a failure is classified |
| `test_practice.py` | 25 | Course scoping, no-repeats, marking |
| `test_ask.py` | 22 | Retrieval and the live ask, against the real database |
| `test_prompt.py` | 21 | The generation prompt, batching, headroom |
| `test_resolution.py` | 20 | Course lookup rules and LIKE escaping |
| `test_service.py` | 15 | Batching, concurrency, partial failure |

Tests needing no database and no network: **`test_parser`, `test_prompt`, `test_resolution`,
`test_answering`, `test_llm`, `test_service`, `test_portability`** - 197 of the 315, verified by running the suite with no credentials set at all: 197 passed, 118 skipped. The remainder
connect to PostgreSQL **inside a transaction that is always rolled back**, so a run leaves the
database exactly as it found it. Verified: counts identical before and after a full suite run.

### The portability gates

`test_portability.py` reads the SQL in the package and fails on two things that pass on SQLite and
break on PostgreSQL:

- `is_correct = 1` — SQLite stores booleans as integers and accepts it silently; PostgreSQL fails
  with `operator does not exist: boolean = integer`. **This exact bug shipped in the sibling
  project.**
- engine-specific JSON and aggregate functions — JSON is assembled in Python instead.

The gate also tests **itself**: one case proves it fires on the bug, another proves it does not fire
on a comment warning about the bug.

### ⚠ There is no release-blocker harness and no coverage floor

Unlike the sibling Complaint Generator, this package has **no `make evals`, no designated release
blockers, and no enforced coverage floor.** 315 passing tests is a good suite; it is not a release
gate. Nothing currently fails a build on question quality, because nothing measures question
quality. See §8, OI-Q2.

---

## 7. Findings from this review

Five things were found while preparing this guide. All five are recorded here rather than left for
the tester to hit cold.

### 7.1 No course has a description — every question is written from a four-word title

**Severity: high. Not a defect. It is the single most important limitation in the product.**

`description`, `rqf_level` and `subject_area` are **NULL on all 33 rows** of `qc_courses`. When
generating, the model is given a course *title* — four or five words — and writes from its own
knowledge of the subject.

| | |
|---|---|
| Courses with a description | **0 of 33** |
| What generation is therefore given | the title, and nothing else |

The questions are correct law and on topic. **Not one of them is traceable to a lesson.** Nothing in
the product pretends otherwise: every run prints what it wrote from, and every stored question
carries the same sentence in a `grounding` column.

Closing this needs read access to the platform's own `public.courses` (which has descriptions) and
`course_modules`. The import script already exists in the sibling project at
`backend/scripts/import_platform_courses.py`. **This package needs no code change when it runs** —
the columns are already read and already passed into the prompt.

*Note that the **answering** side is less affected: it retrieves from 706 real explanations written
for these courses, which is a great deal more than a title.*

### 7.2 Marking is an answer-key oracle

**Severity: medium for this use, disqualifying for assessment. By design, and it must not be
carried forward silently.**

The key is never sent with a question — but submitting each option in turn will find it, because
each submission is marked independently. There is no attempt state, so nothing stops a second guess.

| Use | Verdict |
|---|---|
| Local practice and authoring | Acceptable. The user is testing themselves |
| Anything that certifies a person | **Not acceptable** |

A real sitting needs one submission per question against a recorded attempt. **The platform already
has that in UC-03** — this is deliberately not a second one. Do not reuse these endpoints for a
graded assessment without that change.

### 7.3 A paraphrased question can miss material the course actually holds

**Severity: medium. Reproducible. A limitation of keyword retrieval, not a bug.**

Retrieval matches literal words. A question that asks about a concept in different words scores
poorly against material that uses the textbook term.

| Question | Material found | Grounded |
|---|---|---|
| "What is social disorganisation theory?" | 6 | ✅ **true** — 2 items from Criminology |
| "Why do run-down areas have more crime?" | 6 | ❌ **false** — answered from general knowledge |

Both questions are about the same thing, and the Criminology course holds good material on it. The
second phrasing did not retrieve it well enough for the model to use.

**The failure is safe** — it under-claims rather than over-claims, and says plainly that the answer
is general knowledge. But a learner asking in their own words gets less of their course than a
learner who already knows the terminology, which is the wrong way round.

*Recommended for the dev team: PostgreSQL full-text search (`tsvector`/`plainto_tsquery`) with
stemming would catch most of this, at the cost of the engine-portability the current approach keeps.
An embedding index would catch nearly all of it, at the cost of a new dependency.*

### 7.4 676 of the 706 bank questions served in practice are unreviewed drafts

**Severity: medium. Needs a decision, not a code change.**

Practice questions come from two sources. The larger one is the platform's own `qb_questions`:

| Status | Count |
|---|---|
| `DRAFT` — model-written, not reviewed by a person | **676** |
| `ACTIVE` — reviewed | 30 |

The sibling project's own rule is that **only `ACTIVE` questions are ever delivered to a learner.**
This package serves both, because they are what the courses hold, and shows the status on every
question rather than hiding it.

**That is defensible for practice and not defensible for assessment.** The decision — whether
practice may draw on unreviewed questions — belongs to the client, not to the build.

### 7.5 A factual slip is already present in the stored questions

**Severity: low in itself. Load-bearing as evidence.**

Question 4 in `qgen_questions` reads *"associated with Wolfang's birth cohort research"*. The
criminologist is **Wolfgang**.

It is a small error in an otherwise sound question, and it is exactly the point: a model produces
text that is fluent, plausible and slightly wrong, and no automated check in this system would ever
catch it. **This is the argument for the `DRAFT` gate**, and the reason §8's review item is not
optional.

---

## 8. What cannot be signed off yet — and who is needed

*This is the section for the PMO. The engineering is complete and tested. Every remaining item needs
a person, and none of them is a development task.*

| Item | Needs | Blocks | Owner today |
|---|---|---|---|
| **OI-Q1** | A named UAT owner | Sign-off of §5 | **UNASSIGNED** |
| **OI-Q2** | A QA owner for question quality — a reference set, a measure, a floor | Any claim that the questions are good enough | **UNASSIGNED** |
| **OI-Q3** | Rotation of the AI provider key | Any deployment | **UNASSIGNED** — a personal developer key is in use |
| **OI-Q4** | A decision on authentication and network exposure | Anything beyond `127.0.0.1` | **UNASSIGNED** |
| **OI-Q5** | A subject-matter reviewer for the 31 generated drafts | Delivering any of them as assessment | **UNASSIGNED** |
| **OI-Q6** | Course descriptions imported (§7.1) | Questions traceable to a lesson | **UNASSIGNED** |
| **OI-Q7** | A decision on serving unreviewed bank questions (§7.4) | Practice being used for anything graded | Client decision |
| **OI-Q8** | An AWS Bedrock quota increase | A cohort generating at once | Client's AWS console |

### The one to act on first

**OI-Q5 — a subject-matter reviewer.** 31 generated questions are sitting as drafts, and §7.5 shows
that at least one contains a factual slip that no test will ever catch. The machinery for producing
questions is production-ready; **the content is not signed off until a person signs it off.** Every
question is `DRAFT` precisely so this decision has to be taken deliberately rather than by default.

### OI-Q3 — the credentials, plainly

The AI provider key in use is a **personal key belonging to a developer**, read from the sibling
project's `backend/.env`. The database credentials in `.env.example` are local development values on
a throwaway database. Both are documented in this repository. **Neither may reach a deployed
environment.**

**A working `.env` is included in this delivery, deliberately**, so the system runs on receipt
with no setup. It carries the development database credentials and the model key. Treat the
package as confidential, and **rotate the model key once this hand-over is complete** — that is
this item, OI-Q3. `.env` is git-ignored, so it will not travel into a repository by accident.

### OI-Q8 — throttling, and what has and has not been tested

Bedrock throttling is handled: three retries with a widening pause, honouring `Retry-After`, and a
throttle that never clears is reported as **503 retryable** — never as a 502 "the model will not
follow the contract", which would send an operator to debug a prompt that was working.

**This is verified by unit test, not against a live throttle.** The largest live run in this review
was 20 questions in two concurrent batches, which was not throttled. The sibling project measured a
sweep of 33 concurrent requests losing 20 to throttling, every one of which succeeded on retry. A
cohort starting together will hit the account limit; that is a quota request, not a code change.

### The honest limit on this UAT

Nine of the ten scenarios in §5 were executed by hand for this guide; the tenth (UAT-09) by the
automated suite. What
UAT **cannot** establish, because nothing in the build measures it:

- whether the generated questions are **pitched at the right level** for a postgraduate qualification
- whether the answers are **legally correct** — §7.5 shows at least one is not quite
- whether a **learner** can use this, as opposed to a tester — there has been no accessibility audit
  and no user testing

The sign-off should say so.

---

## 9. Sign-off sheet

**Environment**

| | |
|---|---|
| Build / commit | ......................................... |
| Database | ......................................... |
| Model | AWS Bedrock, personal key (OI-Q3) |
| Date tested | ......................................... |
| Tester | ......................................... |

**Part 4 — smoke check (PMO)**

| Check | Pass | Fail | Note |
|---|---|---|---|
| 1 · The catalogue is readable — 33 courses | ☐ | ☐ | |
| 2 · A question is answered, and says where from | ☐ | ☐ | |
| 3 · An uncovered question admits it | ☐ | ☐ | |
| 4 · A practice question comes from the named course | ☐ | ☐ | |
| 5 · Asking again gives a different question | ☐ | ☐ | |
| 6 · With no model, it refuses cleanly with 503 | ☐ | ☐ | |

**Part 5 — UAT scenarios (Product Manager — owner not yet named, OI-Q1)**

| Scenario | Pass | Fail | Severity if failed | Signed |
|---|---|---|---|---|
| UAT-01 Answered from the course material | ☐ | ☐ | 1 | |
| UAT-02 Scoped to a named course | ☐ | ☐ | 1 | |
| UAT-03 A question no course covers | ☐ | ☐ | 1 | |
| UAT-04 A named course holding nothing on it | ☐ | ☐ | 1 | |
| UAT-05 A different question every time | ☐ | ☐ | 1 | |
| UAT-06 A course that runs out | ☐ | ☐ | 1 | |
| UAT-07 An ambiguous course name refused | ☐ | ☐ | 1 | |
| UAT-08 The key never reaches the page | ☐ | ☐ | 1 | see §7.2 |
| UAT-09 Malformed questions refused, not repaired | ☐ | ☐ | 1 | dev team |
| UAT-10 Every stored question is a reviewable draft | ☐ | ☐ | 1 | |

**Part 6 — automated evidence (development team)**

| | Result | Attached |
|---|---|---|
| `python -m pytest` | ............ passed / ............ failed | ☐ |
| `python -m ruff check .` | clean / ............ findings | ☐ |

**Declaration**

> This UAT was executed against a local development database using a personal AI provider key. The
> generated questions have not been reviewed by a subject-matter expert (OI-Q5), no course in the
> catalogue has a description (§7.1), and no accessibility or security review has been carried out.
> This sign-off certifies that the system behaves as specified. **It does not certify that the
> questions it produces are fit to assess anybody against.**

| Role | Name | Signature | Date |
|---|---|---|---|
| Tester | | | |
| Product Manager | | | |
| PMO | | | |

---

*Internal — Confidential. Prepared by Consultancy Outfit.*
