// Crowe Vibe Whisper: shared helpers, tabs, state loading and the event stream.
// Plain JS, no build step. Server text is inserted with textContent, never innerHTML.
// dictation.js, files.js and settings.js register renderers and event handlers here.
"use strict";

const HEADER = { "X-Requested-With": "CroweVibeWhisper" };
const $ = (id) => document.getElementById(id);

const state = {
  server: null,     // last /api/state
  jobs: new Map(),  // id -> job summary (files tab)
  selected: null,   // selected job id
  segments: [],     // segments of the selected job
};

const renderers = [];   // fn(serverState) called after every /api/state load
const eventHandlers = {}; // event name -> [fn(data)]

function onState(fn) { renderers.push(fn); }
function onEvent(name, fn) { (eventHandlers[name] ||= []).push(fn); }

async function api(path, { method = "GET", json } = {}) {
  const opts = { method, headers: { ...HEADER }, credentials: "same-origin" };
  if (json !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(json);
  }
  const resp = await fetch(path, opts);
  let data = {};
  try { data = await resp.json(); } catch { /* empty body */ }
  if (!resp.ok) throw new Error(data.error || `Request failed (${resp.status})`);
  return data;
}

function saveSetting(changes) {
  return api("/api/settings", { method: "POST", json: changes }).catch((e) => showNotice(e.message));
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
  return h ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
}

function fmtBytes(n) {
  return n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.round(n / 1e6)} MB`;
}

function showNotice(text) {
  const n = $("notice");
  n.textContent = text || "";
  n.hidden = !text;
}

async function loadState() {
  const s = await api("/api/state");
  state.server = s;
  applyTheme(s.settings.theme);
  const c = s.choice;
  $("engine-badge").textContent = c
    ? `${c.backend} · ${c.device.kind.toUpperCase()} · ${c.device.name}` : "No engine installed";
  for (const fn of renderers) {
    try { fn(s); } catch (e) { console.error(e); }
  }
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme || "system";
}

// ---- tabs -------------------------------------------------------------------------

function showTab(name) {
  for (const btn of document.querySelectorAll(".tab-btn")) {
    const on = btn.dataset.tab === name;
    btn.setAttribute("aria-selected", on ? "true" : "false");
    btn.tabIndex = on ? 0 : -1;
    $(`tab-${btn.dataset.tab}`).hidden = !on;
  }
  try { localStorage.setItem("tab", name); } catch { /* storage may be off */ }
}

function wireTabs() {
  const buttons = [...document.querySelectorAll(".tab-btn")];
  for (const btn of buttons) {
    btn.addEventListener("click", () => showTab(btn.dataset.tab));
    btn.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const i = buttons.indexOf(btn) + (e.key === "ArrowRight" ? 1 : -1);
      const next = buttons[(i + buttons.length) % buttons.length];
      showTab(next.dataset.tab);
      next.focus();
    });
  }
  let saved = "dictation";
  try { saved = localStorage.getItem("tab") || saved; } catch { /* ignore */ }
  showTab($(`tab-${saved}`) ? saved : "dictation");
}

// ---- events -----------------------------------------------------------------------

function connectEvents() {
  const es = new EventSource("/api/events");
  es.addEventListener("sync", () => loadState().catch(console.error));
  for (const name of ["job", "job_removed", "segment", "job_reset", "download", "dictation", "dictation_level"]) {
    es.addEventListener(name, (e) => {
      const data = JSON.parse(e.data);
      for (const fn of eventHandlers[name] || []) {
        try { fn(data); } catch (err) { console.error(err); }
      }
    });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  wireTabs();
  $("notice").addEventListener("click", () => showNotice(""));
  // Dropping a file anywhere must not navigate the window to it.
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());
  // Lets the Edge app-mode fallback notice when the window is closed.
  setInterval(() => api("/api/heartbeat", { method: "POST" }).catch(() => {}), 5000);
  // Other scripts wire their controls on DOMContentLoaded too (they run after this one).
  setTimeout(() => {
    loadState().catch((e) => showNotice(e.message));
    connectEvents();
  }, 0);
});
