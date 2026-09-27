// Dictation panel: start/stop button, level meter, shortcut picker, last result.
// Uses $, el, api and the shared EventSource from app.js.
"use strict";

const dict = { snap: null, capturing: false };

const CODE_NAMES = {
  Space: "Space", Enter: "Enter", Tab: "Tab", Insert: "Insert", Delete: "Delete", Home: "Home",
  End: "End", PageUp: "PageUp", PageDown: "PageDown", Pause: "Pause", ArrowUp: "Up",
  ArrowDown: "Down", ArrowLeft: "Left", ArrowRight: "Right", Backquote: "Backquote",
  Minus: "Minus", Equal: "Equal", Comma: "Comma", Period: "Period", Slash: "Slash",
  Semicolon: "Semicolon", Quote: "Quote", BracketLeft: "BracketLeft",
  BracketRight: "BracketRight", Backslash: "Backslash",
};

function keyName(code) {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3);
  if (/^Digit[0-9]$/.test(code)) return code.slice(5);
  if (/^F([1-9]|1[0-9]|2[0-4])$/.test(code)) return code;
  return CODE_NAMES[code] || null;
}

function renderDictation(snap) {
  dict.snap = { ...(dict.snap || {}), ...snap };
  const s = dict.snap;
  const btn = $("dict-btn");
  const recording = s.state === "recording";
  btn.textContent = recording ? "Stop & transcribe" : s.state === "transcribing" ? "Transcribing…" : "Start dictation";
  btn.classList.toggle("recording", recording);
  btn.disabled = s.state === "transcribing";
  document.body.classList.toggle("dict-recording", recording);

  const status = $("dict-status");
  if (recording) status.textContent = `Listening… ${fmtTime(s.seconds || 0)}`;
  else if (s.state === "transcribing") status.textContent = "Transcribing…";
  else status.textContent = s.hotkey ? `Ready. Press ${s.hotkey} anywhere.` : "Ready";
  const level = $("dict-level");
  level.hidden = !recording;
  level.value = Math.min(1, (s.level || 0) * 3);

  if (s.hotkey !== undefined) $("hotkey-display").textContent = s.hotkey || "Off";
  $("hotkey-off").hidden = !s.hotkey;

  const err = $("dict-error");
  const message = s.hotkey_error || s.error;
  err.textContent = message || "";
  err.hidden = !message;

  if (s.history) {
    const last = s.history[0];
    $("dict-last").hidden = !last;
    $("dict-last-text").textContent = last ? last.text : "";
  }
}

async function saveHotkey(combo) {
  try {
    await api("/api/dictation/hotkey", { method: "POST", json: { hotkey: combo } });
  } catch (e) {
    renderDictation({ hotkey_error: e.message });
  }
}

function stopCapture() {
  dict.capturing = false;
  $("hotkey-capture").hidden = true;
  $("hotkey-change").textContent = "Change";
}

function wireDictation() {
  $("dict-btn").addEventListener("click", () => api("/api/dictation/toggle", { method: "POST" }).catch(console.error));
  $("dict-copy").addEventListener("click", async () => {
    await api("/api/dictation/copy", { method: "POST" });
    $("dict-copy").textContent = "Copied";
    setTimeout(() => { $("dict-copy").textContent = "Copy again"; }, 1200);
  });
  $("hotkey-off").addEventListener("click", () => saveHotkey(""));
  $("hotkey-change").addEventListener("click", () => {
    if (dict.capturing) { stopCapture(); return; }
    dict.capturing = true;
    $("hotkey-capture").hidden = false;
    $("hotkey-change").textContent = "Cancel";
  });
  // Modifiers seen while capturing, so a modifier-only chord (e.g. Ctrl+Win) can be
  // saved when its keys are released.
  let chordMods = new Set();
  const modsOf = (e) => {
    const mods = [];
    if (e.ctrlKey) mods.push("Ctrl");
    if (e.altKey) mods.push("Alt");
    if (e.shiftKey) mods.push("Shift");
    if (e.metaKey) mods.push("Win");
    return mods;
  };
  window.addEventListener("keydown", (e) => {
    if (!dict.capturing) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.key === "Escape") { stopCapture(); chordMods = new Set(); return; }
    const key = keyName(e.code);
    if (!key) { modsOf(e).forEach((m) => chordMods.add(m)); return; }
    chordMods = new Set();
    stopCapture();
    saveHotkey([...modsOf(e), key].join("+"));
  }, true);
  window.addEventListener("keyup", (e) => {
    if (!dict.capturing || !chordMods.size) return;
    e.preventDefault();
    const order = ["Ctrl", "Alt", "Shift", "Win"];
    const combo = order.filter((m) => chordMods.has(m));
    chordMods = new Set();
    if (combo.length >= 2) {
      stopCapture();
      saveHotkey(combo.join("+"));
    }
  }, true);
}

document.addEventListener("DOMContentLoaded", () => {
  wireDictation();
  const es = window.appEvents;
  es.addEventListener("dictation", (e) => renderDictation(JSON.parse(e.data)));
  es.addEventListener("dictation_level", (e) => renderDictation(JSON.parse(e.data)));
});
