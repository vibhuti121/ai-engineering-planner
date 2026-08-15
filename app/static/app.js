"use strict";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

let selectedFile = null;
let currentPlan = null;

// ── provider banner ───────────────────────────────────────────────────────────
fetch("/api/health")
  .then((r) => r.json())
  .then((h) => {
    const banner = $("provider");
    if (h.provider === "demo") {
      banner.className = "banner demo";
      banner.textContent =
        "Demo mode — each stage replays a recorded response from fixtures/demo/. " +
        "Set ANTHROPIC_API_KEY (or install the Claude CLI) for live planning.";
    } else {
      banner.textContent =
        `Provider: ${h.provider === "api" ? "Anthropic API" : "Claude CLI (subscription)"} · ` +
        `model ${h.model} · prompt ${h.prompt_version}`;
    }
  })
  .catch(() => ($("provider").textContent = "Could not reach the server."));

// ── file selection ────────────────────────────────────────────────────────────
const dropzone = $("dropzone");

function chooseFile(file) {
  if (!file) return;
  selectedFile = file;
  $("filename").textContent = `${file.name} · ${(file.size / 1024).toFixed(0)} KB`;
  $("submit").disabled = false;
}

$("file").addEventListener("change", (e) => chooseFile(e.target.files[0]));
["dragenter", "dragover"].forEach((type) =>
  dropzone.addEventListener(type, (e) => {
    e.preventDefault();
    dropzone.classList.add("over");
  })
);
["dragleave", "drop"].forEach((type) =>
  dropzone.addEventListener(type, (e) => {
    e.preventDefault();
    dropzone.classList.remove("over");
  })
);
dropzone.addEventListener("drop", (e) => chooseFile(e.dataTransfer.files[0]));

// ── progress ──────────────────────────────────────────────────────────────────
// The stages come from the SERVER, not a client-side animation. A progress display that guesses
// is worse than none — this one reports the step the request is actually in.
// Must match `observability.STAGE_SEQUENCE` exactly and in order — a name the server emits but this
// list lacks makes `indexOf` return -1, which blanks every step at the moment somebody is watching.
const STAGES = [
  "validating",
  "cache-lookup",
  "reading",
  "graphing",
  "ordering",
  "verifying",
  "rendering",
  "stored",
];

function paintSteps(stage, detail, elapsed) {
  const reached = STAGES.indexOf(stage);
  document.querySelectorAll("#steps .step").forEach((li, i) => {
    li.classList.toggle("done", reached > i);
    li.classList.toggle("active", reached === i);
    const label = li.dataset.label || (li.dataset.label = li.textContent);
    li.textContent =
      reached === i ? `${label} — ${detail} (${elapsed})` : label;
  });
}

function clock(seconds) {
  const s = Math.floor(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// ── submit ────────────────────────────────────────────────────────────────────
$("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!selectedFile) return;

  const status = $("status");
  status.className = "status";
  status.textContent = "Planning… a cache miss calls the model and takes a couple of minutes.";
  $("submit").disabled = true;

  const trace = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now());
  const steps = $("steps");
  steps.hidden = false;
  paintSteps("validating", "starting", "0:00");

  const poll = setInterval(async () => {
    try {
      const r = await fetch(`/api/progress/${trace}`);
      if (!r.ok) return;
      const p = await r.json();
      paintSteps(p.stage, p.detail, clock(p.elapsed_s));
    } catch (_) {
      /* a dropped poll is not an error — the upload itself is still in flight */
    }
  }, 1000);

  const body = new FormData();
  body.append("file", selectedFile);

  try {
    const params = new URLSearchParams({ trace });
    if ($("refresh").checked) params.set("refresh", "true");
    const response = await fetch(`/api/plan?${params}`, { method: "POST", body });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    currentPlan = payload;
    render(payload);
    status.textContent = "";
    steps.hidden = true;
  } catch (err) {
    status.className = "status error";
    status.textContent = err.message;
    steps.hidden = true;
  } finally {
    clearInterval(poll);
    $("submit").disabled = false;
  }
});

// The only *visible* evidence that a third agent ran, and it sits below the task list because it
// is commentary on the plan rather than a gate on it: improvements worth making, and questions the
// PRD leaves for a human. Nothing here changes a task.
function renderVerification(verification) {
  const section = $("review");
  const panel = $("verification");
  panel.replaceChildren();

  const notes = verification
    ? [...verification.improvements, ...verification.open_questions]
    : [];
  const note = verification ? (verification.coverage_note || "").trim() : "";
  // An empty Review heading reads as a bug, so the whole section stays hidden.
  section.hidden = !notes.length && !note;
  if (section.hidden) return;

  if (note) panel.append(el("p", "verify-note", note));
  appendNotes(panel, "Improvements", verification.improvements);
  appendNotes(panel, "Open questions", verification.open_questions);
}

function appendNotes(panel, heading, notes) {
  if (!notes || !notes.length) return;
  panel.append(el("h3", "review-heading", heading));
  notes.forEach((n) => {
    const cited = [...n.task_ids, ...n.requirement_ids].join(", ");
    const row = el("div", "finding");
    row.append(el("span", "finding-message", n.text));
    if (cited) row.append(el("span", "finding-cites", cited));
    panel.append(row);
  });
}

// ── render ────────────────────────────────────────────────────────────────────
function render(plan) {
  $("result").hidden = false;
  const meta = plan.meta;

  const bar = $("meta-bar");
  bar.replaceChildren();
  bar.append(
    el("span", `chip ${meta.cache}`,
      meta.cache === "hit"
        ? "Served from cache — no model call"
        : `Generated in ${(meta.duration_ms / 1000).toFixed(1)}s`),
    el("span", "chip", `plan ${plan.plan_id}`),
    el("span", "chip", `${meta.provider} · ${meta.model}`),
    el("span", "chip", `${meta.page_count} pages`),
    el("span", "chip", `${meta.task_count} tasks`),
  );
  if (meta.output_tokens) bar.append(el("span", "chip", `${meta.output_tokens} output tokens`));

  // One chip per stage. This is where partial reuse becomes visible: after a prompt edit only the
  // stages downstream of it say "miss", and the rest cost nothing.
  // The model is named only when it differs from the headline one — with a single model across all
  // three stages, repeating it three times is noise.
  (meta.stages || []).forEach((s) =>
    bar.append(el("span", `chip stage ${s.cache}`,
      `${s.name} ${s.cache}` +
      (s.cache === "miss" ? ` ${(s.duration_ms / 1000).toFixed(1)}s` : "") +
      (s.model && s.model !== meta.model ? ` · ${s.model}` : "")))
  );

  // Only the application's own warnings land here — an uncovered requirement, a dropped dependency
  // edge, a stage that failed. The reviewer's advice is not a warning and renders in `#review`.
  const warnings = $("warnings");
  warnings.replaceChildren();
  plan.warnings.forEach((w) => warnings.append(el("div", "warning", w)));

  renderVerification(plan.verification);

  $("summary").textContent = plan.project_summary;

  $("req-count").textContent = `(${plan.requirements.length})`;
  const reqBody = document.querySelector("#requirements tbody");
  reqBody.replaceChildren();
  plan.requirements.forEach((r) => {
    const row = el("tr");
    row.append(el("td", null, r.id), el("td", null, r.category), el("td", null, r.text));
    reqBody.append(row);
  });

  $("task-count").textContent = `(${plan.tasks.length})`;
  const waves = $("waves");
  waves.replaceChildren();
  plan.waves.forEach((wave, i) => {
    const box = el("div", "wave");
    box.append(el("b", null, `Wave ${i + 1} — ${wave.length} in parallel`));
    box.append(document.createTextNode(wave.join(", ")));
    waves.append(box);
  });

  const tasks = $("tasks");
  tasks.replaceChildren();
  plan.tasks.forEach((task) => tasks.append(renderTask(task)));
}

function renderTask(task) {
  const card = el("div", "task");

  const head = el("div", "task-head");
  head.append(
    el("span", "order", `#${task.order}`),
    el("span", "task-title", task.title),
    el("span", `badge ${task.complexity}`, task.complexity),
    el("span", "task-id", task.id),
  );
  card.append(head);
  card.append(el("p", null, task.description));

  const meta = el("p", "task-meta");
  meta.append(
    document.createTextNode(
      `wave ${task.wave} · depends on ${task.dependencies.join(", ") || "nothing"} · ` +
      `requirements ${task.requirement_ids.join(", ") || "—"}`
    )
  );
  card.append(meta);
  if (task.rationale) card.append(el("p", "task-meta", `Why ${task.complexity}: ${task.rationale}`));

  if (task.agent_prompt) {
    const details = el("details");
    details.append(el("summary", null, "Agent prompt — paste into a coding agent"));
    details.append(el("pre", null, task.agent_prompt));
    const copy = el("button", "ghost copy", "Copy prompt");
    copy.addEventListener("click", async () => {
      await navigator.clipboard.writeText(task.agent_prompt);
      copy.textContent = "Copied";
      setTimeout(() => (copy.textContent = "Copy prompt"), 1500);
    });
    details.append(copy);
    card.append(details);
  }
  return card;
}

// ── downloads ─────────────────────────────────────────────────────────────────
function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = el("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

$("dl-json").addEventListener("click", () => {
  if (currentPlan) download(`plan-${currentPlan.plan_id}.json`, JSON.stringify(currentPlan, null, 2), "application/json");
});
$("dl-md").addEventListener("click", () => {
  if (currentPlan) download(`plan-${currentPlan.plan_id}.md`, currentPlan.markdown, "text/markdown");
});
