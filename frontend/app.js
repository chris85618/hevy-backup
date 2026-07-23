"use strict";

const api = async (path, options = {}) => {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return resp.json();
};

// --- tabs ------------------------------------------------------------------
document.querySelectorAll("nav button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("nav button, .tab").forEach((el) => el.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "dashboard") loadDashboard();
    if (btn.dataset.tab === "data") loadData();
    if (btn.dataset.tab === "settings") loadSettings();
  });
});

// --- dashboard -------------------------------------------------------------
async function loadDashboard() {
  try {
    const s = await api("/api/status");
    const docs = s.documents || {};
    document.getElementById("status-cards").innerHTML = `
      <div class="card"><b>${docs.session || 0}</b><span>sessions</span></div>
      <div class="card"><b>${docs.plan || 0}</b><span>plans</span></div>
      <div class="card"><b>${docs.exercise || 0}</b><span>exercises</span></div>
      <div class="card"><b>${docs["body-metric"] || 0}</b><span>body metrics</span></div>
      <div class="card"><b>${s.configured.hevy ? "✓" : "✗"}</b><span>Hevy configured</span></div>
      <div class="card"><b>${s.configured.wger ? "✓" : "✗"}</b><span>wger configured</span></div>
      <div class="card"><b>${s.last_sync ? s.last_sync.replace("T", " ").slice(0, 16) : "never"}</b><span>last sync</span></div>
      <div class="card"><b>FitIR ${s.fitir}</b><span>IR version</span></div>`;
    document.querySelector("#runs-table tbody").innerHTML = (s.recent_runs || [])
      .map((r) => `<tr><td>${r.id}</td><td>${r.connector}</td><td>${r.started_at}</td>
        <td class="${r.status}">${r.status}</td><td class="detail">${r.detail || ""}</td></tr>`)
      .join("");
  } catch (e) {
    document.getElementById("status-cards").textContent = `Backend unreachable: ${e.message}`;
  }
}

document.getElementById("sync-now").addEventListener("click", async () => {
  const out = document.getElementById("sync-result");
  out.textContent = "syncing…";
  try {
    const r = await api("/api/sync/run", { method: "POST" });
    out.textContent = r.status === "success" ? `done: ${JSON.stringify(r.summary)}` : JSON.stringify(r);
  } catch (e) {
    out.textContent = `error: ${e.message}`;
  }
  loadDashboard();
});

// --- data browser ----------------------------------------------------------
let page = 1;
const columns = {
  session: ["id", "title", "started_at", "exercises"],
  plan: ["id", "name", "tags", "days"],
  exercise: ["id", "name", "primary_muscles", "equipment_category"],
  "body-metric": ["id", "metric_key", "at", "quantity"],
};

function cellValue(doc, col) {
  const v = doc[col];
  if (col === "exercises" || col === "days") return (v || []).length;
  if (col === "quantity" && v) return `${v.value} ${v.unit}`;
  if (Array.isArray(v)) return v.join(", ");
  return v ?? "";
}

async function loadData() {
  const kind = document.getElementById("kind-select").value;
  const q = document.getElementById("search").value;
  const data = await api(`/api/documents/${kind}?page=${page}&page_size=20&q=${encodeURIComponent(q)}`);
  const cols = columns[kind];
  document.querySelector("#data-table thead").innerHTML =
    `<tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr>`;
  document.querySelector("#data-table tbody").innerHTML = data.items
    .map((d) => `<tr data-kind="${kind}" data-id="${d.id}">${cols
      .map((c) => `<td>${cellValue(d, c)}</td>`).join("")}</tr>`)
    .join("");
  document.getElementById("page-info").textContent =
    `${page} / ${Math.max(1, Math.ceil(data.total / 20))} (${data.total})`;
  document.querySelectorAll("#data-table tbody tr").forEach((tr) => {
    tr.addEventListener("click", async () => {
      const doc = await api(`/api/documents/${tr.dataset.kind}/${tr.dataset.id}`);
      const box = document.getElementById("doc-detail");
      box.textContent = JSON.stringify(doc, null, 2);
      box.classList.remove("hidden");
    });
  });
}

document.getElementById("kind-select").addEventListener("change", () => { page = 1; loadData(); });
document.getElementById("search").addEventListener("input", () => { page = 1; loadData(); });
document.getElementById("prev").addEventListener("click", () => { if (page > 1) { page--; loadData(); } });
document.getElementById("next").addEventListener("click", () => { page++; loadData(); });

// --- export ----------------------------------------------------------------
async function renderPreview() {
  const p = await api("/api/export/wger/preview");
  document.getElementById("wger-preview-box").innerHTML = `
    <ul>
      <li>configured: ${p.configured}</li>
      <li>pending sessions: ${p.pending_sessions}</li>
      <li>updated sessions to re-push: ${p.updated_sessions}</li>
      <li>sessions to mark deleted: ${p.sessions_to_mark_deleted}</li>
      <li>pending plans (templates): ${p.pending_plans}</li>
      <li>pending weight entries: ${p.pending_weight_entries}</li>
      <li>pending measurements: ${p.pending_measurements}</li>
      <li>pending exercise updates: ${p.pending_exercise_updates}</li>
    </ul>`;
  document.querySelector("#unresolved-table tbody").innerHTML =
    (p.unresolved_exercises || [])
      .map((u) => `<tr><td>${u.ir_id}</td><td>${u.name}</td>
        <td><input type="number" data-ir="${u.ir_id}" placeholder="wger id"></td>
        <td><button class="map-btn" data-ir="${u.ir_id}">Save</button></td></tr>`)
      .join("") || "<tr><td colspan=4>none 🎉</td></tr>";
  document.querySelectorAll(".map-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const input = document.querySelector(`input[data-ir="${btn.dataset.ir}"]`);
      await api("/api/mappings/exercise", {
        method: "POST",
        body: JSON.stringify({ ir_id: btn.dataset.ir, wger_exercise_id: Number(input.value) }),
      });
      renderPreview();
    });
  });
}

document.getElementById("wger-preview").addEventListener("click", renderPreview);
document.getElementById("wger-push").addEventListener("click", async () => {
  const out = document.getElementById("wger-result");
  out.textContent = "pushing…";
  try {
    const r = await api("/api/export/wger", { method: "POST" });
    out.textContent = JSON.stringify(r);
  } catch (e) {
    out.textContent = `error: ${e.message}`;
  }
  renderPreview();
});

// --- settings --------------------------------------------------------------
async function loadSettings() {
  const s = await api("/api/settings");
  const form = document.getElementById("settings-form");
  for (const [k, v] of Object.entries(s)) if (form.elements[k]) form.elements[k].value = v;
}

document.getElementById("settings-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const form = ev.target;
  const body = {};
  for (const key of ["hevy_api_key", "wger_base_url", "wger_api_key", "sync_cron"]) {
    if (form.elements[key].value) body[key] = form.elements[key].value;
  }
  const result = document.getElementById("settings-result");
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(body) });
    result.textContent = "saved ✓";
    loadSettings();
  } catch (e) {
    result.textContent = `error: ${e.message}`;
  }
});

loadDashboard();
