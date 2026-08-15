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
mode and replays **one recorded response per stage** from `fixtures/demo/`, so a reviewer sees the
full output shape — all three stages, not a canned blob — without holding a key. The banner says so
plainly rather than pretending to be live, and `fixtures/demo/provenance.json` records exactly where
each of the three files came from and which parts of one are synthesized.

For live planning, pick either provider:

| Provider | How to enable | Notes |
|---|---|---|
| `api` | `export ANTHROPIC_API_KEY=sk-ant-...` | Production path. Sends the PDF natively, preserving tables. |
| `cli` | have `claude` on your PATH | Development path — bills a Claude subscription, no key needed. |
| `demo` | neither of the above | Replays `fixtures/demo/` — one recorded response per stage. |

`auto` (the default) resolves api → cli → demo. Override with `PLANNER_PROVIDER`. Copy
`.env.example` to `.env` to set any of it; see that file for all options. Which *model* runs which
stage is a separate knob and lives in `models.json` — see *Per-stage models*.

```bash
curl -s localhost:8000/api/health
curl -s -F file=@samples/sample-prd.pdf localhost:8000/api/plan | jq .
.venv/bin/pytest -q
```

A live plan is **three model calls, not one** — read, then graph, then verify — and a cold run takes
roughly **six minutes**. Measured on `samples/sample-prd.pdf` through the CLI provider with the
shipped `models.json`:

| Stage | Model | Time | Tokens in → out |
|---|---|---|---|
| ① read — PRD → requirements | `claude-sonnet-5` | 33.3 s | 27,744 → 3,285 |
| ② graph — requirements → tasks + dependencies | `claude-opus-5` | 268.9 s | 23,786 → 23,593 |
| ③ verify — review the ordered plan | `claude-opus-5` | 86.4 s | 31,435 → 6,747 |
| | | **388.6 s** | **82,965 → 33,625** |

One run, not a best-of — the numbers move a little between runs because the models do. Ordering
happens between ② and ③ and costs nothing: it is `app/domain/ordering.py`, not a model call.
Graphing is 69% of the wall clock and 70% of the output tokens, which is the entire argument for
*Per-stage models* below — that is the one stage worth paying a stronger model for.

The second upload of the same PDF is instant, and editing one stage re-runs that stage and the ones
after it while the ones before load from disk — see *Caching*.

### Watching a run

Because that wait is long enough to look like a hang, the run reports itself on both surfaces. The
terminal prints each stage as it is entered, not when the request finally completes:

```
14:50:21  INFO  planner  received       sample-prd.pdf (3 KB)
14:50:21  INFO  planner  validating     2 pages, 3520 chars of text layer
14:50:21  INFO  planner  cache-lookup   intent 0e5a547fafd1c650 BYPASSED (refresh)
14:50:21  INFO  planner  reading        cli · claude-sonnet-5 — extracting requirements from the PDF
14:50:21  INFO  cli      stage read: running claude -p --output-format json --model claude-sonnet-5 …
14:50:55  INFO  cli      stage read: returncode 0, 7116 bytes of stdout
14:50:55  INFO  planner  reading        14 requirements, 10 open questions (miss)
14:50:55  INFO  planner  graphing       claude-opus-5 — planning tasks and dependencies from the understanding
14:50:55  INFO  cli      stage graph: running claude -p --output-format json --model claude-opus-5 …
14:55:23  INFO  cli      stage graph: returncode 0, 44183 bytes of stdout
14:55:23  INFO  planner  graphing       19 tasks drafted (miss)
14:55:23  INFO  planner  ordering       topological sort over the dependency graph
14:55:23  INFO  planner  ordering       19 tasks → 8 waves, 0 warnings
14:55:23  INFO  planner  verifying      claude-opus-5 — reviewing the ordered plan against the understanding
14:55:23  INFO  cli      stage verify: running claude -p --output-format json --model claude-opus-5 …
14:56:50  INFO  cli      stage verify: returncode 0, 7687 bytes of stdout
14:56:50  INFO  planner  verifying      8 improvements, 6 open questions (miss)
14:56:50  INFO  planner  rendering      markdown
14:56:50  INFO  planner  stored         plan_id a2b44d4d41a5f2ef — a re-upload of this PRD is now free
```

Two things are visible there that a single progress bar would hide: which stage the time is actually
going into (four and a half minutes of the six are one line to the next), and the model each stage
ran on — `--model claude-sonnet-5` for read, `--model claude-opus-5` for graph and verify, one
subprocess each. Every stage line ends in `(miss)` or `(hit-disk)`, so a partial re-run is legible as
it happens rather than only in the response.

The browser shows the same stages as a live checklist with a running clock. It is not an animation —
it polls the server for the stage the request is genuinely in:

```bash
curl -s -F file=@samples/sample-prd.pdf "localhost:8000/api/plan?trace=readme-2" &
curl -s localhost:8000/api/progress/readme-2 | jq -c .
# {"trace_id":"readme-2","stage":"graphing","detail":"claude-opus-5 — planning tasks and dependencies
#  from the understanding","elapsed_s":221.0,"done":false,"error":null}
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

## Per-stage models

The three stages are not equally hard, so they do not get the same model. Reading a PRD into a
requirement list is extraction. Proposing what a twenty-task dependency graph should be, and
reviewing it, is reasoning. One tracked file says which is which:

```json
{
  "default": "claude-sonnet-5",
  "read": "",
  "graph": "claude-opus-5",
  "verify": "claude-opus-5"
}
```

A blank means *not chosen yet* and falls through, so `read` runs on `default`. Resolution per stage,
highest first: `PLANNER_MODEL_<STAGE>` → this stage's entry → `"default"` → `PLANNER_MODEL`. The env
vars exist so CI can pin one stage without editing a tracked file; the file is the human knob.

Four things worth noting about how it is wired:

- **`models.json` belongs in the repo; `.env` does not.** It holds no secret, and which model plans
  the graph is a property of the project, not of one developer's machine — a reviewer should be able
  to see the choice and reproduce the run.
- **Both providers honour it, including the CLI**, because each stage is its own call — on the CLI,
  its own `claude -p --model …` subprocess. So per-stage models work on a subscription with no API
  key at all.
- **A malformed file degrades, it does not crash.** Unreadable or not a JSON object → a warning and
  every stage falls back to one model. A run on one model is worse than a run on three; a service
  that will not boot is worse than both.
- **Each stage's model is in that stage's cache key**, so changing one line here re-runs exactly the
  stages below it and reuses the ones above. Editing `graph` re-runs graph and verify; editing
  `verify` re-runs verify alone.

The evidence is the timing table in *Quickstart*: graph is 69% of the wall clock and 70% of the output
tokens. If exactly one stage is worth a stronger model, the measurement says which.

---

## Caching — the same PRD is never paid for twice, and a changed stage is never paid for again

No id here is random. Each stage's key is derived from the stage's own inputs, and each links to the
one before it, so the keys form a chain (`app/services/cache_key.py`):

```
doc_id     = sha256( normalized_text )                                              # the PRD itself
read_key   = stage_key("read",   parent=doc_id,    payload_hash="",               pv, model, kind)
graph_key  = stage_key("graph",  parent=read_key,  payload_hash=H(understanding), pv, model, kind)
verify_key = stage_key("verify", parent=graph_key, payload_hash=H(ordered_plan),  pv, model, kind)
plan_id    = verify_key       # the plan id IS the terminal link of the chain
```

`pv` and `model` are always *that stage's* prompt version and *that stage's* model. That one detail
is what makes the chain useful: repointing the graph stage at a stronger model, or editing only the
verifier's prompt, moves the keys from that stage down and leaves the ones above untouched — so the
earlier stages load from disk and only the affected ones re-run.

`payload_hash` is there because the parent key alone is not enough. Models are non-deterministic and
`?refresh=true` exists, so two runs can share a `read_key` and still produce different
understandings; without hashing what the stage actually consumed, a stale graph could attach itself
to a freshly regenerated understanding.

The correctness argument is unchanged from the single-call design — **every input that can change a
stage's output is in that stage's key, and nothing that cannot, is** — it just now applies per stage:

| In the key | Why |
|---|---|
| `parent` | A graph derived from a different understanding is a different graph. This is what makes it a chain rather than three independent caches. |
| `payload_hash` | The canonical bytes this stage actually consumed, `sort_keys`-canonicalised so a harmless field reorder cannot cause a spurious miss. |
| `prompt_version` | Editing a prompt changes the output. Nothing to bump by hand: each stage's version is `sha256` of *its own* composed prompt bytes (`app/prompts/__init__.py`), so an edit invalidates exactly the stages it affects. The `sizing_rubric.txt` fragment is composed into both graph and verify, so editing it correctly moves both. |
| `model` | A different model is a different answer — and models are set per stage, so this is `LlmClient.model_for(stage)`, not one global. |
| `kind` | The API adapter sends the PDF natively, the CLI adapter sends extracted text. They can legitimately differ, so they must not share a slot. |

Deliberately **not** in any key: filename, upload timestamp, raw bytes of a text-bearing PDF. None
change the output, and any of them would turn every re-upload into a miss.

### Two tiers, deliberately different in kind

- **L1 — in memory, keyed by `intent_key`.** Computable from the upload alone, *before* any model
  call, because it depends on nothing but the PDF and the configuration. A re-upload of an identical
  PRD returns the stored object without touching the disk or the model — the second response is not
  merely equivalent to the first, it *is* the first. It folds in **every** stage's model, not one,
  so repointing any single stage has to miss here too; otherwise L1 would keep serving a plan the
  per-stage keys had already invalidated.
- **L2 — on disk, keyed per stage**, under `artifacts/<doc_id>/<stage>/<stage_key>.json`. This is
  where partial reuse lives. On an L1 miss the chain runs, and each stage checks its own key first.

### The proof

This README's own history is the demonstration. The verifier's prompt was rewritten *and* its model
moved from `claude-sonnet-5` to `claude-opus-5` — two independent changes, both landing on stage 3
only. The next run:

```
[('read',   'hit-disk',  0.0s, 'claude-sonnet-5'),
 ('graph',  'hit-disk',  0.0s, 'claude-opus-5'),
 ('verify', 'miss',     75.2s, 'claude-opus-5')]
```

Six minutes of work became seventy-five seconds. Nothing was overwritten to do it:
`artifacts/946a397147f8e73d/` keeps every key it has ever computed side by side, so the review before
the rewrite and the one after it both still exist, each fetchable by its own `plan_id`.

A whole-plan re-upload is faster still:

```
first upload    388.6 s   x-plan-cache: MISS
second upload     0.030 s x-plan-cache: HIT     # identical output, zero model calls
```

Reproduce it, including the proof that the two responses are the same and not merely similar:

```bash
curl -s -D /tmp/h1 -F file=@samples/sample-prd.pdf localhost:8000/api/plan -o /tmp/p1.json
curl -s -D /tmp/h2 -F file=@samples/sample-prd.pdf localhost:8000/api/plan -o /tmp/p2.json
grep -i x-plan-cache /tmp/h1 /tmp/h2                                        # MISS, then HIT
diff <(jq -S 'del(.meta)' /tmp/p1.json) <(jq -S 'del(.meta)' /tmp/p2.json)  # no output = identical
curl -s localhost:8000/api/cache                                            # hits:1 misses:1
```

`?refresh=true` bypasses the lookup and re-runs all three stages; `?from=<stage>` re-runs one stage
and everything downstream of it, which is how the 75-second run above was produced.

The claim that the key tracks *content* rather than bytes is also checkable.
`samples/sample-prd-reexported.pdf` is the same PRD written out by a second run of the generator —
8,366 bytes against 3,910 — and it lands on the same `doc_id`, so uploading it after the sample is
a HIT:

```bash
.venv/bin/python -c "
from app.services.pdf_validator import validate_and_extract
from app.services.cache_key import doc_id
f = lambda p: doc_id(validate_and_extract(open(p,'rb').read(), p))
print(f('samples/sample-prd.pdf'), f('samples/sample-prd-reexported.pdf'))"
# 946a397147f8e73d 946a397147f8e73d
```

Storage is behind a `PlanStore` ABC with one implementation today: `InMemoryPlanStore`, an
`OrderedDict` LRU bounded at 100 entries (an unbounded dict in a service that accepts uploads is a
memory leak) guarded by a lock. Two honest consequences: **the L1 cache dies with the process**, and
**multiple uvicorn workers each hold their own map**. Run single-worker, or write the Redis adapter
— which is a new class and a one-line factory change, with nothing else touched. That is why the
port exists. L2 on disk survives a restart, so even after the process dies a re-upload skips every
model call and only re-renders.

---

## Architecture

Layered the way a Spring service is — `domain / ports / adapters / agents / services` — so a model
call is one replaceable implementation of one interface rather than something threaded through the
app.

```
app/
├── domain/          models.py · ordering.py · document.py    pure logic, no I/O, no framework imports
├── ports/           llm.py                                   the LlmClient ABC — one method, complete()
├── adapters/        api_client · cli_client · fixture_client · factory      three transports
├── agents/          reader · graph · verifier · base · json_parser          three stages, one parser
├── services/        planning_service · cache_key · artifact_store · plan_store
│                    · pdf_validator · markdown_renderer
├── prompts/         read_system · graph_system · verify_system
│                    · sizing_rubric · agent_prompt_guidance
├── observability.py stage logging + the tracker the browser polls
└── main.py          FastAPI routes only — no business logic
```

`PlanningService.create_plan()` is the whole flow in one readable method:

> validate → intent lookup (L1) → ① read → ② graph → order → ③ verify → render → store

The split that matters is **transports below, agents above**. There is one port, `LlmClient`, with a
single `complete()` method and three implementations — API, CLI, fixture. Above it sit three agents,
one per stage, each owning its prompt and its response schema and nothing else; all three parse
through one `json_parser`. So a provider is a *transport* choice and a stage is a *reasoning* choice,
and neither can affect the other: **switching provider cannot change the response contract**, and
adding a fourth stage would not touch an adapter.

That is also what makes per-stage models fall out rather than be built — each stage is already its own
`complete()` call, so the model is just an argument to it. On the CLI it is literally its own
subprocess.

The adapters' one documented divergence: `api_client` sends the PDF as a native base64 document block
(preserving tables, which is where PRDs put requirements), while `cli_client` cannot attach a binary
and so sends pypdf-extracted text. That difference is real enough to change output, which is exactly
why the provider kind is in every stage key.

### What the third stage is for

The verifier **reviews the plan; it does not gate it.** It never sees the PRD — it is handed the
reader's understanding and the ordered plan, both of which are themselves model output, so a pass/fail
verdict from it would be one model grading another's work with strictly less information than that
model had. Instead it returns two lists: **improvements** that would make the plan better for an AI
coding agent, and **open questions** the PRD genuinely leaves for a human, each citing the task or
requirement it is about.

Three consequences, all deliberate:

- Its output renders **at the bottom**, under `## Review`, and never enters `warnings`. Advice on a
  working plan is not a problem with it, and a red verdict at the top of a usable plan is worse than
  no verdict at all.
- `Verification` has **no `tasks` field**. A stage that can rewrite the graph can silently break the
  topological guarantee that is the whole reason the ordering is trustworthy — and do it in a way no
  reader could detect afterwards.
- A failed verify still returns a plan. `AgentError` subclasses `LlmError`, so a drifted or
  unparseable review degrades to a warning on an otherwise complete response.

The honest limit: this stage is advisory and **is not covered by the test suite** — the green tests
are the ordering invariant and the validator, not the quality of a review.

### API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | status, active provider, whether each provider is reachable, and the per-stage `models` and `prompt_versions` maps |
| POST | `/api/plan` | multipart `file=@prd.pdf` → full plan. `?refresh=true` re-runs all three stages, `?from=<stage>` re-runs that stage and everything downstream, `?provider=` overrides per request, `?trace=` opts into progress reporting |
| GET | `/api/progress/{trace_id}` | `{stage, detail, elapsed_s, done}` for a run in flight — polled once a second by the UI. Optional: omit `?trace=` and nothing changes |
| GET | `/api/plans` | in-memory history, newest first |
| GET | `/api/plans/{id}` | re-fetch by content-addressed id |
| GET | `/api/plans/{id}/markdown` | markdown download |
| GET | `/api/cache` | `{entries, hits, misses, hit_rate}` |

`/api/health` is the fastest way to see the whole configuration at once, including which model each
stage will use and whether it can run without a key at all:

```json
{ "status": "ok", "provider": "cli",
  "models":          { "read": "claude-sonnet-5", "graph": "claude-opus-5", "verify": "claude-opus-5" },
  "prompt_versions": { "read": "read-v1.34fcd626", "graph": "graph-v1.a1b3db5c", "verify": "verify-v2.4026868c" },
  "api_key_present": false, "cli_available": true, "fixture_available": true }
```

A plan response carries the whole pipeline, not just the task list: `understanding` (what stage ①
extracted), `tasks` (② and the ordering), `verification` (③), `warnings`, `markdown`, and a `meta`
block with `plan_id`, the per-stage `stages` array — each with its own `cache` state, `model` and
elapsed time — and the overall cache result.

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
| `samples/sample-output.json` | **Real application output** from a live run — 17 tasks, 14 requirements, 9 waves, zero warnings. Not hand-written. **Pinned from the earlier single-call design**, before the pipeline was split into read → graph → verify: kept as-is rather than regenerated, so the committed output is one a reviewer can check line by line against the run that produced it. A run today over the same PRD produces a different decomposition — 19 and 24 tasks on two runs — and a `## Review` section at the bottom. |
| `samples/sample-output.md` | The same plan rendered as markdown. |
| `fixtures/demo-plan.json` | That run's raw model response, kept as the capture it is. |
| `fixtures/demo/` | What demo mode actually replays — `read.raw.txt`, `graph.raw.txt`, `verify.raw.txt`, one per stage, re-projected from that capture by `tools/make_demo_fixtures.py`. `provenance.json` records for each file where it came from and, for the review, that it is computed rather than captured — the pre-split design had no third stage to capture one from. |
| `samples/sample-prd-reexported.pdf` | The same PRD, different bytes (8,366 vs 3,910). Exists to demonstrate that the cache key is content-addressed — it hits the sample's entry. |
| `samples/req_v1.pdf` | A one-line brief, 37 characters. The file that exposed the text-layer bug in `AI_PROMPTS.md` B9; kept as the regression case, and it plans into 9 tasks across 3 waves. |

---

## Limitations

Stated plainly, because pretending otherwise is worse than the limitation.

- **The L1 cache is in-memory.** It dies with the process and is per-worker. The `PlanStore` port is
  there so Redis or SQLite is a new class, not a refactor. L2 (per-stage, on disk under `artifacts/`)
  survives a restart, so the cost of losing L1 is a re-render, not three model calls.
- **The review is advisory, and untested.** The third stage suggests improvements and names open
  questions; it does not gate, and nothing in the test suite asserts that its advice is good. It also
  never sees the PRD — it reasons over two upstream model outputs, which is exactly why it offers
  suggestions instead of a verdict.
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
- **Latency is dominated by generation**, not by anything the app does — a cold run is *three*
  sequential model calls and takes about six minutes, two thirds of it in the graph stage. The three
  stages cannot be parallelised: each one's input is the previous one's output. The UI says so while
  it waits rather than looking hung, and the per-stage cache means the second run of anything is
  usually much less than that.
- **Very large PRDs are rejected at 100 pages** rather than chunked. Chunking a PRD across model
  calls and merging the resulting task graphs is real work, and doing it badly would silently
  produce a plan that misses requirements.

See `ASSUMPTIONS.md` for the decisions taken where the brief was silent, and `AI_PROMPTS.md` for
every prompt used — both inside the product and while building it.
