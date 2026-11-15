// Live waveform on the Dictation tab: bars that scroll right to left while you talk
// (like a voice memo), and a travelling wave while the app transcribes or polishes.
// Same look as the floating pill (app/overlay_render.py).
"use strict";

const WAVE = {
  pitch: 6, bar: 3.5, barSeconds: 0.075,
  colors: {
    recording: ["#ff8a1f", "#ff2dc8"],
    transcribing: ["#7a4dff", "#d97bff"],
    polishing: ["#b4e000", "#3fd9a0"],
  },
};

const wave = {
  mode: "idle", target: 0, env: 0, frac: 0, hist: new Array(96).fill(0),
  last: 0, raf: 0, still: false,
};

function waveSet(mode, level) {
  const canvas = $("dict-wave");
  if (mode !== wave.mode) {
    if (mode === "recording") { wave.hist.fill(0); wave.env = 0; }
    wave.mode = mode;
  }
  wave.target = mode === "recording" ? Math.pow(Math.min(1, (level || 0) * 3.5), 0.6) : 0;
  canvas.hidden = mode === "idle";
  if (mode === "idle") {
    cancelAnimationFrame(wave.raf);
    wave.raf = 0;
  } else if (!wave.raf) {
    wave.last = performance.now();
    wave.raf = requestAnimationFrame(waveFrame);
  }
}

function waveFrame(now) {
  const dt = Math.min(0.25, (now - wave.last) / 1000);
  wave.last = now;
  // Quick to rise, slower to fall, and eased between the 12 levels a second we receive.
  const rate = wave.target > wave.env ? 22 : 9;
  wave.env += (wave.target - wave.env) * Math.min(1, dt * rate);
  if (wave.mode === "recording") {
    wave.frac += dt / WAVE.barSeconds;
    while (wave.frac >= 1) { wave.frac -= 1; wave.hist.push(wave.env); wave.hist.shift(); }
  }
  waveDraw(wave.still ? 0 : now / 1000);
  wave.raf = wave.mode === "idle" ? 0 : requestAnimationFrame(waveFrame);
}

function waveDraw(t) {
  const canvas = $("dict-wave");
  const ratio = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  if (canvas.width !== Math.round(w * ratio) || canvas.height !== Math.round(h * ratio)) {
    canvas.width = Math.round(w * ratio);
    canvas.height = Math.round(h * ratio);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const [c0, c1] = WAVE.colors[wave.mode] || WAVE.colors.recording;
  const grad = ctx.createLinearGradient(0, 0, w, 0);
  grad.addColorStop(0, c0);
  grad.addColorStop(1, c1);
  ctx.fillStyle = grad;
  const recording = wave.mode === "recording";
  const frac = recording && !wave.still ? wave.frac : 0;
  const n = wave.hist.length;
  for (let i = -1; ; i++) {
    const x = w - WAVE.bar / 2 - (i + frac) * WAVE.pitch;
    if (x < 0) break;
    let v;
    if (recording) v = i < 0 ? wave.env : i < n ? wave.hist[n - 1 - i] : 0;
    else v = 0.1 + 0.75 * Math.pow(0.5 + 0.5 * Math.sin(t * 5.5 + x * 0.11), 2);
    const fade = Math.min(1, x / 60) * Math.min(1, Math.max(0, w - x) / 14);
    const height = 4 + (h - 6) * v;
    ctx.globalAlpha = fade * (0.6 + 0.4 * v);
    ctx.beginPath();
    ctx.roundRect(x - WAVE.bar / 2, (h - height) / 2, WAVE.bar, height, WAVE.bar / 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}

document.addEventListener("DOMContentLoaded", () => {
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
  wave.still = calm.matches;  // reduced motion: bars step instead of gliding
  calm.addEventListener("change", (e) => { wave.still = e.matches; });
});
