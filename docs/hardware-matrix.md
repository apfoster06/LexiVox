# Hardware matrix

## Measured on the reference laptop

Intel Core Ultra 5 226V (8 cores), Intel Arc 130V GPU (8 GB shared), Intel AI Boost NPU,
16 GB RAM, Windows 11. OpenVINO 2026.4, faster-whisper 1.2.1 (CTranslate2 4.8).
Test audio: 80 s of English speech (Windows text-to-speech), measured 2026-09-27.

| Model | Engine | Device | 80 s file | Last 10 s chunk (wait after you stop) | Model load |
|---|---|---|---|---|---|
| Large v3 Turbo (int8) | OpenVINO | Arc GPU | **2.1 s** | **0.4 s** | 13 s first time, 0.8 s cached |
| Large v3 Turbo (int8) | OpenVINO | CPU | 13.5 s | 3.9 s | 4.5 s |
| Small (fp16) | OpenVINO | Arc GPU | 4.0 s | 0.4 s | 16.5 s first time |
| Small (fp16) | OpenVINO | CPU | 7.6 s | 1.7 s | 2.6 s |
| Small (int8) | faster-whisper | CPU | 12.8 s | 1.8 s | |
| Tiny (fp16) | OpenVINO | Arc GPU | 2.0 s | | 7.9 s |
| Tiny (fp16) | OpenVINO | CPU | 1.8 s | | 0.9 s |
| any | OpenVINO | NPU | fails ("roi_end <= max_dim") | | |

Chunked transcription (the way dictation works) matched one-pass transcription word for
word: 204 of 204 words, 8 chunks of about 0.4 s each on the Arc GPU.

AI polish with Ollama qwen2.5:7b, 100 words in: Clean up 6.9 s, Email 7.9 s, Bullet
points 6.2 s; about 2 s for a short sentence. The first request after loading takes
about 15 s longer, which LexiVox hides by warming the model up while you talk.

## Testing a new machine

1. Install with `scripts\install.ps1` and note which engines it chose.
2. Start LexiVox. `%LOCALAPPDATA%\LexiVox\logs\app.log` has a line like
   `ready: large-v3-turbo model on Intel(R) Arc(TM) 130V GPU (openvino)`.
3. **Settings > Engine** lists what was found; try each GPU and CPU entry.
4. Dictate the same 30-second paragraph with each; note the wait after the second
   shortcut press, and whether the text is right.
5. Transcribe the same file on the Files tab; the transcript header shows the time.
6. Try AI polish if Ollama is installed.
7. Add a row below.

**If a GPU misbehaves:** choose **CPU only** in Settings > Engine (or a CPU entry). A
GPU that fails during a job is skipped automatically until LexiVox restarts.

| Date | CPU | GPU / NPU | Driver | Engines installed | Engine used | Model | 30 s dictation wait | 80 s file | Pass/fail | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-27 | Core Ultra 5 226V | Arc 130V, AI Boost NPU | | cpu, intel | OpenVINO GPU | Large v3 Turbo | ~0.4 s | 2.1 s | Pass | NPU fails with OpenVINO 2026.4 |
| | | | | | | | | | | |
