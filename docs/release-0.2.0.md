# Offline Writing Reviser 0.2.0

Release 0.2.0 turns the original background hotkey process into a standalone
Windows tray utility without changing its local-only revision contract.

## Highlights

- Windows tray lifecycle with status, revision, settings, logs, restart, and
  clean exit actions.
- Native keyboard-accessible settings for model, timeout, input limit, and
  global hotkey.
- Safe JSON persistence under `%LOCALAPPDATA%\OfflineWritingReviser`.
- Discovery and selection of already-installed Ollama models.
- User-facing notifications for actionable failures; successful revisions stay
  silent.
- Safe hotkey replacement that preserves the previous binding on failure.
- Version and non-interactive startup-validation commands.
- Original application/tray icon and a one-file PyInstaller build.
- Expanded logging that records operational metadata but not document contents.

## Privacy and dependencies

All model processing remains local through the Ollama CLI. There is no cloud
provider, model download, telemetry, analytics, login, or Personal AI Assistant
dependency. The packaged executable includes its Python runtime, `pystray`, and
Pillow; Ollama and local models remain separately installed prerequisites.

## Known limitations

This release does not include an installer, Windows autostart, rich-text
preservation, or guaranteed compatibility with elevated/custom editors.
