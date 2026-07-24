# Offline Writing Reviser

Offline Writing Reviser is a Windows background hotkey tool that rewrites only the currently selected text in the foreground application. Press `Ctrl+Alt+W`; the app copies the selection, sends it to a local Ollama model, and pastes back only the revised text.

Processing is completely local. There is no backend service, cloud provider, browser UI, database, account system, telemetry path, or automatic model download.

## Requirements

- Windows.
- Python 3.11 or newer for development.
- Ollama installed locally and available as `ollama` on `PATH`, or configured with `OWR_OLLAMA_EXECUTABLE`.
- The initial model installed manually: `llama3.2:3b`.

Verify or install the model yourself:

```powershell
ollama list
ollama pull llama3.2:3b
ollama list
```

The application verifies that the configured model is already present. It does not run `ollama pull`.

## Development Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest
```

Run a startup validation that configures logging and verifies import/startup wiring without registering the global hotkey:

```powershell
python -m offline_writing_reviser --validate-startup
```

Run the background hotkey listener:

```powershell
python -m offline_writing_reviser
```

Select text in another Windows application and press `Ctrl+Alt+W`. Successful revisions are silent and replace only the selected text. Failures are logged but do not paste explanations or error messages into the target application.

## Configuration

Environment variables:

- `OWR_OLLAMA_EXECUTABLE`: Ollama executable path or command name. Default: `ollama`.
- `OWR_MODEL`: model name. Default: `llama3.2:3b`.
- `OWR_TIMEOUT_SECONDS`: revision timeout. Default: `45.0`.
- `OWR_MAX_CHARACTERS`: maximum selected-text length. Default: `4000`.
- `OWR_HOTKEY`: global hotkey. Default: `Ctrl+Alt+W`.
- `OWR_LOG_FILE`: log path. Default: `%LOCALAPPDATA%\OfflineWritingReviser\logs\writing-reviser.log`.

## Build

```powershell
.\scripts\build.ps1
```

The build script runs tests by default and then uses PyInstaller if installed.

## Current Limitations

- Windows only.
- No GUI, tray icon, installer, settings window, or startup registration.
- Clipboard-based capture works with many applications but cannot guarantee every custom or elevated editor.
- Replacement is plain Unicode text; rich formatting is not preserved.
- Only the Ollama CLI provider is implemented.
