# Development

## Setup

```powershell
uv sync --extra cpu --extra intel      # add --extra nvidia on an NVIDIA machine
uv run python launcher.py              # the app, in its window
uv run pytest                          # the test suite
```

Python 3.12 exactly (`requires-python = ">=3.12,<3.13"`). No Node, no build step.

## Tests

137 tests (parametrized cases included). They use a fake engine, fake microphone, fake
clipboard, fake paste and fake AI, so they never type into your windows or record
you, except where noted.

| File | Covers |
|---|---|
| `test_security.py` | 401 without cookie (pages, API, static), single-use launch token, cookie flags, wrong Host 403, CSRF header 403, CSP and headers, no inline script |
| `test_logging.py` | Redaction of home folder, file names and query strings; tracebacks; log retention |
| `test_models.py` | Catalog fully pinned; verified download from a local fake server; hash mismatch deletes; offline rescan and tamper detection; unsafe names and paths rejected |
| `test_engine.py` | Auto-selection for NVIDIA, Intel GPU+NPU, AMD, CPU-only; GPU failure falls back to CPU; manual choice, CPU only, benchmark winner; CUDA DLL setup without NVIDIA packages |
| `test_api_jobs.py` | Upload to transcript through the API; random temp names; unsupported and fake files rejected; upload limit; settings validation |
| `test_dictation.py` | Shortcut parsing, modifier-only chords (release-only, Ctrl+Win+D ignored, missed key-ups), dictation flow, copy again, too short, mic failure, auto-paste setting. **Real Windows:** a real shortcut firing on simulated keys, a keyboard-hook chord (Ctrl+Alt), a clash with another app, and a clipboard round trip that restores your clipboard |
| `test_options.py` | Filler removal cases, vocabulary, Ollama loopback validation, Ollama client against a fake server, polish per mode and fallback, settings validation, two shortcuts can't match, pill placement |
| `test_chunking.py` | Cut points land in pauses with sane lengths; no cut before 8 s; continuous sound cut at 25 s; noisy rooms; chunks transcribed while recording with context; Clean up per chunk; Email once; polish failure |
| `test_openvino.py` | Engine setting round trip; the telemetry module never loads; **real** OpenVINO transcription on CPU and GPU |
| `test_integration.py` | **Real** faster-whisper transcription through the HTTP API |
| `test_config.py` | Pre-rename data folder moved once; BOM in config.json |

Tests marked **real** need the matching packages and a downloaded model in
`%LOCALAPPDATA%\LexiVox\models`; they skip otherwise. They're marked `integration`
(`uv run pytest -m "not integration"` skips them).

## Conventions

- Keep files under about 500 lines; split by responsibility.
- Front end: plain JS, no frameworks, no inline scripts or styles (the CSP blocks them),
  server text only through `textContent`. Colours come from the tokens in `app.css`.
- Subprocesses: argument lists only, never `shell=True`.
- Never log transcript or dictated text.
- New outbound connections must be added to [network-egress.md](network-egress.md).
- Commits are small and each leaves the tests passing.

## How to…

**Add a speech engine.** Implement the `Backend` protocol in `app/backends/base.py`
(`available_devices`, `load`, `transcribe`, `unload`) in a new module, register it in
`app/backends/__init__.py` behind an `is_installed()` check, add a uv extra in
`pyproject.toml`, add its models to `scripts/pin_models.py`, and give it a rank in
`auto_order` (`app/engine.py`). `install.ps1` decides when to install the extra.

**Add or update a model.** Edit `MODELS` in `scripts/pin_models.py`, run
`uv run python scripts/pin_models.py` (it resolves the current commit and SHA-256 of
every file and rewrites `app/models/catalog.toml`), and review the diff.

**Add a setting.** Add a field to `Settings` (`app/config.py`); add it to `EDITABLE` and
`_validate` in `app/routes/settings_api.py`; add the control in `index.html` and wire it
in `settings.js` (`bind(...)`); add a validation test in `test_options.py`.

**Change the icon or colours.** Replace `assets/brand/icon.png` (a tile with a light
glyph on a transparent background works best), run `uv run python -m app.brand` to
regenerate `app/static/img`, and regenerate `assets/brand/lexivox.ico`. State colours
are in `app/brand.py` (tray), `app/overlay_render.py` (pill), `app/static/js/waveform.js`
(in-app waveform) and `app/static/css/app.css`.

## Decision log

| Decision | Why |
|---|---|
| Python + Flask instead of hardening the Tauri/Rust Vibe fork | The machine had Python, uv, ffmpeg and Ollama but no Rust toolchain; a smaller, auditable codebase |
| pywebview window instead of a browser tab | Behaves like a desktop app; same WebView2 engine Tauri uses |
| OpenVINO for Intel, faster-whisper for CPU/NVIDIA | Measured: OpenVINO on the Arc GPU is about 6× faster than the CPU; faster-whisper can't use Intel or AMD GPUs |
| Own downloader instead of huggingface_hub | Pinned revisions, SHA-256 per file, resume, and the library kept offline |
| Win32 via ctypes (hotkeys, clipboard, paste, DPI, app ID) | No extra packages to trust |
| Keyboard hook only for modifier-only shortcuts | `RegisterHotKey` can't bind Ctrl+Win; the hook is only installed when needed |
| Cut chunks at pauses, not fixed times | Words aren't split; measured identical to one-pass transcription |
| NPU hidden | OpenVINO 2026.4's Whisper pipeline fails on the 226V NPU |
| Process-wide DPI awareness at startup | The recording pill was positioned in scaled units before WebView2 switched the process to physical pixels |
| Start menu shortcut with an app ID | The only way Windows shows "LexiVox" (not "Python") on notifications |
| Models not stored in the Git repo | GitHub's 100 MB file limit; LFS's 2 GB file limit and monthly transfer allowance; see the offline kit idea below |

## Status against the original spec

The spec is `python-transcriber-prompt.md`. Beyond it, the app gained hotkey dictation,
auto-paste, AI polish, live chunking, the tray, the recording pill, vocabulary, filler
removal and the branding.

| Spec item | Status |
|---|---|
| Security model (loopback, token, Host, CSRF, CSP, upload rules, path checks) | Done, tested |
| OpenVINO and faster-whisper backends, auto-selection, CPU fallback | Done, tested |
| Model catalog with pinned revisions and SHA-256, offline rescan | Done, tested |
| `install.ps1` hardware detection, per-machine config, pywebview window | Done |
| Batch queue, SSE progress, cancel, transcript search/timestamps/copy | Done |
| Redacted logs with retention | Done, tested |
| Network egress doc, hardware matrix | Done (this folder) |
| **Exports: TXT, SRT, VTT, JSON, DOCX, PDF, HTML** | **Not built** |
| **Ollama summaries of file transcripts** (`summary_prompt` exists, unused) | **Not built** |
| **Saved transcript history** (list, open, delete) | **Not built**; transcripts are in memory only |
| **"Benchmark this machine" button** (the engine can already use a saved winner) | **Not built** |
| **pip-audit dependency audit** | **Not run** |
| Log retention and upload limit in the UI | Settings exist, no controls yet |
| whisper.cpp Vulkan engine for AMD GPUs (Phase 2) | Not built |
| Speaker identification (Phase 2) | Not built |

## Known issues and ideas

- Starting LexiVox twice runs two copies that fight over shortcuts; a second launch
  should bring the first window forward instead.
- The NVIDIA path is built to faster-whisper's documented requirements but hasn't been
  tested on NVIDIA hardware.
- `install.ps1` needs the internet (Python packages). A fully offline "LexiVox kit"
  (packages, ffmpeg, models, Ollama installer) or a PyInstaller + Inno Setup `.exe`
  installer would remove that.
- Windows caches shortcut icons, so a changed icon can take a sign-out to appear in
  notifications.
