# Crowe Vibe Whisper

Private, offline audio and video transcription for Windows. It's inspired by
[Vibe](https://github.com/thewh1teagle/vibe) and rebuilt in Python + Flask with a plain
HTML/CSS/JS front end. It shares no code with Vibe.

**Nothing leaves the machine.** There's no telemetry, analytics, cloud AI or crash
reporting. The only network access is:
- model downloads you start yourself (from Hugging Face, pinned and SHA-256 verified)
- calls to a local Ollama server on loopback, for summaries

## Run (development)

Requires Python 3.12, [uv](https://docs.astral.sh/uv/) and ffmpeg on PATH.

```powershell
uv sync --extra cpu      # CPU engine (faster-whisper)
uv run python launcher.py
```

The app opens in its own window. Pick a model, click **Download model** if it isn't
installed yet, then drop in audio or video files.

Tests: `uv run pytest`

## Where data lives

Everything is stored per machine in `%LOCALAPPDATA%\CroweVibeWhisper\`: settings,
models, transcripts and logs. Logs are redacted and deleted after 14 days.

## How it's put together

- `launcher.py`: waitress on `127.0.0.1` with a random port, a per-launch token, and a pywebview window
- `app/security.py`: session cookie from the launch token, Host check, CSRF header, CSP
- `app/engine.py`: picks the backend and device (NVIDIA → Intel GPU → Intel NPU → AMD → CPU), and falls back to the CPU if the GPU fails
- `app/backends/`: one module per inference engine
- `app/models/`: the pinned model catalog, verified downloads and offline rescan
- `app/jobs.py`: the queue, the background worker and cancellation. Live progress reaches the page over Server-Sent Events.

## License

MIT. See [LICENSE](LICENSE).
