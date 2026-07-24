# Offline Writing Reviser 0.2.0

Release 0.2.0 turns the original hotkey process into a hardened, completely
hidden Windows background utility without changing its local-only revision
contract.

## Highlights

- Hidden background lifecycle with command-based Settings, restart, and clean
  exit controls.
- Screen-reader and keyboard-accessible Settings, exposed through the Windows
  UI Automation bridge with named controls, roles, values, states, and focus.
- Safe JSON persistence under `%LOCALAPPDATA%\OfflineWritingReviser`.
- Discovery and selection of already-installed Ollama models.
- Rate-limited dialogs for actionable failures; successful revisions stay
  silent and no notification-center integration is used.
- Safe hotkey replacement that preserves the previous binding on failure.
- Version and non-interactive startup-validation commands.
- Original executable icon and a one-file PyInstaller build.
- Expanded logging that records operational metadata but not document contents.

## Privacy and dependencies

All model processing remains local through the Ollama CLI. There is no cloud
provider, model download, telemetry, analytics, login, or Personal AI Assistant
dependency. The packaged executable includes its Python runtime; Ollama and
local models remain separately installed prerequisites. There is no runtime
tray dependency.

PySide6 is the single desktop UI dependency. It is isolated to Settings; the
revision engine and hidden background lifecycle do not depend on UI concerns.

## Known limitations

This release does not include an installer, Windows autostart, rich-text
preservation, or guaranteed compatibility with elevated/custom editors.
