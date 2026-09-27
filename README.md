# Crowe Vibe Whisper

Private, offline dictation and transcription for Windows. It's inspired by
[Vibe](https://github.com/thewh1teagle/vibe) and rebuilt in Python + Flask with a plain
HTML/CSS/JS front end. It shares no code with Vibe.

**Nothing leaves the machine.** There's no telemetry, analytics, cloud AI or crash
reporting. The only network access is:
- model downloads you start yourself (from Hugging Face, pinned and SHA-256 verified)
- calls to a local Ollama server on loopback, for AI polish

## What it does

- **Dictation anywhere:** press a shortcut (default Ctrl+Win), talk, press it again. The
  text is pasted where your cursor is and copied to the clipboard. Speech is transcribed
  in chunks while you talk, so there's only a short wait at the end.
- **AI polish:** a second shortcut (default Ctrl+Alt+Win) has a local Ollama model clean
  the text up, or turn it into an email or bullet points.
- **Files:** drop in audio or video files for a timestamped transcript.
- Custom vocabulary, filler-word removal, microphone picker, recording indicator, tray icon.

## Install on a PC

Needs [uv](https://docs.astral.sh/uv/) (`winget install astral-sh.uv`) and ffmpeg
(`winget install Gyan.FFmpeg`). [Ollama](https://ollama.com) is optional (AI polish).

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

The installer detects the hardware and installs only the engines it can use:

| Hardware | Engines installed | Used for |
|---|---|---|
| Any PC | faster-whisper | fast int8 CPU transcription |
| Intel CPU or GPU (Arc, Iris Xe) | + OpenVINO | the Intel GPU, and a fast CPU path |
| NVIDIA GPU | + CUDA 12 libraries (~1.3 GB) | faster-whisper on the GPU |
| AMD GPU | CPU engines only | (a Vulkan engine is planned) |

Override with `-Engines cpu,intel,nvidia`. It then creates a Start menu shortcut.

The app picks the fastest engine automatically; **Settings → Speech recognition → Engine**
lets you choose a specific GPU or CPU instead. If a GPU fails, the app finishes on the CPU.
The Intel NPU isn't offered yet: OpenVINO 2026.4's Whisper pipeline fails on it
(`CVW_ENABLE_NPU=1` lists it anyway, for testing).

### Models

Open **Settings → Speech recognition**, pick a model, and click **Download model**. Each
engine has its own copy (OpenVINO and faster-whisper use different formats).
Recommended: Large v3 Turbo on a GPU, Small on a fast CPU, Base on a slow one.

**Offline / many PCs:** copy `%LOCALAPPDATA%\CroweVibeWhisper\models\` from a PC that has
the models to the same place on another PC, then click **Rescan**. Every file is checked
against its pinned SHA-256, so a damaged or altered copy is refused.

## Speed on the reference laptop

Intel Core Ultra 5 226V, Arc 130V GPU; 80 s of speech:

| Model | Engine / device | Whole recording | Last 10 s chunk (wait after you stop) |
|---|---|---|---|
| Large v3 Turbo | OpenVINO, Arc GPU | 2.1 s | 0.4 s |
| Large v3 Turbo | OpenVINO, CPU | 13.5 s | 3.9 s |
| Small | OpenVINO, Arc GPU | 4.0 s | 0.4 s |
| Small | faster-whisper, CPU | 12.8 s | 1.8 s |

Chunked transcription matched a one-pass transcription word for word (204/204 words).

## Development

```powershell
uv sync --extra cpu --extra intel   # plus --extra nvidia on NVIDIA machines
uv run python launcher.py
uv run pytest
```

## Where data lives

Everything is stored per machine in `%LOCALAPPDATA%\CroweVibeWhisper\`: settings,
models, compiled-model cache, and logs. Logs are redacted and deleted after 14 days;
dictated text is never logged.

## How it's put together

- `launcher.py`: waitress on `127.0.0.1` with a random port, a per-launch token, a pywebview window, tray icon
- `app/security.py`: session cookie from the launch token, Host check, CSRF header, CSP
- `app/engine.py`: picks the backend and device, preloads the model, falls back to the CPU if the GPU fails
- `app/backends/`: one module per inference engine (faster-whisper, OpenVINO)
- `app/dictation/`: hotkeys, microphone, live chunking (`chunker.py`, `pipeline.py`), clipboard and paste
- `app/models/`: the pinned model catalog, verified downloads and offline rescan
- `app/jobs.py`: the file queue and background worker; progress reaches the page over Server-Sent Events

## License

MIT. See [LICENSE](LICENSE).
