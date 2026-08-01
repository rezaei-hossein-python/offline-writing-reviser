# Troubleshooting

| Symptom | Likely cause | Resolution |
|---|---|---|
| `Ctrl+Alt+P` does nothing | Hotkey conflict, app stopped, modifiers still held, or setup incomplete | Start Offline Writing Reviser, release the keys, check Model Setup, then run Diagnostics. Settings reports registration errors. |
| Selection copies but is not replaced | Target focus changed, paste failed, or complete output was unchanged/rejected | Keep the source window active until completion. Check the log failure code; retry in Notepad to isolate an unsupported editor. |
| Model output rejected; original preserved | A protected fact, identifier, meaning anchor, or structure changed | This is the safe fallback. Revise a smaller selection or edit manually; do not disable review for sensitive text. |
| Revision timeout | A request exceeded the configured absolute deadline | Retry after the first model load, raise the timeout in Settings, or use a smaller selection. A section receives one retry before preservation. |
| Model missing / AI model not ready | `gemma3:4b` is absent or verification failed | Open **Set up intelligent revision** from the Start menu and finish through Ready. |
| Setup still downloading | The model is several gigabytes | Reopen Model Setup to view persistent byte/percentage progress and wait. Do not launch a separate pull. |
| Setup window disappeared | Active setup was hidden | Reopen the Start-menu setup shortcut; it focuses the existing job. |
| Ollama unavailable | Ollama is missing, stopped, or its loopback API failed | Run Model Setup to install/start/repair it, then use Diagnostics. |
| Application already running | Single-instance protection rejected a duplicate | Use the existing background process; open Settings or run `--restart`. |
| Long text takes several minutes | Sequential local inference on slower hardware | Wait for progress announcements. Approximately 2,000 words may take several minutes; timing is not guaranteed. |
| App does not start at login | HKCU Run entry is missing/disabled | Enable **OfflineWritingReviser** in Startup Apps or reinstall to recreate `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\OfflineWritingReviser`. |
| Settings does not open | Background/control endpoint is stale | Run `--restart`, wait a few seconds, then reopen Settings. If it persists, inspect the log. |
| SmartScreen warning | Installer has no Authenticode signature | Compare SHA-256 with the release notes before choosing to run it. |
| Antivirus detection | Unsigned/new binary or false positive | Verify the hash and source; quarantine it if they differ. Submit a matching false positive to the vendor. |
| Clipboard contention | Another app changes or locks the clipboard | Pause clipboard history/sync/managers, keep the target active, and retry. The app avoids overwriting newer clipboard data. |
| Unsupported application | Nonstandard editor capture/paste behavior | Use a manually validated app (Notepad or Word). Browser support remains qualified. |

## Exact paths and commands

```powershell
$app = "$env:LOCALAPPDATA\Programs\Offline Writing Reviser\OfflineWritingReviser.exe"
& $app --version
& $app --validate-startup
& $app --diagnostics
& $app --diagnostics-json
& $app --diagnostics --gemma-test
& $app --settings
& $app --provision-model
& $app --restart
& $app --exit
```

- Log: `%LOCALAPPDATA%\OfflineWritingReviser\logs\writing-reviser.log`
- Settings: `%LOCALAPPDATA%\OfflineWritingReviser\settings.json`
- Provisioning state: `%LOCALAPPDATA%\OfflineWritingReviser\provisioning\state.json`
- Install directory: `%LOCALAPPDATA%\Programs\Offline Writing Reviser`
- Per-user Start menu: `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Offline Writing Reviser`

For a clean application uninstall, use the installed uninstaller; shared Ollama/models remain. To reset user settings, first run `& $app --exit`, rename or delete only `%LOCALAPPDATA%\OfflineWritingReviser\settings.json`, then start the application. Reinstall preserves and reuses Ollama/models. Removing Ollama or its model storage is a separate product operation and is intentionally outside this application's uninstaller.
