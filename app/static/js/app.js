// Crowe Vibe Whisper front end. Plain JS, no build step.
// All text from the server is inserted with textContent, never innerHTML.
"use strict";

const HEADER = { "X-Requested-With": "CroweVibeWhisper" };
const $ = (id) => document.getElementById(id);

const state = {
  server: null,     // last /api/state
  jobs: new Map(),  // id -> job summary
  selected: null,   // selected job id
  segments: [],     // segments of the selected job
};

async function api(path, { method = "GET", body, json } = {}) {
  const opts = { method, headers: { ...HEADER }, credentials: "same-origin" };
  if (json !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(json);
  } else if (body !== undefined) {
    opts.body = body;
  }
  const resp = await fetch(path, opts);
  let data = {};
  try { data = await resp.json(); } catch { /* empty body */ }
  if (!resp.ok && !data.jobs) throw new Error(data.error || `Request failed (${resp.status})`);
  return data;
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

function fmtTime(sec) {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const mm = String(m).padStart(2, "0"), ss = String(r).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function fmtBytes(n) {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  return `${Math.round(n / 1e6)} MB`;
}

function showNotice(text) {
  const n = $("notice");
  n.textContent = text;
  n.hidden = !text;
}

// ---- state + rendering --------------------------------------------------------

async function loadState() {
  const s = await api("/api/state");
  state.server = s;
  state.jobs = new Map(s.jobs.map((j) => [j.id, j]));
  applyTheme(s.settings.theme);
  renderEngine();
  renderOptions();
  renderDownload(s.download);
  renderQueue();
  if (state.selected && state.jobs.has(state.selected)) selectJob(state.selected);
}

function currentBackend() {
  return state.server?.choice?.backend || null;
}

function renderEngine() {
  const c = state.server.choice;
  $("engine-badge").textContent = c ? `${c.backend} · ${c.device.kind.toUpperCase()} · ${c.device.name}` : "No engine installed";
}

function renderOptions() {
  const s = state.server;
  const backend = currentBackend();
  const modelSel = $("model-select");
  modelSel.replaceChildren();
  for (const m of s.models) {
    const v = backend && m.variants[backend];
    let label = m.label;
    if (m.name === s.recommended_model) label += " (recommended)";
    if (v) label += v.installed ? " · installed" : ` · ${fmtBytes(v.size)}`;
    modelSel.append(el("option", { value: m.name, text: label }));
  }
  modelSel.value = s.effective_model;

  const langSel = $("language-select");
  if (!langSel.options.length) {
    langSel.append(el("option", { value: "auto", text: "Detect automatically" }));
    const entries = Object.entries(s.languages).sort((a, b) => a[1].localeCompare(b[1]));
    for (const [code, name] of entries) langSel.append(el("option", { value: code, text: name }));
  }
  langSel.value = s.settings.language;
  $("translate-check").checked = s.settings.translate;
  renderModelStatus();
}

function selectedModelInfo() {
  const name = $("model-select").value;
  const backend = currentBackend();
  const m = state.server.models.find((x) => x.name === name);
  return { name, backend, variant: m && backend ? m.variants[backend] : null };
}

function renderModelStatus() {
  const { name, backend, variant } = selectedModelInfo();
  const status = $("model-status");
  const row = $("download-row");
  status.className = "hint";
  if (!backend) {
    status.textContent = "No transcription engine is installed. Run scripts\\install.ps1.";
    status.className = "hint error";
    row.hidden = true;
  } else if (!variant) {
    status.textContent = `Model "${name}" isn't available for ${backend}.`;
    row.hidden = true;
  } else if (variant.installed) {
    status.textContent = "Installed and verified.";
    status.className = "hint ok";
    row.hidden = !isDownloading();
  } else {
    status.textContent = `Not installed. Download is ${fmtBytes(variant.size)} from Hugging Face (SHA-256 verified).`;
    row.hidden = false;
  }
}

function isDownloading() {
  const d = state.server?.download;
  return d && (d.status === "downloading" || d.status === "verifying");
}

function renderDownload(d) {
  if (state.server) state.server.download = d;
  const busy = d && d.status === "downloading";
  $("download-btn").hidden = !!busy;
  $("download-cancel").hidden = !busy;
  $("download-progress").hidden = !busy;
  const text = $("download-text");
  text.className = "hint";
  if (!d) { text.textContent = ""; return; }
  if (busy) {
    $("download-progress").value = d.total ? d.done / d.total : 0;
    text.textContent = `${d.model}: ${fmtBytes(d.done)} of ${fmtBytes(d.total)}`;
  } else if (d.status === "error") {
    text.textContent = d.error || "Download failed";
    text.className = "hint error";
  } else if (d.status === "cancelled") {
    text.textContent = "Download cancelled.";
  } else {
    text.textContent = "";
  }
}

const STATUS_TEXT = {
  queued: "Queued", converting: "Converting audio…", transcribing: "Transcribing…",
  done: "Done", error: "Error", cancelled: "Cancelled",
};

function renderQueue() {
  const list = $("queue");
  list.replaceChildren();
  const jobs = [...state.jobs.values()].sort((a, b) => a.created - b.created);
  $("queue-empty").hidden = jobs.length > 0;
  for (const j of jobs) {
    const active = j.status === "queued" || j.status === "converting" || j.status === "transcribing";
    const statusText = j.status === "error" ? `Error: ${j.error}` :
      j.status === "transcribing" ? `Transcribing… ${Math.round(j.progress * 100)}%` : STATUS_TEXT[j.status];
    const actions = el("span", { class: "q-actions" },
      active
        ? el("button", { class: "btn ghost small", type: "button", text: "Cancel", "aria-label": `Cancel ${j.name}`,
            onclick: (e) => { e.stopPropagation(); api(`/api/jobs/${j.id}/cancel`, { method: "POST" }); } })
        : el("button", { class: "btn ghost small", type: "button", text: "Remove", "aria-label": `Remove ${j.name} from queue`,
            onclick: (e) => { e.stopPropagation(); api(`/api/jobs/${j.id}`, { method: "DELETE" }); } }));
    const li = el("li", { tabindex: "0", "data-id": j.id, class: j.id === state.selected ? "selected" : "",
        onclick: () => selectJob(j.id),
        onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectJob(j.id); } } },
      el("span", { class: "q-name", text: j.name, title: j.name }),
      el("span", { class: `q-status ${j.status}`, text: statusText }),
      actions);
    if (j.status === "transcribing") li.append(el("progress", { max: "1", value: String(j.progress) }));
    list.append(li);
  }
}

async function selectJob(id) {
  state.selected = id;
  renderQueue();
  const job = await api(`/api/jobs/${id}`);
  if (state.selected !== id) return;
  state.segments = job.segments;
  renderTranscript(job);
}

function renderTranscript(job) {
  const meta = $("transcript-meta");
  const parts = [job.name];
  if (job.engine) parts.push(`${job.engine.backend} on ${job.engine.device.kind.toUpperCase()}`);
  if (job.elapsed) parts.push(`${job.elapsed.toFixed(1)} s`);
  meta.textContent = parts.join(" · ");
  const prog = $("job-progress");
  prog.hidden = job.status !== "transcribing";
  prog.value = job.progress;
  if (job.notice) showNotice(job.notice);

  const box = $("transcript");
  box.replaceChildren();
  if (!state.segments.length) {
    const msg = job.status === "done" ? "No speech was found." :
      job.status === "error" ? job.error : job.status === "cancelled" ? "Cancelled." : "Waiting for text…";
    box.append(el("p", { class: "hint", text: msg }));
    return;
  }
  for (const seg of state.segments) box.append(segmentNode(seg));
  applySearch();
}

function segmentNode(seg) {
  const text = el("span", { class: "tx", text: seg.text });
  return el("div", { class: "seg" }, el("span", { class: "ts", text: fmtTime(seg.start) }), text);
}

function applySearch() {
  const q = $("search").value.trim().toLowerCase();
  for (const node of $("transcript").querySelectorAll(".seg")) {
    const tx = node.querySelector(".tx");
    const raw = tx.textContent;
    tx.replaceChildren(document.createTextNode(raw));
    if (!q) { node.classList.remove("hidden"); continue; }
    const idx = raw.toLowerCase().indexOf(q);
    node.classList.toggle("hidden", idx < 0);
    if (idx >= 0) {
      tx.replaceChildren(raw.slice(0, idx), el("mark", { text: raw.slice(idx, idx + q.length) }), raw.slice(idx + q.length));
    }
  }
}

function transcriptText() {
  const withTs = $("ts-toggle").checked;
  return state.segments.map((s) => (withTs ? `[${fmtTime(s.start)}] ${s.text}` : s.text)).join("\n");
}

// ---- theme ----------------------------------------------------------------------

const THEMES = ["system", "light", "dark"];

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("theme-toggle").textContent = `Theme: ${theme}`;
}

// ---- events ---------------------------------------------------------------------

function connectEvents() {
  const es = new EventSource("/api/events");
  es.addEventListener("sync", () => loadState().catch(console.error));
  es.addEventListener("job", (e) => {
    const j = JSON.parse(e.data);
    state.jobs.set(j.id, j);
    renderQueue();
    if (j.id === state.selected && (j.status === "done" || j.status === "error" || j.status === "cancelled")) {
      selectJob(j.id);
    } else if (j.id === state.selected) {
      $("job-progress").hidden = j.status !== "transcribing";
    }
    if (j.notice) showNotice(j.notice);
  });
  es.addEventListener("job_removed", (e) => {
    const { id } = JSON.parse(e.data);
    state.jobs.delete(id);
    if (state.selected === id) {
      state.selected = null;
      state.segments = [];
      $("transcript").replaceChildren(el("p", { class: "hint", text: "Pick a file in the queue to see its transcript." }));
      $("transcript-meta").textContent = "";
    }
    renderQueue();
  });
  es.addEventListener("segment", (e) => {
    const { id, progress, segment } = JSON.parse(e.data);
    const j = state.jobs.get(id);
    if (j) { j.progress = progress; j.segment_count += 1; }
    if (id === state.selected) {
      const box = $("transcript");
      if (!state.segments.length) box.replaceChildren();
      state.segments.push(segment);
      const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
      box.append(segmentNode(segment));
      if ($("search").value) applySearch();
      if (atBottom) box.scrollTop = box.scrollHeight;
      $("job-progress").value = progress;
    }
    const li = document.querySelector(`#queue li[data-id="${id}"]`);
    if (li && j) {
      li.querySelector(".q-status").textContent = `Transcribing… ${Math.round(progress * 100)}%`;
      const p = li.querySelector("progress");
      if (p) p.value = progress;
    }
  });
  es.addEventListener("job_reset", (e) => {
    const { id } = JSON.parse(e.data);
    if (id === state.selected) { state.segments = []; $("transcript").replaceChildren(); }
  });
  es.addEventListener("download", (e) => {
    const d = JSON.parse(e.data);
    renderDownload(d);
    if (d.status === "done") loadState().catch(console.error);
  });
}

// ---- uploads --------------------------------------------------------------------

function upload(files) {
  if (!files.length) return;
  const { variant } = selectedModelInfo();
  const status = $("upload-status");
  if (variant && !variant.installed) {
    status.textContent = "Download the selected model first.";
    status.className = "hint error";
    return;
  }
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  form.append("model", $("model-select").value);
  form.append("language", $("language-select").value);
  form.append("translate", $("translate-check").checked ? "true" : "false");

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/jobs");
  xhr.setRequestHeader("X-Requested-With", HEADER["X-Requested-With"]);
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) status.textContent = `Uploading… ${Math.round((e.loaded / e.total) * 100)}%`;
  };
  xhr.onload = () => {
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
    const rejected = data.rejected || [];
    if (xhr.status === 413) {
      status.textContent = "File is larger than the upload limit.";
    } else if (rejected.length) {
      status.textContent = rejected.map((r) => `${r.name}: ${r.error}`).join("; ");
    } else if (xhr.status >= 400) {
      status.textContent = data.error || "Upload failed.";
    } else {
      status.textContent = "";
    }
    status.className = status.textContent ? "hint error" : "hint";
    for (const j of data.jobs || []) state.jobs.set(j.id, j);
    if (!state.selected && data.jobs && data.jobs.length) selectJob(data.jobs[0].id);
    renderQueue();
  };
  xhr.onerror = () => { status.textContent = "Upload failed."; status.className = "hint error"; };
  status.className = "hint";
  status.textContent = "Uploading…";
  xhr.send(form);
}

// ---- wiring ---------------------------------------------------------------------

function wire() {
  const dz = $("dropzone");
  $("file-input").addEventListener("change", (e) => { upload([...e.target.files]); e.target.value = ""; });
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("over"));
  dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("over"); upload([...e.dataTransfer.files]); });
  // Dropping a file anywhere else must not navigate the window to it.
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());

  $("model-select").addEventListener("change", () => {
    api("/api/settings", { method: "POST", json: { model: $("model-select").value } }).catch(console.error);
    renderModelStatus();
  });
  $("language-select").addEventListener("change", () =>
    api("/api/settings", { method: "POST", json: { language: $("language-select").value } }).catch(console.error));
  $("translate-check").addEventListener("change", () =>
    api("/api/settings", { method: "POST", json: { translate: $("translate-check").checked } }).catch(console.error));

  $("download-btn").addEventListener("click", async () => {
    const { name, backend } = selectedModelInfo();
    try {
      const { download } = await api("/api/models/download", { method: "POST", json: { backend, model: name } });
      renderDownload(download);
    } catch (err) {
      renderDownload({ status: "error", error: err.message });
    }
  });
  $("download-cancel").addEventListener("click", () => api("/api/models/download/cancel", { method: "POST" }));

  $("search").addEventListener("input", applySearch);
  $("ts-toggle").addEventListener("change", () => $("transcript").classList.toggle("no-ts", !$("ts-toggle").checked));
  $("copy-btn").addEventListener("click", async () => {
    await navigator.clipboard.writeText(transcriptText());
    $("copy-btn").textContent = "Copied";
    setTimeout(() => { $("copy-btn").textContent = "Copy"; }, 1200);
  });
  $("theme-toggle").addEventListener("click", () => {
    const next = THEMES[(THEMES.indexOf(document.documentElement.dataset.theme) + 1) % THEMES.length];
    applyTheme(next);
    api("/api/settings", { method: "POST", json: { theme: next } }).catch(console.error);
  });
  $("notice").addEventListener("click", () => showNotice(""));

  // Lets the Edge app-mode fallback notice when the window is closed.
  setInterval(() => api("/api/heartbeat", { method: "POST" }).catch(() => {}), 5000);
}

document.addEventListener("DOMContentLoaded", () => {
  wire();
  loadState().catch((e) => showNotice(e.message));
  connectEvents();
});
