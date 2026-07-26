# Architecture

Offline Writing Reviser 0.2.0 keeps the proven revision pipeline isolated from
a completely hidden Windows background lifecycle.

## Boundaries

### Entry point and lifecycle

`offline_writing_reviser.__main__` owns argument parsing for:

- hidden background startup
- `--settings`
- `--exit`
- `--restart`
- `--version`
- `--validate-startup`

`offline_writing_reviser.application` composes the background process:

1. Load and validate per-user settings.
2. Acquire the Windows single-instance mutex.
3. Configure per-user file logging.
4. Create the Ollama service, revision controller, and hotkey thread.
5. Start a withdrawn Tk settings host and hidden Win32 control endpoint.
6. Wait without exposing a console, taskbar window, notification-area icon, or
   persistent notification.
7. Stop the control endpoint, close Settings, unregister the hotkey, release the
   mutex, and optionally launch a replacement process.

Normal startup has no visible desktop shell. The configured global hotkey is the
primary interaction.

### Instance control

`offline_writing_reviser.windows.control` owns a zero-size, never-shown Win32
tool window with a fixed class and title. It exists only as a same-user IPC
target and does not appear in the taskbar or notification area.

Short-lived command processes locate that control window and post one of three
private `WM_APP` messages:

- show Settings
- request clean Exit
- request clean Restart

If `--settings` is used while no instance exists, the command launches a
detached background process, waits for its control endpoint, and posts the
Settings message. `--restart` starts the app when it is not already running.
`--exit` is idempotent.

The single-instance mutex remains authoritative for preventing duplicate
background runtimes.

### Core revision engine

`offline_writing_reviser.core` owns the provider-neutral revision contract:

- tightly scoped revision prompt
- input validation and maximum-length enforcement
- concurrent revision guard
- sequential paragraph/sentence/word-aware chunking for long selections
- output sanitization
- conservative typography, line-break, paragraph, list, commentary, and
  minimal-edit validation before replacement
- provider-neutral results and errors

Each chunk is a contiguous slice of the original selection and is validated
independently. The service joins only fully validated chunks, validates the
complete result again, and returns nothing if any chunk fails. The Windows
controller therefore cannot partially replace a selection.

Chunk processing is intentionally sequential to keep memory use bounded and
failure behavior predictable. Chunking improves reliability and the safe
processing ceiling; it does not materially solve large-text latency. CPU-only
Gemma inference remains the dominant bottleneck, very large selections may
take minutes, and model-based proofreading can still miss corrections.

This layer has no Tkinter, Windows control, Ollama, network, or persistence
dependency.

### Local Ollama provider

`offline_writing_reviser.providers.ollama` is the only model integration. It:

- resolves the configured local `ollama` executable
- discovers local models through `ollama list`
- verifies the configured model before revision
- invokes the loopback-only Ollama chat API with deterministic proofreading
  settings after confirming the configured model through `ollama list`
- requests a ten-minute keep-alive so nearby jobs can reuse one loaded model
- maps missing executable, missing model, timeout, and process failures
- never downloads a model and has no cloud fallback

### Settings and paths

`offline_writing_reviser.settings` validates the four user-editable values and
performs atomic JSON writes using a sibling temporary file and `os.replace`.
Invalid JSON or values are preserved as `settings.json.corrupt` before defaults
are restored. Environment variables can override loaded values.

`offline_writing_reviser.paths` centralizes `%LOCALAPPDATA%` and PyInstaller
resource resolution. Configuration and logs never live beside the executable.

`offline_writing_reviser.settings_ui` is the only Qt boundary. It uses standard
Qt Widgets and their Windows UI Automation bridge, explicit accessible names,
`QLabel.setBuddy` programmatic label associations, standard roles/value
patterns, and an explicit focus order. A dispatch bridge accepts cross-thread
requests while the Qt dispatcher owns the process main thread and model
discovery remains off the UI thread. Closing Settings destroys only the dialog;
the hidden background reviser remains alive.

Tk/ttk was removed from this boundary because live UI Automation inspection
exposed its children as anonymous, non-focusable `TkChild` panes. Validation
errors focus the affected control before an accessible modal error dialog is
shown, and focus returns to that control after dismissal.

### Status and errors

`offline_writing_reviser.desktop_status` defines Ready, Revising, Ollama
unavailable, Model unavailable, Hotkey unavailable, and Error. States are
logged and shown contextually inside Settings rather than through a persistent
desktop surface.

Successful revisions produce no UI. Errors requiring direct intervention use
short dialogs dispatched by the settings UI host. Repeated dialogs with the
same title are rate-limited; detailed exceptions remain in the file log.

### Windows text integration

`offline_writing_reviser.windows` retains the baseline hotkey and selected-text
safety contract:

- wait for hotkey modifiers to be physically released
- snapshot and restore clipboard formats where practical
- capture only selected foreground text
- retain foreground window/process identity
- abort replacement if focus assumptions change
- serialize overlapping controller triggers

Hotkey changes use a two-manager handoff: register the candidate shortcut first,
then unregister the old shortcut only after success. A failed candidate leaves
the old manager running.

## Packaging

`scripts/build.ps1` runs tests and PyInstaller in one-file console-subsystem
mode. A normal Explorer launch hides only its private console; diagnostic and
control commands can still report through an invoking terminal. The executable
embeds its Python runtime, the minimal Qt Widgets runtime, and the original icon
while leaving Ollama external.

`pystray` and Pillow are not runtime dependencies. Pillow is build-only and is
used to generate the original executable icon.
