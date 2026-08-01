# User guide

## Intelligent Revision

The primary and only production action is `Ctrl+Alt+P`:

1. Select editable text.
2. Press `Ctrl+Alt+P` once.
3. Wait while the local model processes it.
4. The complete revised text replaces the selection.

The engine can correct spelling, grammar, and punctuation; improve vocabulary, clarity, and natural phrasing; remove awkward repetition; and restructure sentences. It preserves meaning, intent, facts, identifiers, names, numbers and their roles, dates, amounts, URLs, emails, negation, and other protected details. Correct text may remain unchanged.

Examples:

| Selected text | Possible accepted result |
|---|---|
| `I recieved the adress yesterday.` | `I received the address yesterday.` |
| `He explained me the process.` | `He explained the process to me.` |
| `The meeting was very good and we discussed about many important things.` | `The meeting went very well, and we discussed many important things.` |
| `Jordan Lee approved CAD 1,250 on July 8, 2026.` | May remain unchanged; protected facts must not drift. |

## Applications and large selections

Notepad and Microsoft Word have passed manual end-to-end validation. Browser-editor compatibility is not fully manually verified; clipboard handling, rich editors, or browser shortcuts may vary, so do not rely on browser support for sensitive work without testing that editor first.

Long selections are divided sequentially using paragraph, sentence, clause, and whitespace boundaries. Slow sections can cause smaller pending sections. Progress is announced by section. Unsafe or timed-out sections remain unchanged while later sections continue, then the full selection is reconstructed. The application should never paste a partial or truncated result. Local speed depends on hardware; around 2,000 words may take several minutes on slower machines.

## What different outcomes mean

| Outcome | Behavior |
|---|---|
| No selection | An actionable message asks you to select text; the model is not called. |
| Duplicate hotkey press | The second invocation is ignored while the first revision owns the worker. |
| Large selection | It is processed in adaptive sections up to the configured 20,000-character default maximum. |
| Section timeout | One retry is attempted; if it also times out, that section stays unchanged and later sections continue. |
| Provider/model unavailable | Processing stops and nothing is replaced. Open Model Setup or Diagnostics. |
| Provisioning in progress | The hotkey reports setup status and directs you to reopen Model Setup. |
| Semantic safety rejection | The original section is retained. Completion may say some sections were unchanged. |
| Unchanged output | No replacement is needed; this can mean the text was already good or all proposed changes were rejected. |
| Focus changed before paste | Replacement is cancelled so text is not pasted into the wrong window. |

## Settings and status

Open **Start > Offline Writing Reviser > Settings**. Keyboard-accessible controls manage the installed model, request timeout, maximum input length, global hotkey, log location, and defaults. The shipped and validated binding is `Ctrl+Alt+P`. Closing Settings does not exit the app.

There is no tray icon. Use Start-menu shortcuts for Settings, Model Setup, and uninstall. For exit/restart and diagnostics, use the commands in the [README](../README.md#settings-diagnostics-exit-and-restart).
