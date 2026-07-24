# Architecture

Offline Writing Reviser 0.2.0 keeps the proven revision pipeline isolated from a
small Windows desktop shell.

## Boundaries

### Entry point and application lifecycle

`offline_writing_reviser.__main__` owns argument parsing for normal tray startup,
`--version`, and `--validate-startup`.

`offline_writing_reviser.application` composes the process:

1. Load and validate per-user settings.
2. Acquire the Windows single-instance mutex.
3. Configure per-user file logging.
4. Create the Ollama service, revision controller, and hotkey thread.
5. Start the tray and hidden settings UI host.
6. Coordinate status, notifications, settings changes, restart, and exit.
7. Stop the tray, close settings, unregister the hotkey, release the mutex, and
   optionally launch a replacement process.

The application continues into the tray when Ollama, the configured model, or
the preferred hotkey is unavailable so the user can inspect status and change
settings. Startup validation does not register a hotkey or contact a model.

### Core revision engine

`offline_writing_reviser.core` owns the provider-neutral revision contract:

- tightly scoped revision prompt
- input validation and maximum-length enforcement
- concurrent revision guard
- output sanitization
- provider-neutral results and errors

This layer has no tray, Tkinter, Windows, Ollama, network, or persistence
dependency.

### Local Ollama provider

`offline_writing_reviser.providers.ollama` is the only model integration. It:

- resolves the configured local `ollama` executable
- discovers local models through `ollama list`
- verifies the configured model before revision
- invokes `ollama run <model> <prompt>`
- maps missing executable, missing model, timeout, and process failures
- never downloads a model and has no cloud fallback

### Settings and paths

`offline_writing_reviser.settings` validates the four user-editable values and
performs atomic JSON writes using a sibling temporary file and `os.replace`.
Invalid JSON or values are preserved as `settings.json.corrupt` before defaults
are restored. Environment variables can override the loaded values for managed
use.

`offline_writing_reviser.paths` centralizes `%LOCALAPPDATA%` and PyInstaller
resource resolution. Configuration and logs never live beside the executable.

### Desktop shell

`offline_writing_reviser.tray` is a narrow `pystray` adapter. It displays the
current state and delegates menu actions to the application coordinator.

`offline_writing_reviser.settings_ui` uses standard Tk/ttk controls. Tk owns a
dedicated UI thread and receives cross-thread requests through a queue. Model
discovery runs off the UI thread.

`offline_writing_reviser.desktop_status` defines the state enum and maps
internal exception types to short user-facing messages. Detailed exceptions
remain in file logs; stack traces are not placed in notifications.

### Windows integration

`offline_writing_reviser.windows` contains hotkey and selected-text behavior.
The text adapter retains the baseline safety contract:

- wait for hotkey modifiers to be physically released
- snapshot and restore clipboard formats where practical
- capture only selected foreground text
- retain foreground window/process identity
- abort replacement if focus assumptions change
- serialize overlapping controller triggers

Hotkey changes use a two-manager handoff: register the candidate shortcut first,
then unregister the old shortcut only after success. A failed candidate leaves
the old manager running.

## State flow

The desktop status is one of Ready, Revising, Ollama unavailable, Model
unavailable, Hotkey unavailable, or Error. Revision start sets Revising. A
successful replacement returns to Ready without a notification. Expected
failures map to one notification and a meaningful state. A later successful
availability check or revision restores Ready.

## Packaging

`scripts/build.ps1` runs tests and PyInstaller in one-file/windowed mode. The
generated executable embeds the original icon and `pystray`/Pillow runtime while
leaving Ollama external. Bundled resources resolve through PyInstaller's
temporary extraction root; source execution resolves them relative to the
package.
