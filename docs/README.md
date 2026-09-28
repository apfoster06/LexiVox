# LexiVox documentation

LexiVox is a private, offline dictation and transcription app for Windows. You press a
shortcut, talk, and the text is pasted where your cursor is. Speech recognition runs
on the PC (Intel GPU, NVIDIA GPU or CPU); optional AI clean-up runs on a local Ollama
server. Nothing is sent to the cloud.

| Document | For | What's in it |
|---|---|---|
| [User guide](user-guide.md) | Everyone | Dictation, AI polish, files, every setting, the tray and recording pill, troubleshooting |
| [Setup and deployment](setup-and-deployment.md) | Whoever installs it | Installing on new PCs, hardware-specific engines, models, Ollama, shortcuts, updating, uninstalling |
| [Architecture](architecture.md) | Developers | How the pieces fit: processes and threads, request flow, engines, live chunking, data layout, file map |
| [Security and privacy](security-and-privacy.md) | Reviewers, IT | Threat model and every control, what's stored and logged, known limits |
| [Network egress](network-egress.md) | Reviewers, IT | Every outbound connection the app and its scripts can make, with file:line |
| [Hardware matrix](hardware-matrix.md) | Testers | Measured speeds, and a table to fill in for each machine |
| [HTTP API](api.md) | Developers | The app's internal endpoints and live events |
| [Development](development.md) | Developers | Dev setup, tests, how to extend, decision log, status against the original spec, roadmap |

The project [README](../README.md) is the short version.
