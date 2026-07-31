# Offline Writing Reviser

Offline Writing Reviser is a local Windows background application for
intelligent text revision. Select text in an editable application and press
`Ctrl+Alt+P`. The application corrects spelling and grammar, improves awkward
or non-native phrasing when clearly beneficial, preserves meaning and factual
details, and pastes the result back into the original selection.

The application has one production revision engine. `Ctrl+Alt+W`,
LanguageTool, and private Java runtimes are not part of the product.

## User workflow

1. Install the application per user.
2. Complete the accessible model setup when prompted. Existing compatible
   Ollama and `gemma3:4b` installations are reused.
3. Select text in Notepad, a browser editor, Office, or another normal Windows
   editor.
4. Press `Ctrl+Alt+P`.

Correct, clear, natural text is returned unchanged. A revision is rejected
when deterministic validation detects changes to protected values such as
numbers, currencies, dates, times, URLs, email addresses, identifiers, quoted
text, names, negation, or modality.

## Runtime behavior

- one hidden per-user background process;
- no terminal, taskbar window, tray icon, private Java process, or
  LanguageTool server;
- one configurable global hotkey, defaulting to `Ctrl+Alt+P`;
- foreground target captured synchronously when the hotkey fires;
- prior clipboard content restored without overwriting a newer external
  clipboard change;
- paragraph-aware chunking for selections up to 20,000 characters by default;
- local Ollama HTTP inference with automatic hardware acceleration selected by
  Ollama;
- metadata-only production logging (counts, timings, process name, state, and
  error category; never selected or revised text).

Only one application instance runs in a Windows session. Start-menu shortcuts
open Settings, retry model setup, restart/exit through the command line, or
uninstall the application. Closing Settings does not stop the background
process.

## Settings and control

```powershell
OfflineWritingReviser.exe --settings
OfflineWritingReviser.exe --diagnostics
OfflineWritingReviser.exe --diagnostics-json
OfflineWritingReviser.exe --diagnostics --gemma-test
OfflineWritingReviser.exe --provision-model
OfflineWritingReviser.exe --restart
OfflineWritingReviser.exe --exit
OfflineWritingReviser.exe --validate-startup
```

Settings are stored under
`%LOCALAPPDATA%\OfflineWritingReviser\settings.json`. Logs are stored under
`%LOCALAPPDATA%\OfflineWritingReviser\logs`.

## Development

```powershell
python -m pip install -e ".[dev,build]"
python -m pytest -p no:cacheprovider --basetemp .pytest-temp
python -m compileall -q src tests
.\scripts\build-installer.ps1
```

The installer is written to
`dist\installer\OfflineWritingReviser-Setup.exe`, with a sibling SHA-256 file.
The build is per-user and does not bundle a model, Ollama, Java, or
LanguageTool.
