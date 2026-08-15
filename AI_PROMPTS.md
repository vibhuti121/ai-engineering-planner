# AI prompts used

Two different things are called "prompts" in this project and they are kept apart:

- **Section A** — the prompts that ship *inside the product*. These are what the application sends
  to the model at runtime. They live as `.txt` files under `app/prompts/` and are quoted verbatim
  below.
- **Section B** — the prompts *I* used while building the application, and the corrections that
  were needed. This is the development trail, written as the work happened.
- **Section C** — what changed in the product prompt across iterations, and why.

---

## Section A — prompts that ship inside the product

### A1. `app/prompts/system_prompt.txt`

Sent as the `system` parameter by the API adapter and prepended to the prompt by the CLI adapter.
Both adapters send it byte-identically, which is what makes the two providers interchangeable.

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

### A3. The two lines the *application* writes, not the model

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
