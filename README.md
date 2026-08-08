# Offline Writing Reviser

Offline Writing Reviser is a Windows background utility that intelligently revises selected English text in place. Select text in an editable application, press `Ctrl+Alt+P`, and the local `gemma3:4b` model corrects spelling, grammar, punctuation, vocabulary, clarity, and naturalness. It may restructure sentences when doing so preserves the original meaning and intent.

The product has one production action and hotkey: Intelligent Revision on `Ctrl+Alt+P`. It was manually validated in Windows Notepad and Microsoft Word. Browser-editor support is not yet fully manually verified, so treat it as application-dependent.

## Why it is safe and private

All selected text is processed locally through Ollama after initial setup; the application does not send it to a cloud API. Ollama manages the model files. The prompt, output sanitizer, and deterministic semantic validator protect facts and identifiers including names, numbers and their roles, dates, times, amounts, URLs, email addresses, quoted text, negation, modality, and intent. Unsafe sections remain unchanged. These controls reduce risk but cannot mathematically guarantee perfect semantic equivalence; review sensitive revisions. The application is not a security boundary.

Selected and revised text are not logged by default. Logs contain operational metadata such as character and section counts, timing, process name, state, and failure codes.

## Requirements and disk space

- 64-bit Windows 10 or Windows 11.
- Internet access for first-time Ollama installation and `gemma3:4b` download; normal revision is offline afterward.
- Approximately 5 GB of free disk space is recommended for Ollama, the several-gigabyte model, and working headroom. The 31 MB installer does not bundle either one.
- CPU-only systems work but are slower. Ollama may use a supported GPU automatically.
- Python 3.11 or newer is required only for source development.

## Install and set up

1. Download `OfflineWritingReviser-Setup.exe` and its checksum from the release.
2. Verify it with `Get-FileHash .\OfflineWritingReviser-Setup.exe -Algorithm SHA256`.
3. Run the per-user installer. Administrator rights should not normally be required.
4. Model Setup opens automatically. It reuses or installs Ollama, reuses or downloads `gemma3:4b`, verifies the model list, and runs a minimal inference test.
5. You may hide Model Setup while it continues. Reopen **Start > Offline Writing Reviser > Set up intelligent revision** to reconnect to the same job. Progress and the Ready state persist.

The download takes several gigabytes and setup time depends on the connection. First inference can be slower while the model loads. See the [installation guide](docs/installation.md) for checksum details, upgrade/uninstall behavior, and setup troubleshooting.

## Use

1. Select text in Notepad or Microsoft Word.
2. Press `Ctrl+Alt+P` once.
3. Wait for the section progress announcements.
4. The complete revised result replaces the selection. Correct text may remain unchanged.

Long selections are processed sequentially in adaptive sections and reconstructed before replacement, so no partial or truncated document should be pasted. On slower hardware, about 2,000 words may take several minutes. A timed-out or unsafe section is preserved while later sections continue; a provider/model failure stops the operation.

For examples and all runtime states, see the [user guide](docs/user-guide.md).

## Settings, diagnostics, exit, and restart

Use the Start-menu **Settings** shortcut to select an installed Ollama model, set the request timeout and maximum input length, change the single global hotkey, locate logs, or restore defaults. The shipped and validated production binding is `Ctrl+Alt+P`. Closing Settings leaves the hidden background process running.

Installed commands (run from PowerShell):

```powershell
$app = "$env:LOCALAPPDATA\Programs\Offline Writing Reviser\OfflineWritingReviser.exe"
& $app --version
& $app --diagnostics
& $app --diagnostics-json
& $app --diagnostics --gemma-test
& $app --validate-startup
& $app --settings
& $app --provision-model
& $app --restart
& $app --exit
```

Settings are at `%LOCALAPPDATA%\OfflineWritingReviser\settings.json`, provisioning state at `%LOCALAPPDATA%\OfflineWritingReviser\provisioning\state.json`, and logs at `%LOCALAPPDATA%\OfflineWritingReviser\logs\writing-reviser.log`. There is no tray icon or persistent terminal; reopen controls from the Start menu. See [troubleshooting](docs/troubleshooting.md).

`--exit` cleanly stops the background process and unregisters the hotkey. `--restart` performs that shutdown and launches one replacement instance. The installer also registers per-user startup at sign-in.

## Uninstall

Use **Start > Offline Writing Reviser > Uninstall Offline Writing Reviser** or Windows Installed apps. Uninstall stops the background application and removes its program files, shortcuts, and startup entry. Shared Ollama, downloaded models, and per-user settings/logs remain unless removed separately, so reinstalling can reuse them.

## Accessibility and limitations

Settings and Model Setup provide keyboard navigation, labelled controls, logical focus, status text, and polite progress announcements through Windows UI Automation; they were designed and structurally validated for NVDA. No tray interaction or terminal is required. See [accessibility](docs/accessibility.md) for tested scope and limitations.

Known limitations include the unsigned installer (Windows SmartScreen or antivirus may warn), unverified full browser compatibility, hardware-dependent local inference speed, unchanged fallbacks when validation rejects output, and no mathematical guarantee of semantic equivalence.

## Developer quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,build]"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\prepare-languagetool-runtime.ps1
python -m offline_writing_reviser --validate-startup
python -m pytest -p no:cacheprovider --basetemp .pytest-temp\full
python -m compileall -q src tests
.\scripts\build-installer.ps1
```

The installer is created at `dist\installer\OfflineWritingReviser-Setup.exe` with a sibling `.sha256` file. Verify it with:

```powershell
Get-FileHash .\dist\installer\OfflineWritingReviser-Setup.exe -Algorithm SHA256
```

Architecture, exact build/test commands, and the cross-machine checklist are in the [architecture](docs/architecture.md), [developer guide](docs/development.md), and [installer guide](docs/installer.md). Release history is in [CHANGELOG.md](CHANGELOG.md); the v0.4.0 draft is in [docs/release-0.4.0.md](docs/release-0.4.0.md).
