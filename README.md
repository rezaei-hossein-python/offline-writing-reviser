# Offline Writing Reviser

Offline Writing Reviser is a local-only Windows tray utility that revises the
currently selected text in the foreground application. Select text and press
`Ctrl+Alt+W`, or choose **Revise selected text** from the tray menu. The app
copies the selection, asks an already-installed local Ollama model to revise it,
and safely pastes back only the revised text.

Successful revisions are intentionally silent. The app does not include a cloud
provider, cloud fallback, account, telemetry, analytics, or automatic model
downloads. Selected and revised document text is not written to the log.

## Prerequisites

- Windows 10 or Windows 11.
- [Ollama](https://ollama.com/) installed and running locally.
- At least one Ollama model installed manually. The default is `llama3.2:3b`.
- Python 3.11 or newer only when running from source or building.

Ollama remains an external dependency of the packaged executable. The app never
runs `ollama pull`; model installation is always an explicit user action:

```powershell
ollama list
ollama pull llama3.2:3b
```

## Running the utility

From source:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,build]"
python -m offline_writing_reviser
```

Or start the packaged `OfflineWritingReviser.exe`. The utility starts in the
Windows notification area without leaving a main window open.

The tray menu provides:

- **Status** — current Ready, Revising, Ollama, model, hotkey, or error state.
- **Revise selected text** — the same action as the global hotkey.
- **Settings** — model, timeout, input limit, hotkey, and log location.
- **Open log folder** — opens the per-user log directory.
- **Restart** — cleanly unregisters the hotkey and starts a replacement process.
- **Exit** — unregisters the hotkey and terminates the app.

Double-clicking the tray icon opens Settings.

## Settings and local models

Settings are stored as readable JSON at:

```text
%LOCALAPPDATA%\OfflineWritingReviser\settings.json
```

Safe temporary-file replacement is used when saving. If the JSON is corrupt, it
is moved to `settings.json.corrupt` and defaults are restored. Settings contains
no credentials or secrets.

The Settings window lists models returned by the local `ollama list` command.
Use **Refresh models** after installing a model. A missing configured model is
clearly indicated and is never downloaded automatically. If a newly selected
hotkey cannot be registered, the previous working hotkey is preserved.

Defaults:

- Model: `llama3.2:3b`
- Revision timeout: 45 seconds
- Maximum input length: 4,000 characters
- Global hotkey: `Ctrl+Alt+W`

The settings UI uses native controls, follows a logical Tab order, shows native
focus indicators, supports Enter to save, and Escape to close.

Environment variables remain available for managed or diagnostic use:

- `OWR_OLLAMA_EXECUTABLE`
- `OWR_MODEL`
- `OWR_TIMEOUT_SECONDS`
- `OWR_MAX_CHARACTERS`
- `OWR_HOTKEY`
- `OWR_LOG_FILE`

Environment values override persisted settings for the current process.

## Command-line diagnostics

```powershell
python -m offline_writing_reviser --version
python -m offline_writing_reviser --validate-startup
```

The packaged equivalents are:

```powershell
.\OfflineWritingReviser.exe --version
.\OfflineWritingReviser.exe --validate-startup
```

Both commands exit without registering the hotkey or opening the tray.

## Build

Install the development and build dependencies, then run:

```powershell
python -m pip install -e ".[dev,build]"
.\scripts\build.ps1
```

The build script validates its cleanup paths, cleans `build` and `dist`, checks
the required build tools, runs the test suite, generates the original
application icon if necessary, and creates:

```text
dist\OfflineWritingReviser.exe
```

Use `-SkipTests` only when the same source state has already passed tests:

```powershell
.\scripts\build.ps1 -SkipTests
```

No Python installation is required to run the resulting executable.

## Troubleshooting

- **Ollama unavailable:** install/start Ollama and use **Refresh models**.
- **Model unavailable:** install it manually with Ollama or select another
  already-installed model.
- **Hotkey unavailable:** another application may own the shortcut. Choose a
  different `Ctrl`/`Alt` plus letter or number combination.
- **No text selected:** select editable text in the active application first.
- **Replacement cancelled:** focus changed while the local model was working;
  the app deliberately leaves the target and clipboard unchanged.
- **Timeout:** increase the timeout or choose a smaller local model.
- **Elevated application:** Windows may prevent a non-elevated utility from
  sending copy/paste input to an elevated target.

Detailed logs are stored at:

```text
%LOCALAPPDATA%\OfflineWritingReviser\logs\writing-reviser.log
```

The app logs startup/version, configured model, Ollama checks, hotkey lifecycle,
revision timing and character counts, safe replacement aborts, settings changes,
failures, and shutdown. It does not log full selected or revised text.

## Current limitations

- Windows only; no installer or Windows autostart registration yet.
- Ollama CLI is the only provider.
- Clipboard capture cannot support every custom or elevated editor.
- Replacement is plain Unicode text; rich formatting is not preserved.
- Notifications depend on Windows notification settings.
