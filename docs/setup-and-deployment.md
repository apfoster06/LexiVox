# Setup and deployment

How to install LexiVox on a Windows PC, which parts each kind of hardware needs, and how
to update and remove it.

## What a PC needs

| Requirement | Needed for | How to get it |
|---|---|---|
| Windows 10 or 11, 64-bit | Everything | |
| [uv](https://docs.astral.sh/uv/) | Installs Python 3.12 and the app's libraries | `winget install astral-sh.uv` |
| ffmpeg on PATH | Converting audio and video | `winget install Gyan.FFmpeg` |
| Microsoft Edge WebView2 runtime | The app window | Built into Windows 11 and up-to-date Windows 10 |
| Visual C++ runtime | The speech engines | Usually already installed |
| Microphone access for desktop apps | Dictation | Windows Settings > Privacy & security > Microphone |
| A current GPU driver | Using an Intel or NVIDIA GPU | Windows Update or the vendor's site |
| Ollama and a chat model (optional) | AI polish only | [`scripts\install-ollama.ps1`](#ai-polish-ollama) |
| About 2 to 4 GB of disk | App, libraries and a model or two | More for extra models and Ollama |

No admin rights are needed: everything installs per user.

## Installing

```powershell
git clone https://github.com/apfoster06/crowe_vibe_whisper.git LexiVox
cd LexiVox
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

`install.ps1` checks for uv and ffmpeg (and tells you the winget command if either is
missing), detects the hardware, installs the matching speech engines with
`uv sync --no-dev --extra ...`, and creates the Start menu shortcut.

| Hardware found | Engines installed (uv extras) | Notes |
|---|---|---|
| Any PC | `cpu`: faster-whisper | Fast int8 CPU transcription; always installed |
| Intel CPU or GPU | + `intel`: OpenVINO GenAI | Uses Arc and Iris Xe GPUs; also a fast CPU path |
| NVIDIA GPU | + `nvidia`: CUDA 12 cuBLAS and cuDNN 9 (~1.3 GB) | Never installed on a PC without an NVIDIA GPU |
| AMD GPU | CPU engines only | A Vulkan engine (whisper.cpp) is on the roadmap |

Options: `-Engines cpu,intel,nvidia` overrides detection, `-NoShortcut` skips the
shortcut, `-DryRun` shows the plan without installing.

The Intel NPU ("AI Boost") isn't used: OpenVINO 2026.4's Whisper pipeline fails on it.
Setting the environment variable `LEXIVOX_ENABLE_NPU=1` lists it anyway, for testing.

## Speech models

LexiVox needs at least one Whisper model for the engine it uses. In the app: **Settings >
Speech recognition**, pick a model, **Download model**. Downloads come from Hugging Face,
pinned to an exact version, and every file is checked against its SHA-256 fingerprint;
a mismatch is deleted.

| Model | faster-whisper (CPU/NVIDIA) | OpenVINO (Intel) | Use for |
|---|---|---|---|
| Tiny | 78 MB | 84 MB | Testing only |
| Base | 148 MB | 154 MB | Slow CPUs |
| Small | 486 MB | 493 MB | Fast CPUs (8+ cores) |
| Medium | 1531 MB | 784 MB (int8) | |
| Large v3 Turbo | 1622 MB | 828 MB (int8) | GPUs: best accuracy, and fast |

### Installing models without internet (or on many PCs)

Models live in `%LOCALAPPDATA%\LexiVox\models\<engine>\<model>\`. Copy those folders from a
PC that has them (USB drive, network share) to the same place on another PC, then click
**Rescan** in Settings (or restart LexiVox). Every file is verified against the pinned
SHA-256s in `app/models/catalog.toml`, so a damaged or altered copy is refused.

## AI polish (Ollama)

Only the AI polish shortcut needs Ollama; everything else works without it.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-ollama.ps1
```

The script installs Ollama (winget, or the official installer, which it refuses to run
unless it carries Ollama's valid signature), starts the server on 127.0.0.1, adds a
chat model chosen by memory, and runs a test request.

| RAM | Model chosen | Size |
|---|---|---|
| 16 GB or more | qwen2.5:7b | 4.7 GB |
| Less than 16 GB | llama3.2:3b | 2.0 GB |

Options: `-Model <name>`; `-ModelsFrom <folder>` copies models from another PC's
`%USERPROFILE%\.ollama\models` instead of downloading; `-SkipModel`;
`-AcceptAgreements` (winget prompts); `-DryRun`. Ollama itself still needs the internet
to install, and it checks online for its own updates.

## Starting LexiVox and shortcuts

| How | Notes |
|---|---|
| Start menu > LexiVox | Created by `install.ps1` (or by the app on first run) |
| `LexiVox.bat` in the project folder | Double-click; tells you to run `install.ps1` if it isn't installed yet |
| `scripts\run.ps1` | Same, from PowerShell |
| `scripts\create-shortcut.ps1 [-Desktop] [-NoPin]` | Start menu (and desktop) shortcut pointing at `LexiVox.bat`, started minimized so no console flashes; tries to pin to Start |

Windows 11 normally stops scripts from pinning to Start. If the script can't, pin it by
hand: Start > All apps > right-click LexiVox > Pin to Start.

All shortcuts carry the app ID `LexiVox.App`. That's what makes Windows label
notifications "LexiVox" with its icon instead of "Python". LexiVox also registers the
name and icon under `HKCU\Software\Classes\AppUserModelId\LexiVox.App`.

Don't start LexiVox twice: two copies compete for the shortcuts and the tray.

## Updating

```powershell
cd LexiVox
git pull
uv sync --no-dev --extra cpu --extra intel   # same extras as the original install
```

Settings and models are kept (they live outside the project folder). There's
no automatic update, on purpose.

## Where LexiVox keeps data

Everything is per user in `%LOCALAPPDATA%\LexiVox\` (override with the environment
variable `LEXIVOX_DATA_DIR`):

| Path | Contents |
|---|---|
| `config.json` | Settings (see the [user guide](user-guide.md#settings-reference)) |
| `models\` | Verified speech models, one folder per engine and model |
| `ov-cache\` | OpenVINO's compiled-model cache (makes GPU loads take under 1 s after the first) |
| `logs\` | Redacted logs, rotated at 5 MB, deleted after 14 days |
| `tmp\` | Uploads and recordings while they're processed; emptied at startup |
| `icon\` | The icon files used for the window and notifications |
| `edge-profile\` | Only if the Edge fallback window was used |

Data from the app's earlier name (`%LOCALAPPDATA%\CroweVibeWhisper`) is moved here
automatically on first launch.

## Uninstalling

1. Quit LexiVox from the tray.
2. Delete the project folder.
3. Delete `%LOCALAPPDATA%\LexiVox` (settings, models, logs).
4. Delete the shortcut: `%APPDATA%\Microsoft\Windows\Start Menu\Programs\LexiVox.lnk`
   (and the desktop one, if created).
5. Delete the registry key `HKCU\Software\Classes\AppUserModelId\LexiVox.App`.
6. Optional: uninstall Ollama (Settings > Apps) and delete `%USERPROFILE%\.ollama`.
