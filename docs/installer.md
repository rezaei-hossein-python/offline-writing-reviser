# Installer and clean-state verification

The Inno Setup 6 installer is a per-user, x64-compatible bootstrap. It normally needs no elevation, supports paths with spaces, installs to `%LOCALAPPDATA%\Programs\Offline Writing Reviser`, writes one quoted `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\OfflineWritingReviser` entry, starts the hidden application, and launches Model Setup as an independent post-install action.

Bundled content is the application executable, its Python/PySide6 runtime,
icon, third-party notices, Eclipse Temurin 17.0.20+8 x64 JRE, and LanguageTool
6.6 standalone distribution. The private Java and LanguageTool files are under
the application directory and require no user setup. Ollama, `gemma3:4b`, AI
models, benchmarks, and tests are not bundled. The core installer never blocks
on multi-gigabyte model provisioning.

```powershell
.\scripts\build-installer.ps1
```

The script safely cleans repository-local build outputs, runs the full tests, builds the windowed app, compiles `dist\installer\OfflineWritingReviser-Setup.exe`, and writes its `.sha256` sibling. See [development.md](development.md) for split build commands.

Clean-machine acceptance must verify:

1. 64-bit Windows per-user installation without an administrator prompt under normal policy.
2. Silent background startup: no console, tray, taskbar window, duplicate
   instance, duplicate LanguageTool server, or orphan worker/Java process.
3. Model Setup reuse/install of Ollama, resumable `gemma3:4b` pull, hide/reopen/focus, persistent Ready, and no duplicate pull.
4. Real `Ctrl+Alt+P` selection replacement in Notepad and Microsoft Word, including modifier release, clipboard restoration, unchanged output, failure fallback, and a long document.
5. Settings, diagnostics, version, startup validation, restart, exit, and login startup.
6. Uninstall stops the application-owned `javaw.exe`, removes private Java and
   LanguageTool files with the application, shortcuts, and startup registration,
   while preserving shared Ollama/models and user settings/logs.
7. Installer byte size and SHA-256 match the release notes. Record SmartScreen/signature status.

The current installer is unsigned. Do not describe browser, GPU, performance, or assistive-technology coverage as verified unless that exact acceptance pass was performed.
