# Architecture

LexiVox is one Python process that runs a small local web server and shows it in a
native window. Python is the glue; the heavy work happens in compiled inference engines
(OpenVINO, CTranslate2) that Python calls.

## Processes and threads

```
launcher.py (main thread)
 ├─ waitress HTTP server on 127.0.0.1:<random port>  ── Flask app (app/__init__.py)
 │    └─ 16 worker threads (API requests, the Server-Sent Events stream)
 ├─ pywebview window (WebView2)  ── runs on the main thread until the app quits
 ├─ tray icon (pystray)                          thread "tray"
 ├─ recording pill (Win32)                      thread "overlay"
 ├─ global shortcuts (Win32 message loop)        thread "hotkey"
 ├─ file job worker                              thread "job-worker"
 ├─ dictation: monitor + chunk worker            threads "dictation-monitor", "dictation-chunks"
 ├─ model download                               thread "model-download" (when running)
 └─ engine warm-up at startup                    thread "engine-warmup"
```

Startup (`launcher.main`): block the `openvino_telemetry` module, make the process
per-monitor DPI aware (before any window exists), set up redacted logging, create the
Flask app with a random launch token, start waitress on port 0 (the OS picks a free
port), then write the icon files, register the app ID, remove the pre-rename
identity, start the tray, overlay and hotkeys, and open the window at
`/?launch=<token>`.

Closing the window hides it (the `closing` event is cancelled) so shortcuts keep
working; **Quit** in the tray destroys the window, which returns from
`webview.start()`, and the `finally` block stops dictation, jobs, tray, overlay and the
server. If pywebview can't start, `--edge` mode opens Edge in app mode with an isolated
profile; without a tray it shuts down when the page's heartbeat stops for 30 s.

## Request flow and security

Every request passes `app/security.py`:

1. **Host check:** only `127.0.0.1:<port>` or `localhost:<port>` (blocks DNS rebinding).
2. **Session:** `/?launch=<token>` is exchanged once for an HttpOnly, SameSite=Strict
   cookie holding a different random secret, then redirects to `/`. Every other route,
   including static files, requires that cookie.
3. **CSRF:** POST/DELETE must carry `X-Requested-With: LexiVox`.
4. **Headers:** strict CSP (`default-src 'self'`, no inline script or style), nosniff,
   no-referrer, frame-ancestors none.

See [Security and privacy](security-and-privacy.md) for the full model.

## The front end

Plain HTML, CSS and JavaScript with no build step (`app/templates/index.html`,
`app/static/`). Server text is only ever inserted with `textContent`.

| File | Role |
|---|---|
| `js/app.js` | Shared helpers (`api`, `el`, `saveSetting`), tabs, state loading, the EventSource; other scripts register renderers with `onState` and handlers with `onEvent` |
| `js/dictation.js` | The tile button, live status and partial text, quick toggles, recent dictations |
| `js/files.js` | Upload, queue, transcript view |
| `js/settings.js` | All settings, the shortcut picker, microphones, Ollama status, engine and model |
| `css/app.css` | Electropop design tokens (light and dark) and components |

The page loads `/api/state` once, then follows live changes over one Server-Sent Events
stream (`/api/events`). If it falls behind, the server sends `sync` and the page reloads
the state. See [HTTP API](api.md).

## Speech engines

`app/backends/base.py` defines the interface every engine implements:
`available_devices()`, `load(model, device)`, `transcribe(wav, options, on_segment,
cancel)`, `unload()`. The rest of the app only sees `Segment(start, end, text)`.

| Backend | Module | Devices |
|---|---|---|
| faster-whisper (CTranslate2) | `faster_whisper_backend.py` | NVIDIA GPU (CUDA float16), CPU (int8) |
| OpenVINO GenAI | `openvino_backend.py` | Intel GPU, CPU (NPU hidden, see setup) |

`app/backends/__init__.py` loads only the engines whose packages are installed.
OpenVINO compiles models to a cache (`ov-cache`) so later GPU loads take under a second;
it takes audio in 120-second blocks so file progress updates and cancel works.

### Choosing an engine (`app/engine.py`)

`Engine.current()` decides, in order:

1. "CPU only" in Settings: the best CPU engine.
2. A specific engine chosen in Settings.
3. A saved benchmark winner (not yet produced by the UI).
4. Automatic: NVIDIA GPU (faster-whisper) > Intel GPU (OpenVINO) > Intel NPU > AMD GPU
   (whisper.cpp, future) > CPU (faster-whisper, then OpenVINO).

Devices that fail are remembered for the session (`Engine.failed`) and skipped. A
failure on a GPU or NPU is retried once on the CPU of the same engine (same model files)
and the user sees "GPU failed, finished on CPU". `Engine.preload()` loads the model at
startup and whenever recording starts. `Engine.transcribe` holds a lock, so file jobs
and dictation chunks take turns.

## Dictation

```
hotkey thread ──toggle(mode)──▶ DictationService (app/dictation/service.py)
                                  │ start: MicRecorder.start(), ChunkPipeline(), preload model, warm Ollama
                                  ▼
monitor thread, every 0.25 s:  pipeline.poll() ─ find_cut() on audio since the last cut
                                  │ a pause found → queue (start, end)
                                  ▼
chunk worker: read samples → WAV → ffmpeg 16 kHz → Engine.transcribe (prompt = vocabulary
              + last 200 chars) → remove fillers → [Clean up: polish this chunk]
                                  ▼
stop: recorder.stop(); pipeline.finish() queues the rest and waits → join text
      → [Email/Bullets, or chunk polish missing: polish all] → copy → paste → history
```

- **Shortcuts** (`hotkey.py`): normal combinations use Win32 `RegisterHotKey`;
  modifier-only chords such as Ctrl+Win use a low-level keyboard hook
  (`ModifierChord`). The chord fires on release only if no other key was pressed in
  between, taps an unassigned key (0xE8) so Win doesn't open Start, and prunes
  modifiers whose key-up was missed (e.g. after Win+L). Both run on one thread with a
  message loop; bindings are named (`dictate`, `polish`).
- **Recording** (`recorder.py`): sounddevice (PortAudio) at the device's native rate
  into a growing int16 buffer that can be read while recording continues. The
  microphone list uses WASAPI names.
- **Where to cut** (`chunker.py`): 30 ms frames; "quiet" is above the noise floor (5th
  percentile) but well below speech (30% of the median). Cuts are never before 8 s; from
  12 s the longest pause of at least 0.3 s is taken; at 25 s it cuts at the quietest
  moment. Measured: chunked transcription matched one-pass word for word.
- **Clipboard and paste** (`clipboard.py`, `paste.py`): Win32 via ctypes. Text is flagged
  `CanUploadToCloudClipboard = 0`. Paste waits for all modifiers to be released, skips
  LexiVox's own window, then sends Ctrl+V.
- **AI polish** (`app/ollama.py`): HTTP to Ollama on loopback only, no proxies,
  `think: false`, temperature 0.2. Clean up runs per chunk; Email and Bullets once at
  the end. If Ollama fails, the spoken text is pasted and the user is told.
- **Text** (`app/textproc.py`): filler removal (keeps "mm-hmm" and "uh-huh") and the
  vocabulary prompt ("Glossary: …", at most 600 characters).

## File transcription

`POST /api/jobs` saves each upload under a random name in `tmp\`, checks it with
ffprobe, and queues a `Job`. One worker (`app/jobs.py`) converts to 16 kHz mono WAV
(`app/audio.py`), transcribes, streams each segment as an event, and deletes both files.
ffmpeg and ffprobe are called with argument lists, `-protocol_whitelist file` and a
`file:` prefix, so a crafted file can't make them open network URLs.

## Models (`app/models/`)

`catalog.toml` pins every model to a Hugging Face commit with the SHA-256 and size of
each file (generated by `scripts/pin_models.py`). `ModelManager` streams downloads to
`.part` files, hashing as it goes, and only renames them into place when the hash
matches. A folder counts as installed when a `.verified.json` marker matches the pinned
revision and every file has the right size. **Rescan** re-hashes everything on disk.
Catalog paths are checked so they can't escape the models folder.

## Desktop integration

| Module | Does |
|---|---|
| `app/tray.py` | pystray icon and menu; the icon is recoloured per dictation state |
| `app/overlay.py` | The recording pill: a layered Win32 window (drawn by `app/overlay_render.py` with Pillow) styled `WS_EX_NOACTIVATE`, `TOOLWINDOW`, `TRANSPARENT`, topmost, placed on the monitor with the focused window, scaled for its DPI |
| `app/winshell.py` | DPI awareness, app ID, registry display name and icon, window icon, Start menu shortcut, removing pre-rename leftovers |
| `app/brand.py` | Splits `assets/brand/icon.png` into tile and glyph masks and recolours them; `python -m app.brand` regenerates `app/static/img` |

## Settings and data

`app/config.py`: `Settings` (a dataclass) is stored as JSON; unknown keys and wrong types
fall back to defaults, a BOM is tolerated, saves are atomic. `data_dir()` resolves
`%LOCALAPPDATA%\LexiVox` (or `LEXIVOX_DATA_DIR`) and moves the pre-rename folder once.

## File map

| Path | Purpose |
|---|---|
| `launcher.py` | Entry point: server, window, tray, overlay, hotkeys, shutdown |
| `app/__init__.py` | App factory, services container, temp cleanup, engine warm-up |
| `app/security.py` | Launch token, cookie, Host/CSRF checks, security headers |
| `app/config.py` | Paths, settings, data-folder migration |
| `app/logging_setup.py` | Rotating logs, redaction, retention |
| `app/engine.py` | Engine choice, preload, CPU fallback, model recommendation |
| `app/backends/*` | Engine interface, faster-whisper, OpenVINO, hardware facts |
| `app/jobs.py` | File queue and worker |
| `app/audio.py` | ffprobe and ffmpeg helpers |
| `app/events.py` | Event bus behind the SSE stream |
| `app/models/*` | Catalog, verified downloads, download service |
| `app/dictation/*` | Service, pipeline, chunker, recorder, hotkeys, clipboard, paste |
| `app/ollama.py` | Loopback-only Ollama client and polish prompts |
| `app/textproc.py` | Filler removal, vocabulary prompt |
| `app/tray.py`, `app/overlay.py`, `app/winshell.py`, `app/brand.py` | Desktop integration and branding |
| `app/routes/*` | Page, JSON API, settings/dictation API |
| `app/languages.py` | Whisper's language list |
| `scripts/*` | Install, Ollama, shortcuts, run, model pinning |
| `tests/*` | pytest suite (see [Development](development.md)) |
