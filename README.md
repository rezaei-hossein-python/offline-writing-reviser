# Offline Writing Reviser

Offline Writing Reviser is a local-only Windows background utility that proofreads
the currently selected text in the foreground application. Select text and
press `Ctrl+Alt+W`. The app copies the selection, asks an already-installed
local Ollama model to correct objective spelling and grammar errors, and safely
pastes back only when the text changed.

Before replacement, the app conservatively verifies that typography, line
breaks, blank lines, paragraphs, and list structure were preserved. Commentary,
truncated or expanded rewrites, and structurally damaged output are rejected,
leaving the selected text untouched.

Normal operation is completely hidden: there is no taskbar window, console,
system-tray icon, notification-area menu, or persistent notification-center
presence. Successful revisions are silent. The app has no cloud provider,
cloud fallback, account, telemetry, analytics, or automatic model downloads.
Selected and revised document text is not written to the log.

## Prerequisites

- Windows 10 or Windows 11.
- [Ollama](https://ollama.com/) installed and running locally.
- At least one Ollama model installed manually. The default is `gemma3:4b`.
- Python 3.11 or newer only when running from source or building.

Ollama remains an external dependency of the packaged executable. The app never
runs `ollama pull`; model installation is always an explicit user action:

```powershell
ollama list
ollama pull gemma3:4b
```

## Running the utility

From source:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,build]"
python -m offline_writing_reviser
```

Or launch `OfflineWritingReviser.exe`. Normal startup registers the configured
global hotkey and remains hidden in the background. Select editable text in
another application and press `Ctrl+Alt+W`.

Only one background instance runs per Windows session. Starting the executable
again does not create a duplicate.

## Settings and process control

Open Settings through the running background process:

```powershell
python -m offline_writing_reviser --settings
.\OfflineWritingReviser.exe --settings
```

If Settings is requested while the utility is not running, the command starts
the hidden background process and then opens Settings. Closing Settings leaves
the reviser running.

Request a clean shutdown or restart:

```powershell
python -m offline_writing_reviser --exit
python -m offline_writing_reviser --restart

.\OfflineWritingReviser.exe --exit
.\OfflineWritingReviser.exe --restart
```

These commands communicate with the existing process through a hidden local
Win32 control window. They do not create another background reviser. Restart
unregisters the hotkey, closes Settings if open, releases the single-instance
mutex, and then launches a replacement process.

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

- Model: `gemma3:4b`
- Revision timeout: 45 seconds
- Maximum input length: 20,000 characters
- Global hotkey: `Ctrl+Alt+W`

The Settings window uses Qt Widgets with the Windows UI Automation
accessibility bridge. Every field has an explicit accessible name and
programmatically associated label. Tab and Shift+Tab follow the documented
control order; combo-box and spin-box arrow keys retain their standard
behavior; Enter activates Save and Escape activates Cancel. Validation moves
focus to the affected field and presents the error in a screen-reader-exposed
dialog.

Environment variables remain available for managed or diagnostic use:

- `OWR_OLLAMA_EXECUTABLE`
- `OWR_MODEL`
- `OWR_TIMEOUT_SECONDS`
- `OWR_MAX_CHARACTERS`
- `OWR_CHUNK_CHARACTERS` (internal chunk target; defaults to 2,000)
- `OWR_HOTKEY`
- `OWR_LOG_FILE`

Environment values override persisted settings for the current process.

## Command-line diagnostics

```powershell
python -m offline_writing_reviser --version
python -m offline_writing_reviser --validate-startup

.\OfflineWritingReviser.exe --version
.\OfflineWritingReviser.exe --validate-startup
```

Diagnostic commands exit without registering the hotkey or starting a
background instance.

## Build

Install development and build dependencies, then run:

```powershell
python -m pip install -e ".[dev,build]"
.\scripts\build.ps1
```

The build script validates cleanup paths, cleans `build` and `dist`, checks the
required build tools, runs tests, generates the original application icon if
needed, and creates:

```text
dist\OfflineWritingReviser.exe
```

Use `-SkipTests` only when the same source state has already passed tests:

```powershell
.\scripts\build.ps1 -SkipTests
```

No Python installation is required to run the resulting executable.

## Troubleshooting

- **Settings:** run `OfflineWritingReviser.exe --settings`.
- **Ollama unavailable:** install or start Ollama, then refresh models.
- **Model unavailable:** install it manually with Ollama or select another
  already-installed model.
- **Hotkey unavailable:** choose a different `Ctrl`/`Alt` plus letter or number
  combination in Settings.
- **No text selected:** select editable text in the active application first.
- **Replacement cancelled:** focus changed while the model was working, so the
  app deliberately left the target unchanged.
- **Timeout:** increase the timeout or choose a smaller local model.
- **Elevated application:** Windows may prevent a non-elevated utility from
  sending copy/paste input to an elevated target.
- **Stop a hidden instance:** run `OfflineWritingReviser.exe --exit`.

Errors requiring direct intervention may use a short Windows dialog. Repeated
dialogs are rate-limited. There are no routine success messages or
notification-center entries.

Detailed logs are stored at:

```text
%LOCALAPPDATA%\OfflineWritingReviser\logs\writing-reviser.log
```

The app logs startup/version, configured model, Ollama checks, hotkey lifecycle,
revision timing and character counts, safe replacement aborts, settings changes,
control commands, failures, restart, and shutdown. It does not log full selected
or revised text.

## Current limitations

- Windows only; no installer or Windows autostart registration.
- Ollama CLI is the only provider.
- Clipboard capture cannot support every custom or elevated editor.
- Replacement is plain Unicode text; rich formatting is not preserved.
- Local model output quality depends on the selected model.
- Large selections are split into sequential, boundary-aware chunks to improve
  reliability and the safe processing ceiling, not to provide a major speed
  improvement. CPU-only Gemma inference remains the dominant bottleneck, so
  very large selections may take several minutes.
- Proofreading is model-based and can still miss corrections, especially in
  long or highly repetitive text.
- Final NVDA acceptance should be repeated on the target Windows/NVDA version
  because automated UI Automation inspection cannot substitute for listening
  to an interactive screen-reader session.
