# Architecture

Offline Writing Reviser 0.4 has one canonical action: Intelligent Revision on
`Ctrl+Alt+P`.

## Runtime flow

The windowed executable acquires a per-session mutex, configures metadata-only
logging, starts a hidden Qt dispatcher for accessible Settings and error
dialogs, starts a hidden Win32 control endpoint, and registers one global
hotkey. There is no tray icon or taskbar window.

When the hotkey fires, the foreground and focused window handles are captured
synchronously. A worker waits for Ctrl, Alt, and P to be physically released,
snapshots the clipboard, sends a standard-control `WM_COPY` or a scan-code
Ctrl+C sequence, and waits for the clipboard sequence number to change. Empty
standard-edit selections are distinguished from copy timeout and clipboard
contention.

The clipboard is restored immediately after capture. Before replacement, the
original target is restored and verified. The adapter snapshots the current
clipboard again, writes the revision, sends `WM_PASTE` or Ctrl+V, and restores
that fresh snapshot only if no other application changed the clipboard in the
meantime.

## Revision engine

`OfflineWritingService` validates input, divides long selections at paragraph,
sentence, or word boundaries, and sends each non-empty chunk to the configured
local Ollama model. The prompt permits spelling, grammar, punctuation,
naturalness, vocabulary, redundancy, and clarity improvements while requiring
unchanged output for already-good text.

Output sanitation rejects control characters, commentary, Markdown wrappers,
truncation, excessive expansion/deletion, damaged indentation, changed blank
lines, or altered list structure. Deterministic semantic validation compares
URLs, email addresses, phone numbers, numbers and currencies, dates, times,
identifiers, quoted text, names, negation, modality, certainty, causal and
temporal relations, intent, politeness, question structure, paragraph
structure, and meaning anchors. An unsafe chunk falls back to its original
text; a failed final whole-selection validation returns the full original.

`Ctrl+Alt+W` is removed. It is not registered as an alias. LanguageTool, its
SAFE routing policy, private Java, lifecycle management, diagnostics, and
installer payload are removed.

## Ollama and provisioning

The application reuses an existing compatible Ollama installation and model.
If the API is stopped, it starts `ollama serve` hidden and detached. Inference
uses Ollama's local loopback API with deterministic generation options and does
not force a CPU or GPU backend. Diagnostics classify CPU, full GPU, or partial
GPU offload from Ollama's reported model and VRAM sizes; vendor/backend remain
unknown when Ollama does not expose them.

Provisioning is a separate accessible post-install process. Setup never waits
for the Ollama installer or model download. The provisioner supports consent,
cancel, retry, resumable installer download, streamed model-pull progress,
existing-install reuse, and model verification. Failure does not corrupt the
core installation, but revision remains unavailable until the model is ready.

## Lifecycle and installation

The installer is per-user and writes one quoted HKCU Run entry. Duplicate
launches exit without disturbing the running instance. Settings, exit, and
restart commands use the hidden control endpoint. Shutdown unregisters the
hotkey and joins active revision workers for a bounded interval. Ollama is
shared user software and is intentionally preserved by uninstall.

Inno Setup stops the application before removing its files and deletes the
startup entry. The packaged application contains Python/PySide runtime files,
the application icon, and third-party notices. It contains no model, Ollama,
Java, LanguageTool, benchmark output, or test artifact.

## Privacy and accessibility

Selected and revised text never enters production logs. Logs contain operation
IDs, character counts, window/process metadata, timings, state transitions,
provider/model status, and error categories.

Settings and provisioning use labelled Qt widgets with accessible names,
descriptions, status text, logical tab order, keyboard operation, and standard
dialogs exposed through Windows UI Automation for NVDA.
