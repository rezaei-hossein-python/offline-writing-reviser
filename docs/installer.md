# Installer and clean-state verification

The Inno Setup installer is per-user, requires no elevation, supports paths
with spaces, writes a single HKCU startup entry, launches the hidden
application after setup, and starts the accessible model provisioner as an
independent post-install action.

Bundled application content:

- `OfflineWritingReviser.exe`;
- PyInstaller's Python and PySide6 runtime files;
- application icon;
- third-party notices.

Not bundled:

- Ollama;
- `gemma3:4b` or any other model;
- Java;
- LanguageTool;
- benchmark results, tests, build caches, or temporary downloads.

Build:

```powershell
.\scripts\build-installer.ps1
```

The script removes old `dist`, `build`, and temporary build output within the
repository, runs the full test suite with a workspace-local pytest temp
directory, builds a fresh windowed application, compiles the installer, and
writes a SHA-256 checksum.

Clean acceptance must verify:

1. no application process, startup entry, or install directory;
2. normal non-admin install completes without waiting for downloads;
3. hidden startup creates no console, tray, or taskbar window;
4. model-ready `Ctrl+Alt+P` works in real editors;
5. model-not-ready state is reported through accessible setup/error UI;
6. exit leaves no application-owned process;
7. uninstall removes files, shortcuts, and the startup entry while preserving
   shared Ollama and unrelated software.
