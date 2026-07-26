# Offline Writing Reviser

Offline Writing Reviser is a local Windows background proofreader. Select text
in the foreground application and press `Ctrl+Alt+W`. Normal operation has no
taskbar window, console, tray icon, or routine notification.

Version 0.3.0 uses a conservative local hybrid pipeline:

1. bundled LanguageTool applies only deterministic SAFE spelling corrections;
2. the corrected text is checked again for unresolved evidence;
3. only justified contextual cases are sent to the local `gemma3:4b` model
   through Ollama;
4. broad rewrites, commentary, truncation, and formatting damage are rejected;
5. a rejected model answer falls back to the safe LanguageTool result.

Correct text is not sent to Gemma when LanguageTool provides no meaningful
evidence. Selected or revised text is never written to the log.

## Install

Run `OfflineWritingReviser-Setup.exe`. The compact online installer:

- installs the application, a private Java 17 runtime, and LanguageTool;
- reuses a compatible existing Ollama installation;
- offers to download the official Ollama Windows installer when Ollama is
  absent;
- checks for `gemma3:4b` and, with explicit consent, shows progress while
  Ollama downloads it;
- validates Java, LanguageTool, Ollama, and a small proofreading request;
- starts the hidden background application.

The application does not require Python, system Java, PATH configuration, or a
manual LanguageTool service. Model provisioning requires internet access and
several gigabytes of free disk space. Ollama and its model store are shared
user resources and are deliberately preserved when Offline Writing Reviser is
uninstalled.

The per-user installation does not require administrator rights and defaults
to:

```text
%LOCALAPPDATA%\Programs\Offline Writing Reviser
```

Application settings and logs are under:

```text
%LOCALAPPDATA%\OfflineWritingReviser
```

## Use and process control

Launch `OfflineWritingReviser.exe`, select editable text, and press
`Ctrl+Alt+W`. Only one background instance runs per Windows session.

```powershell
OfflineWritingReviser.exe --settings
OfflineWritingReviser.exe --diagnostics
OfflineWritingReviser.exe --diagnostics --gemma-test
OfflineWritingReviser.exe --exit
OfflineWritingReviser.exe --restart
OfflineWritingReviser.exe --version
OfflineWritingReviser.exe --validate-startup
```

Settings requested while the background process is absent start it first.
Closing Settings leaves proofreading active. Exit and restart use a hidden
local Win32 control window and cleanly unregister the hotkey and stop the owned
LanguageTool process.

`--diagnostics` reports application configuration, bundled runtime health,
Ollama/API/model state, loaded-model RAM/VRAM values exposed by Ollama, and a
deterministic LanguageTool health test. Add `--gemma-test` for a small,
potentially slow end-to-end inference test. The report never includes
clipboard or document text.

## Hardware behavior

The application does not choose or force a CPU/GPU backend. Ollama selects its
supported backend, so one installed application works on CPU-only machines and
automatically benefits from supported GPU acceleration. Diagnostics classify a
loaded model as CPU, GPU, partial GPU, or unknown from Ollama's reported model
and VRAM allocation. Current Ollama APIs do not reliably expose an exact GPU
vendor/device/backend in every version, so the application reports that field
as unknown rather than guessing.

Gemma requests use a ten-minute keep-alive. This avoids repeated cold model
loads during an editing session, at the cost of temporary system RAM or VRAM
residency. Ollama remains responsible for memory management and hardware
fallback.

## Settings

Defaults:

- Model: `gemma3:4b`
- Revision timeout: 45 seconds
- Maximum input length: 20,000 characters
- Hotkey: `Ctrl+Alt+W`

Settings use Qt Widgets and the Windows UI Automation accessibility bridge.
Controls have accessible names, label associations, standard keyboard
behavior, and an explicit focus order. Final NVDA listening validation should
still be repeated on each target Windows/NVDA release.

Environment overrides for managed diagnostics:

- `OWR_OLLAMA_EXECUTABLE`
- `OWR_MODEL`
- `OWR_TIMEOUT_SECONDS`
- `OWR_MAX_CHARACTERS`
- `OWR_CHUNK_CHARACTERS`
- `OWR_HOTKEY`
- `OWR_LOG_FILE`

## Running and testing from source

The repository intentionally does not contain vendor binaries. Place pinned
dependencies at:

```text
vendor\java\bin\java.exe
vendor\languagetool\languagetool-server.jar
```

Then:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,build]"
python -m pytest
python -m offline_writing_reviser
```

## Build

Build the private-runtime application directory:

```powershell
.\scripts\build.ps1
```

Build the signed-ready installer and checksum (Inno Setup 6 required):

```powershell
.\scripts\build-installer.ps1
```

Artifacts:

```text
dist\OfflineWritingReviser\OfflineWritingReviser.exe
dist\installer\OfflineWritingReviser-Setup.exe
dist\installer\OfflineWritingReviser-Setup.exe.sha256
```

Use `-SkipTests` or `-SkipApplicationBuild` only after the same source state has
already passed those stages. See [release build and installation](docs/installer.md)
and [architecture](docs/architecture.md).

## Troubleshooting

- Run `OfflineWritingReviser.exe --diagnostics`.
- Logs are at
  `%LOCALAPPDATA%\OfflineWritingReviser\logs\writing-reviser.log`.
- If Ollama or the model is absent, rerun setup or use
  `OfflineWritingReviser.exe --provision-model`.
- If the hotkey is unavailable, choose another Ctrl/Alt plus letter or number
  combination in Settings.
- If focus changes while inference is running, replacement is deliberately
  cancelled.
- Windows may prevent a non-elevated app from interacting with an elevated
  editor.

Performance logs contain character counts, routing decisions, LanguageTool and
Gemma timing, model telemetry, and validation outcomes, but not user text.

## Limitations

- Windows only; rich clipboard formatting is not preserved.
- The standard installer is online for Ollama/model provisioning.
- GPU acceleration cannot be validated on a CPU-only build machine.
- Ollama's API may expose VRAM allocation without an exact backend/device name.
- CPU-only Gemma inference can take several seconds; deterministic
  LanguageTool-only corrections remain fast.
- Installer automation cannot replace a final NVDA listening pass or a
  representative clean-machine hotkey test.
