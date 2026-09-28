# Security and privacy

## Goals

1. **Nothing leaves the PC** unless the user starts it (a model download) or it goes to
   a local Ollama server on the same PC.
2. **The local web server can only be used by the LexiVox window**, not by websites
   open in a browser or other programs on the network.
3. **Small, pinned, verifiable dependencies and models.**

## What LexiVox stores

| Data | Where | How long |
|---|---|---|
| Settings, including custom vocabulary | `%LOCALAPPDATA%\LexiVox\config.json` | Until changed |
| Speech models | `%LOCALAPPDATA%\LexiVox\models` | Until deleted |
| Microphone audio | In memory while recording; each chunk briefly as a WAV in `tmp\` | Deleted as soon as it's transcribed; `tmp\` is emptied at every start |
| Uploaded files | `tmp\` under a random name | Deleted when the job ends |
| Dictated text | Memory (last 20) and the Windows clipboard | Until LexiVox quits; the clipboard follows Windows' rules |
| File transcripts | Memory | Until LexiVox quits (nothing is saved to disk yet) |
| Logs | `logs\` | 14 days; redacted; never contain dictated or transcribed text |

## Controls

### Local web server (`app/security.py`, `launcher.py`)

| Threat | Control |
|---|---|
| Another PC on the network connects | Binds to 127.0.0.1 only, on a random port |
| A website in a browser calls the local server (DNS rebinding) | Host header must be `127.0.0.1:<port>` or `localhost:<port>` (403 otherwise) |
| Another local program or website calls the API | Every route, including static files, needs a session cookie. It's obtained once by exchanging a random 32-byte launch token (compared in constant time); the cookie holds a different secret, so the launch URL is useless after first use |
| Cross-site form posts (CSRF) | Cookie is `SameSite=Strict` and `HttpOnly`; POST/DELETE also need the `X-Requested-With: LexiVox` header, which cross-site forms can't set |
| Script injection in the page | CSP `default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`; no inline scripts or styles; no CDNs; server text inserted with `textContent` only |
| Debugger remote code execution | Served by waitress, never the Flask debug server |
| Path traversal | Uploads are saved under server-generated names; model names must be in the catalog; catalog file paths can't contain `..` or be absolute; resolved paths are checked to stay inside the models folder |
| Oversized uploads | 4 GB limit, enforced by both Flask and waitress |
| Malicious media files | Checked with ffprobe before use; ffmpeg and ffprobe get argument lists (never a shell), `-protocol_whitelist file` and a `file:` prefix, so a playlist disguised as audio can't fetch URLs |

### Models and dependencies

- Every model file is pinned to a Hugging Face commit and SHA-256
  (`app/models/catalog.toml`); downloads are hashed while streaming and deleted on
  mismatch; offline copies are re-verified by Rescan.
- `uv.lock` pins every Python package and its hashes.
- `huggingface_hub` runs with `HF_HUB_OFFLINE=1` for the whole process (LexiVox's own
  downloader doesn't use it).
- OpenVINO ships an opt-in telemetry package (`openvino_telemetry`) used only by its
  model-conversion tools. LexiVox never runs those, and the launcher blocks the module
  from loading at all (tested by `test_telemetry_package_never_loads`).

### Ollama (`app/ollama.py`)

- The Ollama URL must be `http` on a loopback address; anything else is rejected in
  Settings (`ALLOW_NON_LOOPBACK = False` is the single switch if a LAN server is ever
  wanted).
- Requests bypass any system proxy.
- `scripts/install-ollama.ps1` only runs the Ollama installer if it's validly signed by
  Ollama, and warns if `OLLAMA_HOST` would expose Ollama to the network.

### Clipboard, paste and keyboard

- Dictated text is flagged `CanUploadToCloudClipboard = 0`, so Windows' "sync across
  devices" doesn't upload it. It can still appear in the local Win+V history.
- Auto-paste sends Ctrl+V only after all modifier keys are released, and never into
  LexiVox's own window.
- Modifier-only shortcuts (like Ctrl+Win) need a low-level keyboard hook, which sees
  every keystroke while it's installed. The code only tracks whether modifier keys are
  down and whether any other key was pressed; it stores and logs nothing. It's only
  installed when such a shortcut is configured. Normal shortcuts use `RegisterHotKey`
  and need no hook.

### Logging (`app/logging_setup.py`)

A redacting formatter applies to every log line, including tracebacks: the user's home
folder becomes `~`, file names become `<file>.<ext>`, and URL query strings are
dropped. Logs rotate at 5 MB and are deleted after 14 days. Dictated and transcribed
text is never logged.

## Known limits

| Limit | Notes |
|---|---|
| Other programs running as the same Windows user | Can read LexiVox's files, the clipboard and Ollama (which has no authentication, by design). Out of scope: such a program already has the user's access. |
| Launch token on a command line (Edge fallback only) | Other programs of the same user can see process command lines; the token is single-use and exchanged within a second. The normal pywebview window doesn't expose it this way. |
| Local clipboard history | Win+V keeps dictations locally if the user has it turned on. |
| Keyboard hook | Some security software flags low-level keyboard hooks. Use a normal shortcut (with a letter or F-key) to avoid it. |
| Dependency vulnerability audit | Not run yet (`pip-audit`). See [Development](development.md#status-against-the-original-spec). |
| WebView2 and Edge | Microsoft components with their own update and reputation services; see [Network egress](network-egress.md). |
