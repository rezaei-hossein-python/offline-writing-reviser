# Architecture (Phase 25 experimental branch)

Offline Writing Reviser retains one canonical action: Intelligent Revision on
`Ctrl+Alt+P`. Checkpoint 4 uses one bounded LanguageTool mechanical-correction
service followed by optional focused `qwen3:1.7b` paraphrasing. It does not add
a hotkey, mode, router, retry, or competing output path.

## Deterministic LanguageTool stage

`LanguageToolCorrectionService.correct(text)` performs exactly one explicit
`en-US` `/v2/check` request. It normalizes LanguageTool's UTF-16 offsets,
rejects malformed, overlapping, conflicting, non-mechanical, or unsafe edits,
and applies the remaining edits once in reverse-offset order. Deterministic
guards preserve numbers, dates, times, amounts, URLs, email addresses,
identifiers, quoted content, detectable names, negation, and modality. The
result retains original/corrected text, applied and skipped edit records, rule
and category identifiers, duration, runtime status, and structured failure
information.

There is no rule-routing policy, recursive check, LanguageTool/model choice,
or separate proofreading/paraphrasing service. Style suggestions are excluded;
the stage is limited to spelling, grammar, punctuation, capitalization, and
other mechanical corrections.

The application owns one reusable loopback server on a dynamic private port.
It launches only bundled Eclipse Temurin `javaw.exe` with no console and only
the bundled LanguageTool 6.6 server. Startup and requests are bounded, the
single application instance prevents duplicate owners, and shutdown terminates
only the recorded child process. The application prewarms the server in a
background thread and irreversibly disables restart after shutdown begins.

## End-to-end revision flow

```mermaid
flowchart LR
    A[Selected text] --> B[Ctrl+Alt+P]
    B --> C[Capture foreground/focus target]
    C --> D[Wait for modifiers to release]
    D --> E[Copy selection and restore clipboard]
    E --> F[Paragraph-first section plan]
    F --> G[One LanguageTool pass]
    G --> H{Small deterministic fast path}
    H -->|skip| K[Use safe LT correction]
    H -->|paraphrase| I[Ollama / qwen3:1.7b]
    I --> J[Sanitize and validate against original]
    J -->|unsafe, failed, or no useful change| K
    J -->|accepted| L[Use safe paraphrase]
    K --> M[Reconstruct full document]
    L --> M
    M --> N[Restore and verify target]
    N --> O[Paste complete result]
    O --> P[Restore current clipboard snapshot]
    P --> Q[Ready]
```

The hidden windowed process acquires a per-session mutex, initializes metadata-only logging and a Qt event dispatcher, exposes a hidden Win32 control endpoint, then registers one Windows global hotkey. The hotkey callback captures the foreground and focused window handles synchronously. A single guarded worker prevents duplicate invocations, waits for Ctrl, Alt, and P to be physically released, and begins the clipboard state machine.

Selection acquisition prefers synchronous `WM_COPY` for standard controls and otherwise sends a scan-code Ctrl+C sequence. It waits for the clipboard sequence number to change and distinguishes an empty selection, copy timeout, and clipboard contention. The pre-capture clipboard snapshot is restored without overwriting a newer external clipboard change. Replacement restores and verifies the original target, snapshots the clipboard again, uses `WM_PASTE` or Ctrl+V, and conditionally restores that fresh snapshot.

No replacement occurs until the entire result has been reconstructed. Focus change, capture failure, provider failure, cancellation, or an invalid full reconstruction leaves the original selection intact.

## Revision engine and safety

`SequentialWritingService` retains original, LanguageTool-corrected,
paraphrased, and final text in memory. It runs LanguageTool exactly once per
section, invokes `qwen3:1.7b` only when a small deterministic fast path does not
settle the result, and validates Qwen output against the original selection.
The prompt limits Qwen to natural wording, fluency, vocabulary, clarity,
concision, and flow; LanguageTool owns mechanical correctness. Already-natural
text remains unchanged.

The sanitizer rejects commentary, prompt leakage, Markdown/code wrappers, control characters, truncation, excessive expansion/deletion, and damaged structure. The semantic validator and normalizers protect:

- numbers and their grammatical/semantic roles, currencies, amounts, dates, and times;
- URLs, email addresses, phone numbers, identifiers, quoted values, and casing-sensitive names;
- negation, modality, certainty, causal/temporal relations, question structure, reference, politeness, and intent;
- paragraph, list, heading, quote, indentation, and blank-line structure.

Validation is deliberately conservative. Rejected Qwen sections fall back to
their safe LanguageTool correction, not blindly to their source text. A valid
deterministic correction is therefore retained when Qwen times out, is missing,
returns malformed output, or fails semantic validation. The controls reduce
semantic risk but cannot prove equivalence.

## Adaptive large-document processing

The default maximum selection is 20,000 characters. Paragraphs are independent
sections with a 1,000-character target; oversized paragraphs split at sentence,
clause, then whitespace boundaries. This avoids splitting protected URL,
email, date, and identifier tokens where practical.

Each Qwen request has an absolute 45-second deadline and no retry. A timeout,
provider failure, malformed output, unsafe output, or no useful change uses the
safe LanguageTool section and continues. LanguageTool failure is explicit and
leaves the original selection intact.

All sections are reassembled byte-contiguously around their revised content,
preserving separators and structure. Fast LanguageTool-only corrections avoid
model progress chatter. Model work announces `Revising text` or
`Revising section n of m`, followed by the applicable corrected, revised, or
fallback completion status. Performance evidence is hardware-specific.

## Ollama provider

The provider discovers an existing Ollama executable, starts `ollama serve`
hidden when the local API is unavailable, verifies `qwen3:1.7b`, and streams one
loopback chat response into memory before validation. Production options are a
4,096-token context, 384-token output limit, temperature 0.2, top-p 0.9,
repeat penalty 1.05, thinking disabled, and ten-minute keep-alive. Ollama
chooses CPU/GPU acceleration.

## Application-level Model Setup

```mermaid
flowchart LR
    A[Install] --> B[Check Ollama]
    B -->|missing| C[Download and install Ollama]
    B -->|present| D[Start or connect to Ollama]
    C --> D
    D --> E[Check model list]
    E -->|missing| F[Pull gemma3:4b]
    E -->|present| G[Verify model list]
    F --> G
    G --> H[Minimal inference]
    H --> I[Persist Ready]
```

The application-level `ProvisioningController` owns one worker and persists 64-bit-safe byte totals and percentages atomically in `%LOCALAPPDATA%\OfflineWritingReviser\provisioning\state.json`. Its phases are checking Ollama, installing Ollama, starting Ollama, checking model, downloading model, verifying model, testing inference, ready, failed, and cancelled.

Closing or choosing Hide during active work hides the window without cancelling. The Start-menu setup shortcut sends a reconnect/focus message to the existing process; a provisioning mutex and controller guard prevent duplicate workers and duplicate model pulls. Interrupted Ollama/model downloads retain resumable data where available, and Retry resumes the missing stage. Ready, failure, progress, and retry state survive the window. While setup is active, the hotkey reports that setup is still in progress and directs the user to Model Setup.

The Qt dialog supplies accessible labels, focus order, byte/percentage details, and polite announcements when the stage changes or progress advances materially. Ready is persisted only after model-list verification and a minimal inference succeeds.

## Settings, diagnostics, and lifecycle

Settings use labelled Qt widgets and persist per-user JSON atomically. They expose installed-model selection, the single hotkey binding (shipped as `Ctrl+Alt+P`), request timeout, maximum input length, log location, and reset. The hidden control window focuses an existing Settings window and routes exit/restart requests to the running instance.

The Inno Setup installer is per-user, x64-compatible, and registers one quoted HKCU Run entry. Normal launches remain silent with no console, tray icon, or taskbar window. Duplicate application launches exit without spawning another worker. Restart performs bounded shutdown, unregisters the hotkey, joins workers, stops the application-owned LanguageTool child, then launches one replacement process. Uninstall requests clean exit and removes the private Java/LanguageTool files with the application directory, along with app files, shortcuts, and startup registration. It preserves shared Ollama, models, settings, and logs.

Logs contain metadata only: operation IDs, counts, target process/window metadata, timings, state transitions, provider/model status, and failure categories. Selected and revised text are not logged by default.
