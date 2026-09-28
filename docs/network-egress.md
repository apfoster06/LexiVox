# Network egress

Every outbound connection LexiVox and its scripts can make. Found by searching the code
for `urlopen`, `build_opener`, `Invoke-WebRequest`, `Invoke-RestMethod`, `winget`,
`ollama pull`, `http://`, `https://`, `socket`, `requests`, `httpx` and subprocess
calls. Line numbers are as of commit `187dbc7`.

## The app at runtime

| # | Where | Triggered by | Destination | Notes |
|---|---|---|---|---|
| 1 | `app/models/manager.py:209` | The user clicks **Download model** | `https://huggingface.co/<repo>/resolve/<pinned commit>/<file>`, which redirects to Hugging Face's CDN | The only outbound internet connection in the app. Pinned revision, SHA-256 checked, `User-Agent: lexivox` |
| 2 | `app/ollama.py:70` | AI polish recordings (including a warm-up when one starts); opening Settings (Ollama status) | `http://127.0.0.1:11434` (or another loopback URL) | Loopback enforced by `validate_base_url`; proxies bypassed. Stays on the PC |
| 3 | `launcher.py:78` | Opening the window | `http://127.0.0.1:<random port>` | The app's own server. Stays on the PC |
| 4 | `launcher.py:148` | Only if pywebview can't start (`--edge` fallback) | Microsoft Edge in app mode with an isolated profile and `--disable-sync` | Edge itself may contact Microsoft services (see below) |

Subprocesses that don't touch the network: `app/audio.py:53` and `:81` (ffprobe and
ffmpeg, limited to local files by `-protocol_whitelist file`), `app/winshell.py:101`
(PowerShell running `make-shortcut.ps1`).

## Scripts (run by a person, at install time)

| Script | Destination | When |
|---|---|---|
| `scripts/install.ps1` (`uv sync`) | pypi.org and files.pythonhosted.org (Python packages); GitHub (uv's Python 3.12 build) | Installing or updating |
| `scripts/install.ps1` | Only prints `winget` commands for uv and ffmpeg; it doesn't run them | |
| `scripts/install-ollama.ps1:94` | winget sources (Microsoft) and Ollama's release download | Only if Ollama is missing |
| `scripts/install-ollama.ps1:98` | `https://ollama.com/download/OllamaSetup.exe` | Only if Ollama is missing and winget isn't available; signature checked |
| `scripts/install-ollama.ps1:133` | Ollama's model registry (`ollama pull`) | Adding a chat model without `-ModelsFrom` |
| `scripts/install-ollama.ps1:61, 138, 148` | `http://127.0.0.1:11434` | Checking and testing the local server |
| `scripts/pin_models.py:54` | `https://huggingface.co` API and small files | Developers only, when re-pinning the model catalog |

## Third-party components' own traffic

These aren't LexiVox's connections, but they run on the same PC:

| Component | Traffic | Control |
|---|---|---|
| Ollama | Checks ollama.com for its own updates | Ollama's settings; LexiVox's requests to it stay local |
| Microsoft WebView2 runtime (the window) and Edge | Microsoft's update service; possibly reputation (SmartScreen) checks | Managed by Windows and IT policy. Not verified whether SmartScreen checks loopback pages |
| huggingface_hub (installed with faster-whisper) | None: `HF_HUB_OFFLINE=1` and `HF_HUB_DISABLE_TELEMETRY=1` are set at startup | `launcher.py:16-17` |
| openvino_telemetry (installed with OpenVINO) | None: blocked from loading | `launcher.py`, `test_telemetry_package_never_loads` |
| Windows clipboard sync | None for dictations: flagged `CanUploadToCloudClipboard = 0` | `app/dictation/clipboard.py` |

## Keeping it offline

- After models are installed, LexiVox needs no internet. Copy models between PCs as
  described in [Setup](setup-and-deployment.md#installing-models-without-internet-or-on-many-pcs).
- To enforce it, a Windows Firewall rule can block outbound traffic for
  `.venv\Scripts\python.exe` and `pythonw.exe` in the project folder (allow loopback).
  Model downloads from inside the app then stop working, by design.
