# AI Engineering Planner

Turns a Product Requirements Document (PDF) into a structured engineering implementation plan that
AI coding agents can execute one task at a time.

Each task carries a title, a description ending in acceptance criteria, its dependencies, a computed
implementation order, an S/M/L complexity estimate with a stated rationale, the requirement ids it
satisfies, and a **pasteable prompt for a coding agent** (the bonus deliverable).

> **Reviewers:** [`DELIVERABLES.md`](DELIVERABLES.md) maps each requested deliverable to its file.

---

## Quickstart

```bash
git clone <this repo> && cd ai-engineering-planner
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000
```

Open <http://localhost:8000>, drop in `samples/sample-prd.pdf`, and you get the plan.

**It runs with no credentials.** With no API key and no Claude CLI installed it starts in `demo`
mode and replays a real captured run from `fixtures/demo-plan.json`, so a reviewer can see the full
output shape immediately. The banner says so plainly rather than pretending to be live.

For live planning, pick either provider:

| Provider | How to enable | Notes |
|---|---|---|
| `api` | `export ANTHROPIC_API_KEY=sk-ant-...` | Production path. Sends the PDF natively, preserving tables. |
| `cli` | have `claude` on your PATH | Development path — bills a Claude subscription, no key needed. |
| `demo` | neither of the above | Replays the committed fixture. |

`auto` (the default) resolves api → cli → demo. Override with `PLANNER_PROVIDER`. Copy
`.env.example` to `.env` to set any of it; see that file for all options.

```bash
curl -s localhost:8000/api/health
curl -s -F file=@samples/sample-prd.pdf localhost:8000/api/plan | jq .
.venv/bin/pytest -q
```

A live plan takes **2–4 minutes** — a 17-task plan is ~23,000 output tokens and that is simply what
generating it costs. The second upload of the same PDF is instant (see *Caching* below).

### Watching a run

Because that wait is long enough to look like a hang, the run reports itself on both surfaces. The
terminal prints each stage as it is entered, not when the request finally completes:

```
11:58:55  INFO  planner  validating     2 pages, 3520 chars of text layer
11:58:55  INFO  planner  cache-lookup   plan_id 44d249ee66ea861a MISS
11:58:55  INFO  planner  model-call     cli · claude-sonnet-5 — generating, this is the slow step
11:58:55  INFO  cli      prompt is 8751 chars; waiting up to 600s for the model
12:02:19  INFO  cli      returncode 0, 24193 bytes of stdout
12:02:19  INFO  planner  model-returned 204s, 17 tasks, 23011 output tokens
```

The browser shows the same stages as a live checklist with a running clock. It is not an animation —
it polls the server for the stage the request is genuinely in:

```bash
curl -s -F file=@samples/sample-prd.pdf "localhost:8000/api/plan?trace=demo-1" &
curl -s localhost:8000/api/progress/demo-1 | jq .
# {"stage":"model-call","detail":"cli · claude-sonnet-5 — generating…","elapsed_s":6.0,"done":false}
```

`trace` is optional and client-generated; omit it and everything behaves exactly as before.

---

## The design decision that matters

The brief asks for both a **suggested implementation order** and **dependencies**. If a model emits
both, they can contradict each other — nothing stops task #3 from depending on task #7, and a
reviewer who spot-checks one row finds it.

**So the model is never asked for an order.** It proposes dependencies only; the application
computes the order from the resulting graph.

- `TaskDraft` (what the model fills in) has **no `order` field** — there is nowhere to put one.
- The system prompt forbids stating one, and explains why.
- `app/domain/ordering.py` runs Kahn's algorithm over the dependency graph and assigns `order` 1..N.

The output is correct by construction rather than correct by luck, and `tests/test_ordering.py`
asserts the invariant directly: *every task's order exceeds the order of each of its dependencies.*

Two things fall out of doing it this way:

**Waves.** Every node that becomes ready in the same round of the sort shares a wave — so the plan
shows what can be built *in parallel*, not just a flat 1..N list. The sample output has 17 tasks in
9 waves, with wave 7 holding four independent tasks.

**It never crashes on model sloppiness.** Dangling dependency ids, self-dependencies, duplicate ids
and genuine cycles are each repaired and reported as a `warning` on the response. A cycle drops the
edge closing it and names both tasks. A plan with a noted flaw is worth far more to the user than a
500 after a three-minute wait.

---

## Caching — the same PRD is never paid for twice

`plan_id` is not random, it is **derived from the input**:

```
plan_id = sha256( normalized_text ‖ prompt_version ‖ model ‖ provider_kind )[:16]
```

Because the id is content-addressed, **the store and the cache are the same object** — there is no
second data structure and no invalidation logic. Re-uploading a PRD computes the same id, finds the
plan already in the map, and returns it without calling the model.

The whole correctness argument is that **every input that can change the output is in the key, and
nothing that cannot, is**:

| In the key | Why |
|---|---|
| Normalized text | The PRD's *content*, not its bytes — re-exporting the same document from another tool changes the bytes but not the plan, and should still hit. |
| `prompt_version` | Editing a prompt changes the output, so it must invalidate every cached plan. Nothing to bump by hand: each stage's version is `sha256` of its own composed prompt bytes (see `app/prompts/__init__.py`), so an edit invalidates exactly the stages it affects. |
| `model` | A different model is a different plan. |
| `provider_kind` | The API adapter sends the PDF natively, the CLI adapter sends extracted text. They can legitimately differ, so they must not share a slot. |

Deliberately **not** in the key: filename, upload timestamp, raw bytes. None change the plan, and
any of them would turn every re-upload into a miss.

Measured on the committed sample:

```
first upload    213.6 s   x-plan-cache: MISS
second upload     0.049 s x-plan-cache: HIT     # identical output, zero model calls
```

Reproduce it, including the proof that the two responses are the same and not merely similar:

```bash
curl -s -D /tmp/h1 -F file=@samples/sample-prd.pdf localhost:8000/api/plan -o /tmp/p1.json
curl -s -D /tmp/h2 -F file=@samples/sample-prd.pdf localhost:8000/api/plan -o /tmp/p2.json
grep -i x-plan-cache /tmp/h1 /tmp/h2                                        # MISS, then HIT
diff <(jq -S 'del(.meta)' /tmp/p1.json) <(jq -S 'del(.meta)' /tmp/p2.json)  # no output = identical
curl -s localhost:8000/api/cache                                            # hits:1 misses:1
```

`?refresh=true` bypasses the lookup and overwrites the entry — needed to prove the *model* is stable
on a genuine re-run, not just that the cache echoes itself.

The claim that the key tracks *content* rather than bytes is also checkable.
`samples/sample-prd-reexported.pdf` is the same PRD written out by a second run of the generator —
3,910 bytes against 8,366 — and it lands on the same id, so uploading it after the sample is a HIT:

```bash
.venv/bin/python -c "
from app.services.pdf_validator import validate_and_extract
from app.services.cache_key import compute_plan_id
f = lambda p: compute_plan_id(validate_and_extract(open(p,'rb').read(), p), 'claude-sonnet-5', 'cli')
print(f('samples/sample-prd.pdf'), f('samples/sample-prd-reexported.pdf'))"
# 44d249ee66ea861a 44d249ee66ea861a
```

Storage is behind a `PlanStore` ABC with one implementation today: `InMemoryPlanStore`, an
`OrderedDict` LRU bounded at 100 entries (an unbounded dict in a service that accepts uploads is a
memory leak) guarded by a lock. Two honest consequences: **the cache dies with the process**, and
**multiple uvicorn workers each hold their own map**. Run single-worker, or write the Redis adapter
— which is a new class and a one-line factory change, with nothing else touched. That is why the
port exists.

---

## Architecture

Layered the way a Spring service is — `domain / ports / adapters / services` — so the model call is
one replaceable implementation of one interface rather than something threaded through the app.

```
app/
├── domain/          models.py · ordering.py · document.py    pure logic, no I/O, no framework imports
├── ports/           planner.py                               the Planner ABC
├── adapters/        cli_planner · api_planner · demo_planner · plan_parser · factory
├── services/        planning_service · pdf_validator · cache_key · plan_store · markdown_renderer
├── prompts/         read_system · graph_system · verify_system · sizing_rubric
│                    · agent_prompt_guidance · system_prompt
├── observability.py stage logging + the tracker the browser polls
└── main.py          FastAPI routes only — no business logic
```

`PlanningService.create_plan()` is the whole flow in one readable method:

> validate → derive plan id → cache lookup → *(miss)* call the model → order → scope the prompts →
> render markdown → store

The three planners implement one ABC and share one parser, so **switching provider cannot change
the response contract**. Their one documented divergence: `api_planner` sends the PDF as a native
base64 document block (preserving tables, which is where PRDs put requirements), while
`cli_planner` cannot attach a binary and so sends pypdf-extracted text. That difference is real
enough to change output, which is exactly why `provider_kind` is in the cache key.

### API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | status, active provider, model, prompt version |
| POST | `/api/plan` | multipart `file=@prd.pdf` → full plan. `?refresh=true` forces regeneration, `?provider=` overrides per request, `?trace=` opts into progress reporting |
| GET | `/api/progress/{trace_id}` | `{stage, detail, elapsed_s, done}` for a run in flight — polled once a second by the UI. Optional: omit `?trace=` and nothing changes |
| GET | `/api/plans` | in-memory history, newest first |
| GET | `/api/plans/{id}` | re-fetch by content-addressed id |
| GET | `/api/plans/{id}/markdown` | markdown download |
| GET | `/api/cache` | `{entries, hits, misses, hit_rate}` |

Errors return `{"error": "..."}` with a readable reason and no stack trace: not a PDF (checked by
magic bytes, not the file extension) → 400 · empty → 400 · over 32 MB → 413 · over 100 pages → 422
· provider failure → 502 · zero tasks extracted → 422. An oversized PRD is **rejected, never
silently truncated** — a plan built from a third of a document is worse than no plan.

History browsing uses `store.peek()`, which deliberately does *not* count as a cache hit. Otherwise
paging through past plans would inflate the hit rate into a meaningless number.

---

## The bonus: prompts that are actually pasteable

Every task carries an `agent_prompt` addressed to a fresh agent that has read nothing else. The
model writes the body; **the application appends the last two lines after computing the graph**,
because when the model writes a prompt it does not yet know what ended up upstream or downstream of
that task. Taken verbatim from `samples/sample-output.json`, task #5:

> Implement notification preference settings. Create a `notification_preferences` table (user_id
> PK/FK users, group_added BOOLEAN NOT NULL DEFAULT true, …). Add authenticated GET
> `/api/users/me/notification-preferences` returning the caller's row (creating it with defaults on
> first read if absent), and PATCH … accepting any subset of {group_added, expense_recorded,
> weekly_digest}. **Done when:** GET for a user with no existing row returns all three flags true;
> PATCH `{expense_recorded: false}` persists, and a subsequent GET reflects it while the other two
> remain true.
>
> **Already done (do not rebuild):** Add session-authentication middleware
> **Not your scope (handled separately):** Send group-added and expense-recorded emails; Add weekly
> open-balances digest email job

Those last two lines are the difference between a prompt that reads well and a prompt that stops a
fresh agent from rebuilding its own prerequisite or wandering into the next task's work.

---

## Sample input and output

| File | What it is |
|---|---|
| `samples/sample-prd.pdf` | The test input — "SplitTab", a group expense-splitting PRD with 10 functional and 4 non-functional requirements, chosen for real dependency depth (auth → groups → expenses → settlement → export). |
| `tools/make_sample_prd.py` | The script that generates it, committed so the input is reproducible rather than an opaque binary. |
| `samples/sample-output.json` | **Real application output** from a live run — 17 tasks, 14 requirements, 9 waves, zero warnings. Not hand-written. |
| `samples/sample-output.md` | The same plan rendered as markdown. |
| `fixtures/demo-plan.json` | The model's raw extraction from that run, replayed by demo mode. |
| `samples/sample-prd-reexported.pdf` | The same PRD, different bytes (8,366 vs 3,910). Exists to demonstrate that the cache key is content-addressed — it hits the sample's entry. |
| `samples/req_v1.pdf` | A one-line brief, 37 characters. The file that exposed the text-layer bug in `AI_PROMPTS.md` B9; kept as the regression case, and it plans into 9 tasks across 3 waves. |

---

## Limitations

Stated plainly, because pretending otherwise is worse than the limitation.

- **The cache is in-memory.** It dies with the process and is per-worker. The `PlanStore` port is
  there so Redis or SQLite is a new class, not a refactor.
- **Scanned/image-only PDFs.** The CLI provider rejects them with a 422 pointing at the API
  provider, which can read them natively. "Has a text layer" is judged by characters *per page*
  (see `ASSUMPTIONS.md` 7a) — a heuristic, so the rejection quotes the numbers it measured rather
  than asserting a cause. The cache falls back to hashing raw bytes for these, so two exports of the
  same scan will miss — correct, since we cannot prove they are the same document.
- **Plan quality is not automatically evaluated.** There is no rubric-scored eval harness; quality
  was tuned by reading real outputs against the sample PRD. The prompt iterations that resulted are
  recorded in `AI_PROMPTS.md` section C.
- **No persistence, no auth, no rate limiting.** This is a single-user local tool, not a deployed
  service. Adding any of them is orthogonal to what is being demonstrated here.
- **Latency is dominated by generation**, not by anything the app does — 2–4 minutes on a cache
  miss. The UI says so while it waits rather than looking hung.
- **Very large PRDs are rejected at 100 pages** rather than chunked. Chunking a PRD across model
  calls and merging the resulting task graphs is real work, and doing it badly would silently
  produce a plan that misses requirements.

See `ASSUMPTIONS.md` for the decisions taken where the brief was silent, and `AI_PROMPTS.md` for
every prompt used — both inside the product and while building it.
