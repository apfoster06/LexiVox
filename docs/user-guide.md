# LexiVox user guide

## Starting LexiVox

Open **LexiVox** from the Start menu (or double-click `LexiVox.bat` in the project
folder). The window opens on the **Dictation** tab.

Closing the window doesn't quit LexiVox: it keeps running in the system tray (the icon
by the clock) so your shortcuts keep working. To quit, right-click the tray icon and
choose **Quit**. The tray menu also has **Show window** and **Start dictation**.

The tray icon shows what LexiVox is doing:

| Icon | Meaning |
|---|---|
| Fuchsia tile | Ready |
| Ember (orange) tile | Recording |
| Ultraviolet tile | Transcribing |
| Volt (yellow-green) tile | Polishing with AI |

## Dictation

1. Click in any text box: an email, a document, a chat, a browser form.
2. Press your **dictate shortcut** once and talk. You hear a high beep.
3. Press the shortcut again. You hear a lower beep.
4. A moment later the text is pasted where your cursor is, and it's also on the
   clipboard, so you can paste it again elsewhere with Ctrl+V.

The shortcuts are shown on the Dictation tab and set in **Settings > Shortcuts**. You can
also click the big tile button on the Dictation tab to start and stop.

**While you talk**, LexiVox transcribes what you've said so far in the background (it
cuts the recording at natural pauses about every 10 seconds). The Dictation tab shows
the text as it arrives. So when you stop, only the last few seconds are left to do:
typically under half a second on an Intel Arc GPU, a second or two on a CPU.

**Modifier-only shortcuts** like Ctrl+Win fire when you let go of the keys, and only if
you pressed nothing else in between. That's why Windows' own shortcuts such as
Ctrl+Win+D (new desktop) still work normally.

**Where the text goes:** into whichever window has focus when transcription finishes.
LexiVox won't paste into its own window (the text just stays on the clipboard), and
Windows blocks pasting into programs running as administrator (press Ctrl+V yourself).

**Recent dictations** (the last 20, until you quit) are listed on the Dictation tab
with a **Copy** button. An **AI** tag marks polished ones.

### The recording pill

While you dictate in another program, a small pill shows "Recording 0:07" with a
microphone level bar. It never takes focus, so it can't steal your paste. Choose where
it appears in **Settings > Microphone & recording > Recording indicator**: above the
taskbar, the right edge (vertical), or off. It's hidden when the LexiVox window is in
front, since the window already shows the state.

## AI polish

The second shortcut (**Dictate + AI polish**) records the same way, then has a local AI
model tidy the text before pasting. Pick the style on the Dictation tab or in Settings:

| Style | What it does |
|---|---|
| Clean up | Fixes grammar, punctuation and obvious mistakes; keeps your wording |
| Email | Turns what you said into a short, professional email body |
| Bullet points | Turns it into concise bullets |

AI polish needs [Ollama](https://ollama.com) with a chat model installed (see
[Setup](setup-and-deployment.md#ai-polish-ollama)). Everything stays on your PC. If
Ollama isn't running, LexiVox pastes what you said as spoken and shows a notice.

Clean up runs on each chunk while you talk, so it's mostly done when you stop. Email and
Bullet points need the whole message, so they run at the end (typically 2 to 8 seconds).
The AI model starts loading as soon as you begin a polish recording.

## Transcribing files

On the **Files** tab, drop audio or video files onto the box (or click it to choose).
Files are processed one at a time; each shows its status and progress. Click a file to
see its transcript with timestamps. You can search it, hide the timestamps, and copy it.

Supported formats include MP3, WAV, M4A, AAC, FLAC, OGG, Opus, WMA, AIFF, AMR, MP4, MOV,
MKV, AVI, WMV, WebM and MPEG. The file is converted to 16 kHz audio and deleted after
it's transcribed.

## Settings reference

| Setting | Where | What it does | Default |
|---|---|---|---|
| Dictate shortcut | Shortcuts | Tick modifier keys, pick a key (or None for a modifier-only chord like Ctrl+Win), then Save | Ctrl+Alt+Space |
| Dictate + AI polish shortcut | Shortcuts | Same, for AI polish | Ctrl+Alt+Win |
| Microphone | Microphone & recording | Which mic to use; "Windows default" follows Windows | Windows default |
| Beep | Microphone & recording | Beep when recording starts and stops | On |
| Recording indicator | Microphone & recording | Pill above the taskbar, on the right edge, or off | Above the taskbar |
| Stop recording automatically after | Microphone & recording | Safety limit, 1 to 60 minutes | 15 |
| Paste automatically | Text (also on the Dictation tab) | Press Ctrl+V for you; the text is always copied either way | On |
| Remove filler words | Text (also on the Dictation tab) | Drops um, uh, erm, hmm; keeps "mm-hmm" and "uh-huh" (they mean yes) | On |
| Custom vocabulary | Text | Names and jargon, one per line, so Whisper spells them your way. Used for dictation and files. Click **Save vocabulary** | Empty |
| AI model | AI polish | Which Ollama model; Automatic prefers qwen2.5:7b, then qwen3.5:9b, then llama3.2:3b | Automatic |
| Style | AI polish | Clean up, Email or Bullet points | Clean up |
| Engine | Speech recognition | Automatic, CPU only, or a specific GPU/CPU engine found on this PC | Automatic |
| Model | Speech recognition | Whisper model size; Download it here if it isn't installed | Recommended for your hardware |
| Language | Speech recognition | Detect automatically, or pick one of about 100 languages | Detect automatically |
| Translate to English | Speech recognition | Output English whatever language is spoken | Off |
| Theme | Appearance | Match Windows, light or dark | Match Windows |

**Choosing a speech model:** bigger is more accurate and slower. The recommendation is
Large v3 Turbo with a GPU, Small on a fast CPU (8+ cores), Base on a slower one. Each
engine has its own copy of a model, so switching engine may ask you to download again.

## Troubleshooting

| Problem | What to do |
|---|---|
| "…is already used by another app" under Shortcuts | Another program owns that combination. Pick another. |
| The shortcut does nothing | Check LexiVox is running (tray icon). Two copies running at once will fight over shortcuts: quit one. |
| "No speech was heard" | Check the mic in Settings, and that Windows allows desktop apps to use it: Windows Settings > Privacy & security > Microphone. |
| "Recording was too short" | Speak for at least half a second after the start beep. |
| Text didn't paste | The target app may run as administrator, or focus moved. The text is on the clipboard: press Ctrl+V. |
| "Model … is not installed" | Settings > Speech recognition > Download model, or copy models from another PC and click Rescan (see Setup). |
| AI polish pasted plain text | Ollama isn't running or has no chat model. Open Ollama, or run `scripts\install-ollama.ps1`. |
| "GPU failed, finished on CPU" | A GPU driver problem. The job still finished. Update the graphics driver, or choose a CPU engine in Settings. The GPU is retried after restarting LexiVox. |
| Everything is slow | Check Settings > Engine shows your GPU. Choose a smaller model. |
| The notification icon is a blank page | Windows caches shortcut icons; it updates after signing out and in. |

Logs (with file names and your user folder removed) are in
`%LOCALAPPDATA%\LexiVox\logs`. Dictated text is never written to the logs.
