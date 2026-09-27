// Settings tab: shortcuts, microphone, text options, vocabulary, AI polish, model, theme.
"use strict";

const hk = { capturing: null, mods: new Set() };

const CODE_NAMES = {
  Space: "Space", Enter: "Enter", Tab: "Tab", Insert: "Insert", Delete: "Delete", Home: "Home",
  End: "End", PageUp: "PageUp", PageDown: "PageDown", Pause: "Pause", ArrowUp: "Up",
  ArrowDown: "Down", ArrowLeft: "Left", ArrowRight: "Right", Backquote: "Backquote",
  Minus: "Minus", Equal: "Equal", Comma: "Comma", Period: "Period", Slash: "Slash",
  Semicolon: "Semicolon", Quote: "Quote", BracketLeft: "BracketLeft",
  BracketRight: "BracketRight", Backslash: "Backslash",
};
const MOD_ORDER = ["Ctrl", "Alt", "Shift", "Win"];

function keyName(code) {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3);
  if (/^Digit[0-9]$/.test(code)) return code.slice(5);
  if (/^F([1-9]|1[0-9]|2[0-4])$/.test(code)) return code;
  return CODE_NAMES[code] || null;
}

function modsOf(e) {
  return MOD_ORDER.filter((m) => ({ Ctrl: e.ctrlKey, Alt: e.altKey, Shift: e.shiftKey, Win: e.metaKey })[m]);
}

// ---- rendering --------------------------------------------------------------------

function renderShortcuts(d) {
  $("set-hk-dictate").textContent = d.hotkey || "Off";
  $("set-hk-polish").textContent = d.polish_hotkey || "Off";
  const errors = Object.values(d.hotkey_errors || {});
  $("hk-error").textContent = errors.join(" ");
  $("hk-error").hidden = !errors.length;
}

function renderSettings(s) {
  const st = s.settings;
  renderShortcuts({ ...s.dictation, hotkey: st.hotkey, polish_hotkey: st.polish_hotkey });
  $("set-sounds").checked = st.sounds;
  $("set-maxlen").value = st.max_record_minutes;
  $("set-auto-paste").checked = st.auto_paste;
  $("set-fillers").checked = st.remove_fillers;
  if (document.activeElement !== $("set-vocab")) $("set-vocab").value = st.vocabulary;
  $("set-style").value = st.polish_style;
  $("set-theme").value = st.theme;

  const langSel = $("language-select");
  if (!langSel.options.length) {
    langSel.append(el("option", { value: "auto", text: "Detect automatically" }));
    for (const [code, name] of Object.entries(s.languages).sort((a, b) => a[1].localeCompare(b[1]))) {
      langSel.append(el("option", { value: code, text: name }));
    }
  }
  langSel.value = st.language;
  $("translate-check").checked = st.translate;

  const backend = s.choice && s.choice.backend;
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
  renderModelStatus();
  renderDownload(s.download);
}

function modelInfo() {
  const s = state.server;
  const name = $("model-select").value;
  const backend = s.choice && s.choice.backend;
  const m = s.models.find((x) => x.name === name);
  return { name, backend, variant: m && backend ? m.variants[backend] : null };
}

function renderModelStatus() {
  const { name, backend, variant } = modelInfo();
  const status = $("model-status");
  const row = $("download-row");
  status.className = "hint";
  const d = state.server.download;
  const downloading = d && d.status === "downloading";
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
    row.hidden = !downloading;
  } else {
    status.textContent = `Not installed. ${fmtBytes(variant.size)} download from Hugging Face, SHA-256 verified.`;
    row.hidden = false;
  }
}

function renderDownload(d) {
  if (state.server) state.server.download = d;
  const busy = d && d.status === "downloading";
  $("download-btn").hidden = !!busy;
  $("download-cancel").hidden = !busy;
  $("download-progress").hidden = !busy;
  const text = $("download-text");
  text.className = "hint";
  if (busy) {
    $("download-progress").value = d.total ? d.done / d.total : 0;
    text.textContent = `${d.model}: ${fmtBytes(d.done)} of ${fmtBytes(d.total)}`;
  } else if (d && d.status === "error") {
    text.textContent = d.error || "Download failed";
    text.className = "hint error";
  } else {
    text.textContent = d && d.status === "cancelled" ? "Download cancelled." : "";
  }
}

async function loadMicrophones() {
  const sel = $("set-mic");
  const { microphones, selected } = await api("/api/microphones");
  sel.replaceChildren(el("option", { value: "", text: "Windows default" }));
  for (const name of microphones) sel.append(el("option", { value: name, text: name }));
  if (selected && !microphones.includes(selected)) {
    sel.append(el("option", { value: selected, text: `${selected} (not connected)` }));
  }
  sel.value = selected;
}

async function loadOllama() {
  const status = $("ollama-status");
  const sel = $("set-ollama-model");
  const o = await api("/api/ollama/status");
  sel.replaceChildren();
  if (!o.running) {
    status.textContent = `${o.error} AI polish will paste the text as spoken until it's running.`;
    status.className = "hint error";
    sel.disabled = true;
    return;
  }
  sel.disabled = false;
  status.textContent = `Ollama is running with ${o.models.length} model${o.models.length === 1 ? "" : "s"}.`;
  status.className = "hint ok";
  sel.append(el("option", { value: "", text: `Automatic (${o.default || "none"})` }));
  for (const m of o.models) sel.append(el("option", { value: m, text: m }));
  sel.value = o.models.includes(o.selected) ? o.selected : "";
}

// ---- shortcut capture -------------------------------------------------------------

async function saveHotkey(mode, combo) {
  $("hk-error").hidden = true;
  try {
    await api("/api/dictation/hotkey", { method: "POST", json: { mode, hotkey: combo } });
  } catch (e) {
    $("hk-error").textContent = e.message;
    $("hk-error").hidden = false;
  }
}

function stopCapture() {
  hk.capturing = null;
  hk.mods = new Set();
  $("hk-capture").hidden = true;
  for (const b of document.querySelectorAll(".hk-change")) b.textContent = "Change";
}

function wireShortcuts() {
  for (const b of document.querySelectorAll(".hk-change")) {
    b.addEventListener("click", () => {
      const was = hk.capturing;
      stopCapture();
      if (was === b.dataset.mode) return;
      hk.capturing = b.dataset.mode;
      b.textContent = "Cancel";
      $("hk-capture").hidden = false;
    });
  }
  for (const b of document.querySelectorAll(".hk-off")) {
    b.addEventListener("click", () => saveHotkey(b.dataset.mode, ""));
  }
  window.addEventListener("keydown", (e) => {
    if (!hk.capturing) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.key === "Escape") { stopCapture(); return; }
    const key = keyName(e.code);
    if (!key) { modsOf(e).forEach((m) => hk.mods.add(m)); return; }
    const mode = hk.capturing;
    stopCapture();
    saveHotkey(mode, [...modsOf(e), key].join("+"));
  }, true);
  // Modifier-only chords (e.g. Ctrl+Win) are saved when the keys are released.
  window.addEventListener("keyup", (e) => {
    if (!hk.capturing || !hk.mods.size) return;
    e.preventDefault();
    const combo = MOD_ORDER.filter((m) => hk.mods.has(m));
    const mode = hk.capturing;
    hk.mods = new Set();
    if (combo.length >= 2) { stopCapture(); saveHotkey(mode, combo.join("+")); }
  }, true);
}

// ---- wiring -----------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  wireShortcuts();
  const bind = (id, key, read = (t) => t.checked) =>
    $(id).addEventListener("change", (e) => saveSetting({ [key]: read(e.target) }));
  bind("set-sounds", "sounds");
  bind("set-auto-paste", "auto_paste");
  bind("set-fillers", "remove_fillers");
  bind("set-style", "polish_style", (t) => t.value);
  bind("set-mic", "mic_device", (t) => t.value);
  bind("set-ollama-model", "ollama_model", (t) => t.value);
  bind("language-select", "language", (t) => t.value);
  bind("translate-check", "translate");
  $("set-maxlen").addEventListener("change", (e) => {
    const v = Math.max(1, Math.min(60, parseInt(e.target.value, 10) || 15));
    e.target.value = v;
    saveSetting({ max_record_minutes: v });
  });
  $("set-theme").addEventListener("change", (e) => { applyTheme(e.target.value); saveSetting({ theme: e.target.value }); });
  $("vocab-save").addEventListener("click", async () => {
    await saveSetting({ vocabulary: $("set-vocab").value });
    $("vocab-status").textContent = "Saved.";
    setTimeout(() => { $("vocab-status").textContent = ""; }, 1500);
  });
  $("model-select").addEventListener("change", () => {
    saveSetting({ model: $("model-select").value }).then(() => loadState());
  });
  $("mic-refresh").addEventListener("click", () => loadMicrophones().catch(console.error));
  $("download-btn").addEventListener("click", async () => {
    const { name, backend } = modelInfo();
    try {
      const { download } = await api("/api/models/download", { method: "POST", json: { backend, model: name } });
      renderDownload(download);
      renderModelStatus();
    } catch (err) {
      renderDownload({ status: "error", error: err.message });
    }
  });
  $("download-cancel").addEventListener("click", () => api("/api/models/download/cancel", { method: "POST" }));
  $("tabbtn-settings").addEventListener("click", () => {
    loadMicrophones().catch(console.error);
    loadOllama().catch(console.error);
  });

  onState(renderSettings);
  onEvent("dictation", (d) => { if (d.hotkey !== undefined) renderShortcuts(d); });
  onEvent("download", (d) => {
    renderDownload(d);
    if (d.status === "done") loadState().catch(console.error);
  });
  loadMicrophones().catch(console.error);
  loadOllama().catch(console.error);
});
