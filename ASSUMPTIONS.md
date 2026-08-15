# Assumptions

Everywhere the brief was silent I made a call rather than building for every possibility. Each one
below is the assumption, the decision it produced, and what would change if the assumption is wrong.

---

## About the problem

**1. The consumer of the output is an AI coding agent, not a human project manager.**
The brief says the plan should be "executable by AI coding agents", so tasks are written to be
actionable without a human interpreting them: concrete file paths, endpoint routes with methods,
table and column names, and acceptance criteria phrased as a check the agent can run. No story
points, no effort-in-days per person, no assignee or calendar. *If wrong:* the plan is still
readable by a human, but it is denser and more literal than a PM would write.

**2. "Suggested implementation order" means a dependency-respecting sequence, not a schedule.**
The order is a topological ordering of the dependency graph — position 1..N. It is not a set of
dates and does not model who is free or how many agents are available. Waves are included on top,
so a reader can see what is genuinely parallelisable.

**3. Order and dependencies must not be independent model outputs.**
This is the strongest assumption in the project, and the architecture follows from it. If a model
emits both, they can disagree, and that disagreement is exactly what a reviewer spot-checks. So the
model is only ever asked for dependencies and the application computes order from them. Explained
in full in the README.

**4. A model will occasionally emit an invalid graph.**
Dangling ids, self-dependencies, duplicate ids and true cycles are treated as *expected* input, not
exceptional. Each is repaired and reported as a `warning` rather than raised. Rationale: a
three-minute generation that returns a plan with one noted flaw is far more useful than one that
returns a 500.

**5. Complexity means implementation effort for an AI agent, on a literal rubric.**
S = one file or endpoint, no new schema, under ~1 hour. M = a few files, or a new table plus its
access code, half a day. L = cross-cutting or genuinely risky (payments, a settlement algorithm,
realtime sync, a live-data migration), a day or more. Every rating carries a one-sentence rationale
naming the clause that applies — without that, ratings drift to M for everything.

**6. 8–25 tasks is the useful decomposition band for a typical PRD.**
Fewer means tasks too coarse for an agent to execute in one pass; more means decomposing below the
level where an agent needs the guidance. The sample PRD produced 17. This is a soft target in the
prompt, not enforced — a warning is attached above 40.

---

## About the input

**7. PRDs are text-bearing PDFs.**
Validation is by magic bytes (`%PDF-`), not the file extension. Scanned image-only PDFs are
supported on the API provider, which reads them natively; the CLI provider rejects them with a 422
naming the alternative rather than silently planning from an empty string.

**7a. "Has a text layer" is a density, not a character count.**
`MIN_TEXT_CHARS_PER_PAGE = 25`, so the test is chars ÷ pages, not a flat minimum. A flat minimum
cannot tell a genuinely short document from a scan: a one-page brief of 37 characters and a ten-page
scan whose only extractable characters are page numbers look identical to it, and it rejects both.
The threshold is a heuristic either way, so the rejection message reports the numbers it actually
measured — "Only 37 characters … from this 1-page PDF" — instead of asserting that no text was
found. *If wrong:* a very sparse but genuine document (a mostly-diagram PRD) is refused on the CLI
provider, and the message says exactly why, with the API provider as the stated alternative.

**8. Limits: 32 MB and 100 pages.**
Both are rejections, never truncations. Planning from a silently truncated PRD produces a plan that
misses requirements while looking complete, which is the worst available failure.

**9. Requirement ids may not exist in the source.**
Many PRDs number their requirements; many do not. The model extracts them where present and
synthesises `FR-n` / `NFR-n` otherwise. Because ids are then used to check coverage, the service
warns both when a requirement is implemented by no task and when a task cites an id that is not in
the extracted requirement list.

---

## About the technology

**10. Python + FastAPI, structured like a Java service.**
The stack was free choice. Python for the model calls; the layout is deliberately Java-shaped —
`domain / ports / adapters / services` packages, an ABC as the port with three implementations, an
in-memory repository behind an interface, and the topology logic as its own unit-tested module with
no I/O and no framework imports. The point is that the model call is one swappable implementation
of one interface rather than something threaded through the app.

**11. Two providers, because dev and production have different billing.**
Development runs on a Claude subscription through the `claude` CLI (no API key); production runs on
the Anthropic API. Both are implementations of the same `Planner` port sending the same system
prompt through the same parser, so the response contract cannot change with the provider. The
divergence is that the API adapter attaches the PDF natively while the CLI adapter sends
pypdf-extracted text — which is why `provider_kind` is in the cache key.

**12. The CLI is used as a plain completion endpoint, not an agent.**
It is invoked with `--strict-mcp-config` and a `--disallowedTools` list so it cannot load MCP
servers or go read files before answering. Without this it behaves agentically and its output is not
comparable to the API adapter's.

**13. In-memory storage is sufficient for the demonstration.**
The requirement was explicitly "for now, in memory". `PlanStore` is an ABC so Redis or SQLite is a
new class and a one-line factory change. Consequences are stated in the README rather than hidden:
the cache dies with the process, and each uvicorn worker holds its own map — run single-worker.

**14. LRU eviction at 100 plans.**
An unbounded dict in a service that accepts uploads is a memory leak. 100 is arbitrary but
defensible for a single-user tool, and configurable via `PLAN_CACHE_SIZE`.

**15. The prompt version is derived, not declared.**
Each stage's version is `sha256` of that stage's *composed* prompt bytes, computed in
`app/prompts/__init__.py` and carried into the cache key. This replaces the earlier hand-bumped
`PROMPT_VERSION` constant, which was chosen so that whitespace edits would not needlessly
invalidate every cached plan — a saving that turned out not to be worth the failure mode, because
forgetting to bump it serves plans generated by a prompt that no longer exists, silently and
indefinitely. The trade is now the other way round: a whitespace edit does invalidate the stages it
touches, and only those. Since the rubric fragment is composed into both the planner and the
auditor, editing it correctly invalidates both. The human-readable `-v1` prefix is kept because a
bare hash is unreadable in a log line.

---

## About the scope

**16. No authentication, persistence, rate limiting, or multi-user support.**
A single-user local tool. All are orthogonal to what the assignment asks to demonstrate.

**17. No containerisation.**
`pip install -r requirements.txt` and `uvicorn` is a shorter path for a reviewer than a container
build, and there is no cross-platform toolchain problem to solve here.

**18. No automated plan-quality evaluation.**
A rubric-scored eval harness across multiple PRDs was scoped and deliberately cut for time. Quality
was instead tuned by reading real outputs against the sample PRD; the resulting prompt changes are
recorded in `AI_PROMPTS.md` section C. This is the most substantial thing not built, and it is
listed as a limitation rather than glossed over.

**19. Tests cover the ordering module and the validator, not the whole app.**
`tests/test_ordering.py` (11 tests) covers what is load-bearing and non-obvious: the
dependency-respecting invariant across graph shapes, determinism, wave assignment, and recovery from
each malformed-graph case. `tests/test_pdf_validator.py` (7 tests) was added after the flat-minimum
bug in assumption 7a and pins the boundary it got wrong. The HTTP layer, renderer, and parser were
verified by running them end to end and are documented in the README's verification commands. Given
more time, the parser's fallback paths are the next worth unit tests.

**20. Progress is polled, not streamed.**
A cache miss takes 2–4 minutes, and a request that reports nothing for that long is indistinguishable
from a hang — so the service emits stage events through an injected callback, fanned out to a logger
(terminal) and a bounded in-memory tracker the browser polls once a second at `/api/progress/{trace}`.
Server-sent events would be the streaming answer, but they would have meant making a synchronous
service async for no difference a reviewer could see. The callback defaults to a no-op, so
`PlanningService` stays framework-free and does not know whether anybody is listening. The trade-off
is real: progress is up to one second stale, and the tracker is per-process, so it would need moving
to shared state before this ran behind more than one worker.

**21. The sample PRD is synthetic and deliberately unrelated to any real product.**
"SplitTab", a group expense-splitting app, generated by a committed script so the test input is
reproducible rather than an opaque binary. It was chosen because it has genuine dependency depth
(auth → groups → expenses → settlement → export) plus one algorithmically hard task, so the output
exercises the ordering logic instead of producing a flat list.

**22. The plan is audited, but the auditor is advisory only.**
The verify stage returns findings — uncovered requirements, missing dependencies, suspect sizing,
absent acceptance criteria — and never a revised plan. A model that can rewrite the graph can
silently break the topological guarantee that is the whole reason the ordering is trustworthy, and
it would do so in a way no reader could detect after the fact. A surfaced warning a human can
overrule is worth more than an invisible correction. If this is wrong, the cost is that a reviewer
reads findings the application could have fixed; the alternative risks a plan that is quietly
wrong, which is strictly worse.

**23. Stage artifacts are tracked in git on purpose.**
The intermediate stage outputs are committed rather than ignored, so a fresh clone ships a complete
worked example and the caching and reuse claims can be inspected without spending a token or
holding an API key. The cost is repository noise and a diff that changes whenever a prompt changes.
If this project were a deployed service rather than a reviewable exercise, they would be ignored.
