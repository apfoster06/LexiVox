# HTTP API

The window talks to the app's local server (`127.0.0.1:<random port>`). These endpoints
are internal to LexiVox, not a public API: every request needs the session cookie and
the right Host header, and POST/DELETE need `X-Requested-With: LexiVox` (see
[Security](security-and-privacy.md)). Errors return `{"error": "message"}` with a 4xx
status.

## Page

| Method | Path | Does |
|---|---|---|
| GET | `/?launch=<token>` | Exchanges the single-use launch token for the session cookie, then redirects to `/` |
| GET | `/` | The app page |

## State and settings (`app/routes/api.py`, `app/routes/settings_api.py`)

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/health` | | `{ok}` |
| POST | `/api/heartbeat` | | `{ok}`; keeps the Edge fallback alive |
| GET | `/api/state` | | Everything the page needs: `settings`, `choice` (engine in use), `choices` (all engines found), `effective_model`, `recommended_model`, `models` (install status per engine), `download`, `jobs`, `languages`, `dictation`, `hardware` |
| POST | `/api/settings` | Any of: `model`, `language`, `translate`, `theme`, `auto_paste`, `remove_fillers`, `vocabulary`, `mic_device`, `sounds`, `max_record_minutes`, `overlay_position`, `polish_style`, `ollama_url`, `ollama_model`, `engine` (`""` automatic, `"cpu"`, or a key like `"openvino\|GPU"`) | `{settings}`. Every value is validated; unknown keys are rejected |
| GET | `/api/microphones` | | `{microphones: [names], selected}` |
| GET | `/api/ollama/status` | | `{running, models, selected, default, styles}` or `{running: false, error}` |

## Dictation

| Method | Path | Body | Does |
|---|---|---|---|
| POST | `/api/dictation/toggle` | `{mode: "dictate" \| "polish"}` | Starts or stops recording (same as the shortcut) |
| POST | `/api/dictation/cancel` | | Discards the recording, or stops work in progress |
| POST | `/api/dictation/copy` | `{index}` | Copies a recent dictation (0 = newest) to the clipboard |
| POST | `/api/dictation/hotkey` | `{mode, hotkey}` (`""` turns it off) | Registers a shortcut; 409 with a reason if Windows refuses or it clashes |

## Files

| Method | Path | Body | Does |
|---|---|---|---|
| POST | `/api/jobs` | multipart `files[]`; optional `model`, `language`, `translate` (default: Settings) | Queues files; returns `{jobs, rejected}` (201, or 400 if nothing was accepted) |
| GET | `/api/jobs` | | Job summaries |
| GET | `/api/jobs/<id>` | | One job with its `segments` |
| POST | `/api/jobs/<id>/cancel` | | Cancels a queued or running job |
| DELETE | `/api/jobs/<id>` | | Removes a finished job from the list |

## Models

| Method | Path | Body | Does |
|---|---|---|---|
| GET | `/api/models` | | Catalog with install status, and the current download |
| POST | `/api/models/download` | `{backend, model}` | Starts a verified download (one at a time; 409 if busy) |
| POST | `/api/models/download/cancel` | | Cancels it |
| POST | `/api/models/rescan` | | Re-verifies every model folder on disk (offline installs) |

## Live events: `GET /api/events`

Server-Sent Events. Each event has an increasing `id`; after a reconnect the browser
sends `Last-Event-ID` and receives what it missed. A comment ping is sent every 15 s.

| Event | Data | When |
|---|---|---|
| `sync` | `{}` | On connect, or if the client fell too far behind: reload `/api/state` |
| `job` | Job summary | A file job changes status |
| `segment` | `{id, progress, segment: {start, end, text}}` | A new transcript line |
| `job_reset` | `{id}` | A job restarted on the CPU after a GPU failure |
| `job_removed` | `{id}` | A job was removed |
| `download` | `{backend, model, status, done, total, error}` | Download progress (at most twice a second) |
| `dictation` | `{state, mode, seconds, level, hotkey, polish_hotkey, hotkey_errors, error, history, partial}` | Dictation state changes, and each finished chunk (`partial` = text so far) |
| `dictation_level` | `{state, mode, seconds, level}` | Four times a second while recording |
