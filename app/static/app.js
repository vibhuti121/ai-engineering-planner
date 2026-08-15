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
        "Demo mode — replaying a previously generated plan from fixtures/demo-plan.json. " +
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

// ── submit ────────────────────────────────────────────────────────────────────
$("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!selectedFile) return;

  const status = $("status");
  status.className = "status";
  status.textContent = "Planning… a cache miss calls the model and takes a couple of minutes.";
  $("submit").disabled = true;

  const body = new FormData();
  body.append("file", selectedFile);

  try {
    const query = $("refresh").checked ? "?refresh=true" : "";
    const response = await fetch(`/api/plan${query}`, { method: "POST", body });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    currentPlan = payload;
    render(payload);
    status.textContent = "";
  } catch (err) {
    status.className = "status error";
    status.textContent = err.message;
  } finally {
    $("submit").disabled = false;
  }
});

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

  const warnings = $("warnings");
  warnings.replaceChildren();
  plan.warnings.forEach((w) => warnings.append(el("div", "warning", w)));

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
