# Architecture

Offline Writing Reviser is split into four standalone layers.

## Application

`offline_writing_reviser.application` loads environment-backed configuration, configures file logging, creates the provider/service/controller graph, registers the global hotkey, runs the Windows message loop, and unregisters hotkeys on shutdown.

## Core

`offline_writing_reviser.core` owns the revision contract:

- prompt text
- input validation
- maximum-length enforcement
- concurrent revision guard
- output sanitization
- provider-neutral result and error handling

The core layer has no Windows, Ollama, network, database, or UI dependency.

## Providers

`offline_writing_reviser.providers` contains local model integrations. The initial provider is `OllamaCliOfflineWritingProvider`.

The Ollama provider:

- resolves the configured `ollama` executable
- verifies the configured model with `ollama list`
- never downloads models
- runs `ollama run <model> <prompt>` locally
- maps executable, model, timeout, and process failures to provider-specific exceptions

## Windows Integration

`offline_writing_reviser.windows` contains the proven hotkey and text-selection code adapted from the original implementation.

The text adapter preserves the safety behavior from the source subsystem:

- waits for `Ctrl`, `Alt`, and `W` to be physically released after the hotkey
- snapshots and restores clipboard formats where possible
- captures only the selected foreground text
- checks foreground-window identity before replacement
- skips paste if focus assumptions become unsafe
- serializes overlapping controller triggers

Successful revisions do not show feedback. Failed revisions leave the selected text unchanged where the target application cooperates with normal copy/paste semantics.
