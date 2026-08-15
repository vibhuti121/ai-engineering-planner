# AI prompts used

Two different things are called "prompts" in this project and they are kept apart:

- **Section A** — the prompts that ship *inside the product*. These are what the application sends
  to the model at runtime. They live as `.txt` files under `app/prompts/`, every one of them is
  covered below, and Section A ends with how they are composed and versioned.
- **Section B** — the prompts *I* used while building the application, and the corrections that
  were needed. This is the development trail, written as the work happened.
- **Section C** — what changed in the product prompt across iterations, and why.

---

## Section A — prompts that ship inside the product

### A1. `app/prompts/system_prompt.txt`

Sent as the `system` parameter by the API adapter and prepended to the prompt by the CLI adapter.
Both adapters send it byte-identically, which is what makes the two providers interchangeable.

This is the **single-shot** prompt: one call, PRD in, whole plan out. A3–A6 below are the staged
prompts that split the same job into read → plan → audit; the rules here are the ancestor of the
rules there, and the reasoning behind each one is recorded in Section C.

```text
You are a staff engineer breaking a Product Requirements Document into an implementation plan that
will be executed by AI coding agents, one task at a time, with no human in the loop between tasks.

Return ONE JSON object and nothing else. No prose before it, no markdown fence around it.

{
  "project_summary": "2-4 sentences: what is being built, for whom, and the single hardest part.",
  "requirements": [
    {"id": "FR-1", "text": "the requirement, in the PRD's own words where possible", "category": "auth"}
  ],
  "tasks": [
    {
      "id": "kebab-case-slug",
      "title": "Imperative and specific, max 8 words",
      "description": "What to build, then acceptance criteria.",
      "dependencies": ["other-task-id"],
      "complexity": "S",
      "rationale": "One sentence justifying that complexity band.",
      "requirement_ids": ["FR-1"],
      "agent_prompt": "A prompt a fresh coding agent can execute with no other context."
    }
  ]
}

RULES

1. NEVER state an implementation order. Do not number the tasks, do not add an "order" or "step"
   field, and do not rely on array position to convey sequence. You describe *dependencies*; the
   application computes the order from them with a topological sort. A stated order that disagreed
   with the dependency graph would be a contradiction in the output.

2. Dependencies are DIRECT prerequisites only, and each must be the `id` of another task in this
   same response. If A needs B and B needs C, then A depends on B — do not also list C. Never
   invent an id you did not define. A task that can start immediately has an empty array.

3. Task ids are stable kebab-case slugs describing the work: `auth-jwt-issuance`,
   `settlement-algorithm`, `expense-create-api`. Never `task-1`.

4. Every task names concrete artifacts — file paths, endpoint routes with methods, table and column
   names, function names. "Set up the backend" is not a task; "POST /api/expenses persists an
   expense row and its splits" is.

5. Every description ends with acceptance criteria: the observable condition that proves the task
   is done. Prefer something checkable — a status code, a row that exists, a computed total that
   balances.

6. Testing belongs INSIDE each task's acceptance criteria, not in a separate "write tests" task at
   the end. A trailing test task is a task no agent can scope.

7. No catch-all tasks. "Polish the UI", "handle edge cases", "integration" and "documentation" are
   not tasks — either fold the work into the task that owns it or leave it out.

8. Sizing rubric, applied literally:
   - S: one file or one endpoint, no new schema, no new dependency. Under ~1 hour.
   - M: a few files, or a new table plus the code that reads and writes it. Half a day.
   - L: cross-cutting, or contains genuine algorithmic or integration risk — third-party payments,
     a settlement or scheduling algorithm, realtime sync, a migration over live data. A day or more.
   The `rationale` must say which clause of the rubric applies. If a task is L only because it is
   vague, split it instead.

9. Every task lists at least one `requirement_ids` entry. If a task maps to no requirement in the
   PRD, it does not belong in the plan. If a requirement is covered by no task, you have missed it.

10. Aim for 8-25 tasks. Fewer means the tasks are too coarse for an agent to execute; more means
    you are decomposing below the useful level.

11. Be concise. Descriptions are 2-4 sentences. This output is read by a machine and skimmed by an
    engineer; it is not a design document.

12. Where the PRD is silent on something a builder must decide (a stack, a storage engine, an auth
    mechanism), pick the conventional option, state it in one clause of the description, and move
    on. Do not emit questions, TODOs, or placeholder tasks.
```

### A2. `app/prompts/agent_prompt_guidance.txt`

Appended to the user turn. It governs the bonus deliverable — the per-task pasteable prompt.

```text
AGENT PROMPT (the `agent_prompt` field)

Write it as an instruction addressed to a fresh coding agent that has the repository open and has
read nothing else — no PRD, no other task, no memory of this conversation. If the prompt only makes
sense to someone who has read the plan, it has failed.

Each one must contain, in this order:

1. The goal in one imperative sentence.
2. The concrete artifacts to create or change — paths, routes with HTTP methods, table and column
   names, function signatures.
3. The constraints that are not negotiable: contracts it must not break, formats it must match,
   validation and error cases it must handle.
4. "Done when …" — the same acceptance criteria as the task description, phrased as a check the
   agent can run.

Do not include the implementation order, and do not tell the agent what happens in other tasks.
The application appends two lines to every prompt after it has computed the dependency graph:

    Already done (do not rebuild): <titles of this task's dependencies>
    Not your scope (handled separately): <titles of the tasks that depend on this one>

Leave that boundary to the application — you do not know the final graph when you write the prompt.

Length: 80-200 words. A prompt short enough to paste and long enough to act on.
```

### A3. `app/prompts/read_system.txt` — stage 1, the reader

> *"You are a requirements analyst reading a Product Requirements Document. Your only job is to
> UNDERSTAND the document and record what it says. You do not plan, decompose, or estimate."*

Returns one JSON object and nothing else: `project_summary`, `domain`, `actors`, `requirements[]`,
`constraints`, `out_of_scope`, `open_questions`. Its rules are all about restraint — extract, do
not invent; reuse the PRD's own `FR-n` / `NFR-n` numbering rather than inventing a scheme; split a
compound requirement into the separate things it actually asks for; and emit **no** tasks, no
dependencies, no order, no complexity. The prompt says it in one line: *"You are the eyes, not the
planner."*

Separating reading from planning is the point. A single prompt doing both quietly trades one
against the other — it starts skimming the PRD once it has enough to produce plausible tasks. This
stage has nothing to gain by skimming, because it cannot produce tasks at all.

### A4. `app/prompts/graph_system.txt` — stage 2, the planner

> *"Your input is NOT the PRD. It is a structured understanding of it … Plan from that. Do not ask
> for the document."*

Returns `{"tasks": [...]}` only. Rules 1–11 are inherited from the single-shot prompt in A1 —
including the rule that matters most, that the model states **dependencies and never order**. Two
rules are new, and both exist because this stage has something A1 never had, a structured reading
to work from:

- **12** — every entry in `open_questions` must be resolved by picking the conventional option and
  proceeding. An unanswered question in a plan handed to an autonomous agent is a stall.
- **13** — `out_of_scope` is absolute. A planner that helpfully adds the thing the PRD explicitly
  excluded is worse than one that misses a requirement, because nobody is looking for it.

Rule 8 no longer carries the sizing bands inline; it delegates to the shared rubric in A6.

### A5. `app/prompts/verify_system.txt` — stage 3, the auditor

> *"You are the last check before the plan is handed to AI coding agents."*

Given the understanding from stage 1 plus the plan as the application finally assembled it, this
returns `{verdict, coverage_note, findings[]}` over exactly four checks, in order:
`requirement-uncovered`, `dependency-missing`, `complexity-suspect`, `acceptance-criteria-missing`.
Every finding must cite an id, there are at most ten, and the `verdict` derives mechanically from
the severities rather than from the model's overall impression.

Two constraints are load-bearing:

- **The computed `order` and `wave` are declared off-limits** — *"correct by construction: it is not
  a model output, and it is not yours to question. Audit the content, not the sequence."* The
  ordering comes from a topological sort. Letting a model second-guess it would give back exactly
  the guarantee the design exists to provide.
- **`dependency-missing` carries an explicit carve-out** against flagging redundant transitive
  edges. Without it the auditor reliably "finds" that A should depend on C when A → B → C already
  says so, and buries the real findings under noise.

The auditor **reports and never rewrites** — see `ASSUMPTIONS.md` #22 for why.

### A6. `app/prompts/sizing_rubric.txt` — a shared fragment, not a prompt

The literal S/M/L bands (S: one file or endpoint, under an hour · M: a few files or a new table
with the code around it, half a day · L: cross-cutting, or containing genuine algorithmic or
integration risk, a day or more), plus the requirement that each `rationale` names the clause it
is invoking.

It is a separate file because it is composed into **both** the planner and the auditor. Its own
header says so: *"applied literally — this is the same rubric the plan is later audited against"*.
Duplicating the bands into two prompts would work right up until someone edited one of them, at
which point the auditor would start disagreeing with the planner for no reason a reader could see.

### How the prompts are composed

`app/prompts/__init__.py` holds one table and derives everything else from it:

```
read   = read_system.txt
graph  = graph_system.txt  + sizing_rubric.txt + agent_prompt_guidance.txt
verify = verify_system.txt + sizing_rubric.txt

stage version = "graph-v1.<sha256(composed bytes)[:8]>"      e.g. read-v1.9f3c1a02
```

The version is **derived from the composed bytes**, not declared. Editing any fragment changes the
hash of every stage that includes it, which invalidates exactly those cache entries and no others —
so the sizing rubric changing correctly invalidates both the planner and the auditor. The `-v1`
prefix survives only because a pure hash is unreadable in a log line. This replaces the hand-bumped
`PROMPT_VERSION` constant; the trade-off is recorded in `ASSUMPTIONS.md` #15.

### A7. The two lines the *application* writes, not the model

`PlanningService._scope_agent_prompts` appends this to every `agent_prompt` after the topological
sort has run:

```text
Already done (do not rebuild): <titles of this task's dependencies>
Not your scope (handled separately): <titles of the tasks that depend on this one>
```

This is deliberately not the model's job. When the model writes a task's prompt it does not know
the final graph — which tasks ended up upstream of it, or which ended up downstream. The backend
does know, after ordering. These two lines are the difference between a prompt that reads well and
a prompt that stops a fresh agent from rebuilding its own prerequisite.

---

## Section B — the development prompt trail

Built with Claude Code. The prompts below are mine, paraphrased only where they referenced local
paths. The corrections are the interesting part: they are where the first answer was wrong.

**B1 — scoping.**
> "Read the assignment brief. Plan the build. I have under two hours."

The first plan that came back was a fourteen-step Java/Spring Boot build with Docker, a Maven
wrapper, and an evaluation harness that scored plan quality across scoring rounds. It was a good
plan for a day of work. The correction was to **cut rather than trim**: drop the eval harness,
drop containers, drop the framework I would spend twenty minutes configuring, and keep the two
things that actually get graded — a working application and a real, committed sample output.

**B2 — the design constraint that shaped everything.**
> "The brief asks for both an implementation order and dependencies. What happens when the model's
> order contradicts its own dependency list?"

This is the question the whole architecture answers. If the model emits both fields independently,
nothing stops task #3 from depending on task #7, and a reviewer checking a single row finds it.
The resolution: **the model is never asked for an order.** `TaskDraft` has no `order` field, the
system prompt forbids stating one, and `domain/ordering.py` computes it from the dependency graph.
The output is correct by construction rather than correct by luck.

**B3 — the ordering module.**
> "Write TaskOrderer: Kahn's algorithm, deterministic tie-break, wave grouping. Model sloppiness —
> dangling ids, self-dependencies, duplicate ids, cycles — must degrade into a warning, never an
> exception."

The "never an exception" clause is load-bearing. A model that emits one bad dependency edge should
not cost the user a three-minute planning call; a plan with a noted flaw is worth more than a 500.
Then:
> "Now write the tests first-principles: which properties must hold for any input?"

That produced the invariant the whole design exists to guarantee, asserted in
`tests/test_ordering.py` — *every task's order exceeds the order of each of its dependencies* —
plus determinism across runs and the cycle-recovery case.

**B4 — the two providers.**
> "Development runs on my Claude subscription through the CLI; production runs on the API. Design
> for both without the response contract depending on which is live."

One `Planner` ABC, two live implementations, one shared parser. The divergence is documented rather
than hidden: the API adapter sends the PDF natively as a base64 document block, the CLI adapter
cannot attach a binary and so sends pypdf-extracted text. That difference is real enough to change
the output, which is why the provider is part of the cache key.

**B5 — the cache, and the correction that made it simple.**
> "The same PRD must not be paid for twice. Same input, same output, no second model call. In
> memory for now."

My first sketch was a cache sitting in front of a separate store — two maps, two lifetimes, and an
invalidation problem. The correction was to notice that **if the plan's id is derived from its
input, the store and the cache are the same object**. `compute_plan_id` hashes the normalized PRD
text with the prompt version, the model, and the provider kind; `InMemoryPlanStore` is a bounded
LRU map keyed by exactly that. A second upload of the same PDF does not return an equivalent plan —
it returns *the same object*. There is no invalidation logic because there is nothing to invalidate:
change the prompt and the key changes with it.

**B6 — a bug the first version of the cache had.**
> "Does browsing plan history through `/api/plans/{id}` count as a cache hit?"

It did, which would have made the reported hit rate a lie. Split into `get` (counts) and `peek`
(does not); the history routes use `peek`.

**B7 — the CLI adapter timing out.**

The first end-to-end run returned 502 after 180 seconds. Two causes, both worth recording: the CLI
defaults to *agentic* behaviour — it loads MCP servers and may decide to go read files before
answering — and a sixteen-task plan is genuinely ~20,000 output tokens, which takes around three
minutes to generate. Fixed by pinning the CLI to a plain completion
(`--strict-mcp-config` plus `--disallowedTools`) and raising the default timeout to 600s. The
timeout was a real bug; the generation time is simply what the work costs, and the UI now says so
while it waits.

**B8 — documentation.**
> "Write the README for a reviewer who has fifteen minutes: quickstart first, then the one design
> decision that matters, then the honest limitations."

**B9 — a bug found by someone else uploading their own file.**
> "I uploaded my own one-line PRD and got: *No text could be extracted from this PDF, and the CLI
> provider cannot read a scanned document.*"

The file was not scanned. `pypdf` extracted `'Task:\nImplement User Registration API'` — 37
characters — and the text-layer check was a flat `>= 40`, so a real document failed by three
characters and the error blamed the wrong cause. Two things were wrong and both were worth fixing:

- **The threshold.** A flat minimum cannot separate a short document from a scan. Replaced with a
  density, `MIN_TEXT_CHARS_PER_PAGE = 25` — 37 characters on one page passes, the same 37 spread
  over ten pages does not.
- **The message.** It asserted that no text was found while holding 37 characters of it. It now
  reports what it measured: *"Only 37 characters of text could be extracted from this 1-page PDF."*
  A diagnostic that states the wrong cause confidently costs more than no diagnostic.

`tests/test_pdf_validator.py` now pins both sides of that boundary. The fixed build plans the same
file into 8 tasks across 3 waves with zero warnings.

**B10 — a three-minute request that looks like a hang.**
> "A cache miss takes two to four minutes and reports nothing while it runs. Make the run visible
> in the terminal and in the browser, without making the service know about either."

The correction was to what I asked for, not to the answer. My first framing was "log the stages",
which would have put a logger inside `PlanningService` and tied domain logic to a sink. What
shipped instead is an injected `on_stage(stage, detail)` callback defaulting to a no-op, fanned out
by the route to two listeners — the logger, and a bounded in-memory tracker the browser polls at
`/api/progress/{trace}`. The service still does not know whether anybody is listening, and every
existing caller and test was unaffected by the change.

Server-sent events would be the streaming answer. They were rejected because they meant making a
synchronous service async for no difference a reviewer could observe. The cost of the polling
choice is stated rather than hidden: progress is up to one second stale and the tracker is
per-process (`ASSUMPTIONS.md` #20).

**B11 — splitting the prompt.**
> "One prompt is reading the PRD and planning from it in the same breath. Split reading from
> planning, and add an audit pass — but the audit must not be able to touch the execution order."

The last clause is the whole design. An auditor that can rewrite the plan can silently break the
one guarantee the application makes by construction, so `verify_system.txt` is handed the computed
`order` and `wave` and told explicitly that they are not its to question. It reports findings; the
application decides what to do with them. Section C records why this does not contradict the
earlier decision *not* to ask the model to self-verify its dependency graph.

---

## Section C — how the product prompt evolved

| Change | Why |
|---|---|
| Removed the `order` field from the requested JSON schema entirely | Version 1 asked for an order *and* dependencies, and got plans that contradicted themselves. Deleting the field is stronger than instructing against it — the model cannot state what it has nowhere to put. |
| Added "dependencies are DIRECT prerequisites only" with a worked A→B→C example | The model transitively expanded dependencies, so every late task listed nearly every earlier one. The wave grouping collapsed to one task per wave and the parallelism signal was lost. |
| Made the S/M/L rubric literal, and required `rationale` to cite a clause | Unanchored size estimates drifted toward M for everything. Forcing a one-sentence justification against a named clause made the bands mean something. |
| "Testing belongs inside each task's acceptance criteria" | Every early plan ended with a "write tests" task — the one task an agent cannot scope, because its scope is all the other tasks. |
| "No catch-all tasks", naming the specific offenders | "Polish the UI" and "handle edge cases" appeared in nearly every run. Naming them is more effective than a general instruction to be specific. |
| Every task must cite ≥1 `requirement_ids` | Makes coverage checkable. The service now warns when a requirement is implemented by no task — a gap a reader would otherwise have to find by hand. |
| Deliberately **not** added: a self-verification instruction | Asking the model to check its own dependency graph would trade tokens and latency for a guarantee the topological sort already provides deterministically. |
| Split one prompt into three — read (A3), plan (A4), audit (A5) | The single prompt was doing three jobs against each other: it began skimming the PRD as soon as it had enough to emit plausible tasks. Giving the reader no ability to produce tasks removes the incentive. The planner then works from a structured reading rather than re-deriving one. |
| Lifted the S/M/L bands into a shared fragment (A6) composed into both the planner and the auditor | Two copies of a rubric stay in sync exactly until one is edited. Sharing the file makes "the auditor applies the planner's rubric" true by construction rather than by discipline. |
| The audit stage is **content-only**, and is still barred from touching the order | This is not a reversal of the row above. The auditor checks requirement coverage, missing dependencies, suspect sizing and absent acceptance criteria — things no deterministic check can catch. The ordering remains the topological sort's guarantee, and the prompt says so in as many words. |
