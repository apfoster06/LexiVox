// Files tab: upload, queue and transcript view. Model and language come from Settings.
"use strict";

const STATUS_TEXT = {
  queued: "Queued", converting: "Converting…", transcribing: "Transcribing…",
  done: "Done", error: "Error", cancelled: "Cancelled",
};

function selectedModel() {
  const s = state.server;
  if (!s) return {};
  const backend = s.choice && s.choice.backend;
  const m = s.models.find((x) => x.name === s.effective_model);
  return { name: s.effective_model, label: m ? m.label : s.effective_model, variant: m && backend ? m.variants[backend] : null };
}

function renderFilesModel(s) {
  const { label, variant } = selectedModel();
  const lang = s.settings.language === "auto" ? "auto-detect language" : s.languages[s.settings.language];
  const box = $("files-model");
  box.className = variant && !variant.installed ? "hint error" : "hint";
  box.textContent = variant && !variant.installed
    ? `Model ${label} isn't downloaded yet. Get it in Settings.`
    : `Model: ${label} · ${lang}${s.settings.translate ? " · translate to English" : ""}. Change in Settings.`;
}

function renderQueue() {
  const list = $("queue");
  list.replaceChildren();
  const jobs = [...state.jobs.values()].sort((a, b) => a.created - b.created);
  $("queue-empty").hidden = jobs.length > 0;
  for (const j of jobs) {
    const active = ["queued", "converting", "transcribing"].includes(j.status);
    const statusText = j.status === "error" ? `Error: ${j.error}` :
      j.status === "transcribing" ? `Transcribing… ${Math.round(j.progress * 100)}%` : STATUS_TEXT[j.status];
    const action = active
      ? el("button", { class: "btn ghost small", type: "button", text: "Cancel", "aria-label": `Cancel ${j.name}`,
          onclick: (e) => { e.stopPropagation(); api(`/api/jobs/${j.id}/cancel`, { method: "POST" }); } })
      : el("button", { class: "btn ghost small", type: "button", text: "Remove", "aria-label": `Remove ${j.name}`,
          onclick: (e) => { e.stopPropagation(); api(`/api/jobs/${j.id}`, { method: "DELETE" }); } });
    const li = el("li", { tabindex: "0", "data-id": j.id, class: j.id === state.selected ? "selected" : "",
        onclick: () => selectJob(j.id),
        onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectJob(j.id); } } },
      el("span", { class: "q-name", text: j.name, title: j.name }),
      el("span", { class: `q-status ${j.status}`, text: statusText }),
      el("span", { class: "q-actions" }, action));
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

function clearTranscript() {
  state.selected = null;
  state.segments = [];
  $("transcript").replaceChildren();
  $("transcript-meta").textContent = "Pick a file in the queue.";
  $("job-progress").hidden = true;
}

function renderTranscript(job) {
  const parts = [job.name];
  if (job.engine) parts.push(`${job.engine.backend} on ${job.engine.device.kind.toUpperCase()}`);
  if (job.elapsed) parts.push(`${job.elapsed.toFixed(1)} s`);
  $("transcript-meta").textContent = parts.join(" · ");
  $("job-progress").hidden = job.status !== "transcribing";
  $("job-progress").value = job.progress;
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
  return el("div", { class: "seg" },
    el("span", { class: "ts", text: fmtTime(seg.start) }),
    el("span", { class: "tx", text: seg.text }));
}

function applySearch() {
  const q = $("search").value.trim().toLowerCase();
  for (const node of $("transcript").querySelectorAll(".seg")) {
    const tx = node.querySelector(".tx");
    const raw = tx.textContent;
    tx.replaceChildren(raw);
    if (!q) { node.classList.remove("hidden"); continue; }
    const idx = raw.toLowerCase().indexOf(q);
    node.classList.toggle("hidden", idx < 0);
    if (idx >= 0) tx.replaceChildren(raw.slice(0, idx), el("mark", { text: raw.slice(idx, idx + q.length) }), raw.slice(idx + q.length));
  }
}

function transcriptText() {
  const withTs = $("ts-toggle").checked;
  return state.segments.map((s) => (withTs ? `[${fmtTime(s.start)}] ${s.text}` : s.text)).join("\n");
}

function upload(files) {
  if (!files.length) return;
  const status = $("upload-status");
  const { variant } = selectedModel();
  if (variant && !variant.installed) {
    status.textContent = "Download the model in Settings first.";
    status.className = "hint error";
    return;
  }
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
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
    status.textContent = xhr.status === 413 ? "File is larger than the upload limit."
      : rejected.length ? rejected.map((r) => `${r.name}: ${r.error}`).join("; ")
      : xhr.status >= 400 ? data.error || "Upload failed." : "";
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

document.addEventListener("DOMContentLoaded", () => {
  const dz = $("dropzone");
  $("file-input").addEventListener("change", (e) => { upload([...e.target.files]); e.target.value = ""; });
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("over"));
  dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("over"); upload([...e.dataTransfer.files]); });
  $("search").addEventListener("input", applySearch);
  $("ts-toggle").addEventListener("change", () => $("transcript").classList.toggle("no-ts", !$("ts-toggle").checked));
  $("copy-btn").addEventListener("click", async () => {
    await navigator.clipboard.writeText(transcriptText());
    $("copy-btn").textContent = "Copied";
    setTimeout(() => { $("copy-btn").textContent = "Copy"; }, 1200);
  });

  onState((s) => {
    state.jobs = new Map(s.jobs.map((j) => [j.id, j]));
    renderFilesModel(s);
    renderQueue();
    if (state.selected && state.jobs.has(state.selected)) selectJob(state.selected);
  });
  onEvent("job", (j) => {
    state.jobs.set(j.id, j);
    renderQueue();
    if (j.id === state.selected) {
      if (["done", "error", "cancelled"].includes(j.status)) selectJob(j.id);
      else $("job-progress").hidden = j.status !== "transcribing";
    }
    if (j.notice) showNotice(j.notice);
  });
  onEvent("job_removed", ({ id }) => {
    state.jobs.delete(id);
    if (state.selected === id) clearTranscript();
    renderQueue();
  });
  onEvent("segment", ({ id, progress, segment }) => {
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
    if (li) {
      li.querySelector(".q-status").textContent = `Transcribing… ${Math.round(progress * 100)}%`;
      const p = li.querySelector("progress");
      if (p) p.value = progress;
    }
  });
  onEvent("job_reset", ({ id }) => {
    if (id === state.selected) { state.segments = []; $("transcript").replaceChildren(); }
  });
});
