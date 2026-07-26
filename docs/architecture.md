# Architecture

Offline Writing Reviser 0.3.0 combines a conservative hybrid proofreading
pipeline with a hidden Windows lifecycle.

## Process lifecycle

`offline_writing_reviser.__main__` handles background startup, Settings,
Exit/Restart, diagnostics, provisioning, version, and startup validation.
`application` acquires the per-session mutex, configures per-user logging,
creates the runtime, starts the hidden Win32 command endpoint and global hotkey,
and owns orderly shutdown.

There is no tray icon or visible background window. Short-lived command
processes communicate with a zero-size, never-shown Win32 control window.
Duplicate-instance protection remains mutex-authoritative.

The runtime owns one `LanguageToolRuntime`, one `HybridProofreadingService`, and
the hotkey controller. Shutdown rejects new work, unregisters the hotkey, waits
briefly for active revision threads, then terminates the owned Java child.

## Hybrid proofreading

The authoritative SAFE/routing/validation policy lives in
`offline_writing_reviser.proofreading.policy`. Both production and benchmark
runners import that policy so their executable behavior cannot silently
diverge.

For each boundary-aware input chunk:

1. analyze the original text with LanguageTool using explicit `en-US`;
2. apply non-overlapping, single-candidate SAFE edits in reverse-offset order;
3. analyze the SAFE result again;
4. route only unresolved AMBIGUOUS evidence, unresolved contextual spelling, or
   a mixed partial correction;
5. send post-SAFE text plus local rule evidence to `gemma3:4b`;
6. accept only a local, minimal, structurally safe result;
7. otherwise retain the SAFE output.

Clean, IGNORE-only, and completely resolved chunks bypass Gemma. Provider
failure and model timeout also fall back to the SAFE result. LanguageTool
failure aborts replacement because the deterministic safety stage is required.
The final joined selection is structurally validated before clipboard
replacement.

The Windows controller retains foreground-window/process identity and restores
clipboard formats where practical. It abandons replacement if focus changes or
shutdown begins.

## LanguageTool lifecycle

`proofreading.languagetool.LanguageToolRuntime` resolves application-private
paths relative to either `vendor` (source) or the PyInstaller runtime directory.
It invokes the bundled `java.exe` explicitly and never consults system Java or
PATH.

The runtime reserves a dynamic localhost port, starts LanguageTool without a
visible console, waits for HTTP health, and uses explicit `en-US` requests. It
starts lazily, serializes access, retries once after a failed request, and
terminates only its owned child during shutdown. The server is not started with
LanguageTool's public-bind option.

## Ollama lifecycle and hardware

The Ollama provider reuses an existing compatible user installation. If its
loopback API is stopped, the app may launch `ollama serve` hidden; it does not
own or terminate an already-running shared server. Model requests use a
ten-minute keep-alive and retain the validated deterministic options and
prompt.

No CPU/GPU vendor environment variable or forced device option is set. Ollama
therefore performs its normal hardware selection and CPU fallback. `/api/ps`
telemetry supplies loaded state, model allocation, and `size_vram`; inference
responses supply load, prompt-evaluation, generation, token, and total timing.
The provider reports CPU/GPU/partial/unknown conservatively and never equates
the presence of a Windows GPU with actual acceleration.

## Diagnostics and provisioning

`diagnostics` checks private files, Java version, LanguageTool health and a
deterministic correction, Ollama version/API/model/load state, memory, and
optional Gemma inference telemetry. Reports exclude selected text.

`provisioning` provides an accessible Qt progress dialog. It starts/reuses
Ollama, requests explicit consent before a missing model download, streams
Ollama pull progress, and runs an end-to-end health check. The installer
downloads Ollama only when no compatible installation is found and does not
overwrite or uninstall shared Ollama/model data.

Normal startup performs inexpensive dependency checks in the background and
records actionable state without showing a routine surface.

## Accessibility and errors

Settings remains the Phase 17 Qt/UIA boundary: standard widgets, accessible
names, label buddies, focus order, keyboard behavior, and screen-reader-exposed
validation dialogs. Successful proofreading is silent. Operational errors are
logged and important intervention errors use rate-limited dialogs.

Per-operation logs include character count, LanguageTool duration, routing,
Gemma and native Ollama timing, token counts, validation result, and conservative
compute classification. They do not contain the user's text.

## Packaging

PyInstaller creates an onedir application:

```text
OfflineWritingReviser\
  OfflineWritingReviser.exe
  app\...
  runtime\java\...
  runtime\languagetool\...
  licenses\THIRD_PARTY_NOTICES.md
```

Inno Setup creates a per-user online/bootstrap installer. Application/private
runtimes are embedded; Ollama and `gemma3:4b` are detected, reused, or
provisioned because embedding the model would create a multi-gigabyte,
hard-to-update installer. Uninstall stops the app and removes owned files while
preserving shared Ollama and its model store.
