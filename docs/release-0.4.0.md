# Offline Writing Reviser v0.4.0 - draft release notes

These notes are final release-copy preparation only. No GitHub Release or tag has been created.

## Highlights

- One unified `Ctrl+Alt+P` intelligent revision action with meaning-preserving broad rewriting, grammar, spelling, punctuation, vocabulary, clarity, and naturalness improvements.
- Local `gemma3:4b` inference through Ollama; LanguageTool and Java dependencies are removed.
- Silent single-instance background operation with reliable foreground selection capture, modifier-release handling, focus verification, and clipboard preservation.
- Adaptive large-document revision with structure-aware sections, progress announcements, complete reconstruction, and no partial replacement.
- Automatic Ollama reuse/installation and `gemma3:4b` reuse/provisioning through an accessible Model Setup.
- Persistent setup phase, bytes, percentage, retry, failure, and Ready state; hiding and reopening reconnects to the same job without a duplicate pull.
- Cross-machine validation of `Ctrl+Alt+P` in Windows Notepad and Microsoft Word, including long-document revision.
- Clean per-user installer, startup, exit/restart, upgrade, and uninstaller lifecycle with no orphan application processes.

## Reliability and safety

The output sanitizer and deterministic validator protect numeric roles, facts, names, numbers, dates, times, amounts, URLs, emails, identifiers, quoted values, negation, modality, intent, and document structure. A rejected or malformed section remains unchanged. Each local-model request has a 45-second absolute deadline and one bounded retry; a repeatedly timed-out section is preserved while later sections continue. Provider/model failure stops the operation, and the application pastes only a complete reconstruction.

The capture/replacement state machine restores the user's clipboard without overwriting newer external clipboard data, cancels on target-focus drift, and guards against duplicate hotkey invocations. Shutdown unregisters the hotkey and stops application-owned workers cleanly.

## Installer and requirements

```text
Filename: OfflineWritingReviser-Setup.exe
Size: 32,382,737 bytes (30.88 MiB)
SHA-256: B6DA380442BF7C387BEB1F7EEC8329171F96E4B16E3A4ECDA2FC59291072F867
```

Requirements: 64-bit Windows 10 or Windows 11; internet access for first-time setup; approximately 5 GB free disk space recommended for Ollama, the several-gigabyte `gemma3:4b` model, and headroom. CPU-only inference works but is slower; Ollama may use supported GPU acceleration automatically. The installer is per-user and normally does not require administrator rights. It does not bundle Ollama or the model.

Install by downloading the installer, verifying the full SHA-256, running setup, and allowing Model Setup to reach Ready. The progress window can be hidden and reopened from **Start > Offline Writing Reviser > Set up intelligent revision**. After verification, select text in Notepad or Word and press `Ctrl+Alt+P`.

## Upgrade and uninstall

Run v0.4.0 setup over an earlier installation. Legacy default settings are migrated to `gemma3:4b` and `Ctrl+Alt+P`; existing compatible Ollama/model data are reused. Uninstall stops the background app and removes application files, shortcuts, and its HKCU startup entry. Shared Ollama, downloaded models, settings, and logs are intentionally preserved for other software or a reinstall.

## Known limitations

- The installer is not Authenticode-signed, so SmartScreen or antivirus may warn; verify the checksum before running it.
- Full browser-editor compatibility has not been manually verified. Current manual application validation covers Notepad and Microsoft Word.
- Inference speed is hardware-dependent. About 2,000 words may take several minutes on slower machines; benchmark timings are not guarantees.
- Some sections may remain unchanged when timeout, sanitizer, or semantic validation rejects a proposed change.
- Semantic validation reduces risk but cannot mathematically guarantee perfect equivalence. Review sensitive revisions.
- Accessibility contracts and NVDA-compatible UI behavior are tested structurally, but not every editor, assistive technology, Windows build, or hardware configuration has been manually covered.
