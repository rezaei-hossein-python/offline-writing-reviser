# User installation guide

## Fresh install

1. Download `OfflineWritingReviser-Setup.exe` and the published checksum.
2. In PowerShell, run `Get-FileHash .\OfflineWritingReviser-Setup.exe -Algorithm SHA256` and compare all 64 hexadecimal characters with the release notes.
3. Run the installer. It installs per user under `%LOCALAPPDATA%\Programs\Offline Writing Reviser`; administrator rights should not normally be required.
4. Model Setup opens automatically after the core install finishes.
5. Setup reuses a compatible Ollama installation or, with consent, downloads and installs Ollama.
6. Setup reuses `gemma3:4b` or downloads it through Ollama, verifies the model list, and runs a minimal inference.
7. Choose **Hide** or close the active setup window to let it continue. Reopen **Start > Offline Writing Reviser > Set up intelligent revision** to reconnect to the same job and focus its window.
8. The progress, retry state, and Ready state persist. Duplicate launches do not start a second pull.
9. After Ready is verified, select text in Notepad or Microsoft Word and press `Ctrl+Alt+P`.

The model is several gigabytes and is not bundled in the installer. Allow approximately 5 GB of free space for Ollama, the model, and headroom. Setup time depends on internet speed. First inference may be slower while the model loads. CPU-only machines work but are slower; Ollama may use a supported GPU automatically.

## Upgrade, reinstall, and uninstall

Run the newer installer over the existing per-user installation. Setup stops the old background process, replaces application files, preserves settings and logs, restores the HKCU startup entry, and reuses Ollama and any installed model. No manual Ollama command is required.

Uninstall from Windows Installed apps or the Start-menu uninstall shortcut. It stops Offline Writing Reviser and removes program files, shortcuts, and the startup entry. Ollama, its model files, and `%LOCALAPPDATA%\OfflineWritingReviser` remain because they may be shared or useful for reinstall. Remove them separately only if you intentionally want their data gone.

## Setup troubleshooting

| Symptom | Resolution |
|---|---|
| Ollama is installed but the model is missing | Open **Set up intelligent revision**. Setup skips Ollama installation and pulls only `gemma3:4b`. |
| Setup window is hidden or disappeared | Open the same Start-menu shortcut. It reconnects to and focuses the active job. |
| Download was interrupted | Reopen setup and choose **Retry**. Completed Ollama layers or partial installer data are reused where supported. |
| Setup still says in progress | Leave it running; the multi-GB download may take time. Reopen setup for current bytes and percentage. Do not start another pull. |
| AI model not ready | Finish Model Setup. Ready appears only after Ollama, model-list verification, and minimal inference all pass. |
| No text selected | Select editable text in the active application before pressing `Ctrl+Alt+P`. |
| Selection could not be captured | Keep the target active, release Ctrl/Alt/P, wait briefly, and retry. Close clipboard managers temporarily if needed. |
| Revision timed out | Retry once the model is warm or raise the timeout in Settings. Timed-out document sections remain unchanged. |
| A long document is slow | Leave the source window available and wait for section announcements. About 2,000 words can take several minutes on slower hardware. |
| SmartScreen or antivirus warns | The installer is currently unsigned. Verify the SHA-256 against the release notes and proceed only if it matches the trusted download. Submit false positives to the antivirus vendor. |
| Application is already running | This is expected: one hidden background instance is allowed. Open Settings or use `--restart` instead of launching duplicates. |
| Startup entry is missing | Reinstall to recreate `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\OfflineWritingReviser`, or start the app from the Start menu for the current session. |

- Logs: `%LOCALAPPDATA%\OfflineWritingReviser\logs\writing-reviser.log`
- Settings: `%LOCALAPPDATA%\OfflineWritingReviser\settings.json`
- Provisioning state: `%LOCALAPPDATA%\OfflineWritingReviser\provisioning\state.json`

For exact diagnostic commands and additional symptoms, see [troubleshooting](troubleshooting.md).
