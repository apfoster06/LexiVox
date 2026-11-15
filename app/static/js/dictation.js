// Dictation tab: big mic button, live status, shortcut hints, quick toggles, recent list.
"use strict";

const dict = { snap: {} };

const DICT_STATUS = {
  transcribing: "Transcribing…",
  polishing: "Polishing with AI…",
};

function renderDictation(update) {
  const s = (dict.snap = { ...dict.snap, ...update });
  const btn = $("dict-btn");
  const recording = s.state === "recording";
  const busy = s.state === "transcribing" || s.state === "polishing";
  btn.classList.toggle("recording", recording);
  btn.classList.toggle("busy", s.state === "transcribing");
  btn.classList.toggle("polishing", s.state === "polishing");
  btn.disabled = busy;
  btn.setAttribute("aria-label", recording ? "Stop and transcribe" : "Start dictation");
  document.body.classList.toggle("dict-recording", recording);

  let status;
  if (recording) status = s.mode === "polish" ? "Listening · AI polish" : "Listening";
  else if (busy) status = DICT_STATUS[s.state];
  else status = s.hotkey ? "Ready" : "Ready. Click the mic or set a shortcut in Settings.";
  $("dict-status").textContent = status;
  const loudness = recording ? Math.pow(Math.min(1, (s.level || 0) * 3.5), 0.6) : 0;
  btn.parentElement.style.setProperty("--level", loudness.toFixed(3)); // the glow follows your voice
  $("dict-time").hidden = !recording;
  if (recording) $("dict-time").textContent = fmtTime(s.seconds || 0);
  waveSet(recording || busy ? s.state : "idle", s.level);

  if (s.hotkey !== undefined) {
    $("hk-dictate").textContent = s.hotkey || "Off";
    $("hk-polish").textContent = s.polish_hotkey || "Off";
    $("hk-polish-wrap").hidden = !s.polish_hotkey;
  }
  // Text transcribed so far (chunks finish while you're still talking).
  if (s.partial !== undefined) {
    const live = s.state !== "idle" && s.partial;
    $("dict-partial").hidden = !live;
    $("dict-partial").textContent = live ? s.partial : "";
  }
  if (s.error !== undefined) {
    $("dict-error").textContent = s.error || "";
    $("dict-error").hidden = !s.error;
  }
  if (s.history) renderHistory(s.history);
}

function renderHistory(items) {
  const list = $("dict-history");
  list.replaceChildren();
  $("dict-empty").hidden = items.length > 0;
  items.forEach((item, index) => {
    const when = new Date(item.created * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    const meta = el("span", { class: "h-meta", text: `${when} · ${item.seconds}s${item.pasted ? " · pasted" : ""}` });
    if (item.polished) meta.append(el("span", { class: "tag ai", text: "AI" }));
    const copy = el("button", { class: "btn ghost small", type: "button", text: "Copy" });
    copy.addEventListener("click", async () => {
      await api("/api/dictation/copy", { method: "POST", json: { index } });
      copy.textContent = "Copied";
      setTimeout(() => { copy.textContent = "Copy"; }, 1200);
    });
    list.append(el("li", {},
      el("div", { class: "h-text", text: item.text }), meta,
      el("div", { class: "h-actions" }, copy)));
  });
}

function renderQuickToggles(s) {
  $("q-auto-paste").checked = s.settings.auto_paste;
  $("q-fillers").checked = s.settings.remove_fillers;
  $("q-style").value = s.settings.polish_style;
}

document.addEventListener("DOMContentLoaded", () => {
  $("dict-btn").addEventListener("click", () =>
    api("/api/dictation/toggle", { method: "POST", json: { mode: "dictate" } }).catch((e) => showNotice(e.message)));
  $("q-auto-paste").addEventListener("change", (e) => {
    saveSetting({ auto_paste: e.target.checked });
    $("set-auto-paste").checked = e.target.checked;
  });
  $("q-fillers").addEventListener("change", (e) => {
    saveSetting({ remove_fillers: e.target.checked });
    $("set-fillers").checked = e.target.checked;
  });
  $("q-style").addEventListener("change", (e) => {
    saveSetting({ polish_style: e.target.value });
    $("set-style").value = e.target.value;
  });
  onState((s) => { renderQuickToggles(s); renderDictation(s.dictation); });
  onEvent("dictation", renderDictation);
  onEvent("dictation_level", renderDictation);
});
