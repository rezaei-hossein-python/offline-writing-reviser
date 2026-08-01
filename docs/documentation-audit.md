# Documentation audit for v0.4.0

Audit scope: all tracked Markdown, `pyproject.toml`, Inno Setup metadata, build metadata, and user-facing Settings/Model Setup/error text at the v0.4.0 release-preparation commit.

| Match/category | Classification | Disposition |
|---|---|---|
| `Ctrl+Alt+P`, `gemma3:4b`, Ollama, adaptive sections, persistent Model Setup, silent background operation | Current and intentional | Documented as the unified product. |
| Internal paths/names containing `proofreading` and benchmark asset `proofreading_cases.json` | Current and intentional implementation name | Explicitly identified as internal; not presented as a separate user mode. |
| Statements that Java, LanguageTool, the old hybrid/rule router, separate paraphrase action, or secondary hotkey are removed/not bundled | Current and intentional | Retained only when explaining present dependencies or removal. No install/use instruction remains. |
| `docs/release-0.2.0.md` references to earlier prerequisites/limitations | Historical release documentation | Retained unchanged under a versioned title and linked as historical. |
| `docs/release-0.3.0.md` references to the former hotkey, LanguageTool/Java, hybrid benchmarks, 185 tests, old size/hash/performance | Historical release documentation | Retained as a historical release-candidate validation record and explicitly marked superseded. |
| RC versions `0.4.0rc1` / `0.4.0-rc1` | Stale and must be updated | Promoted consistently to final `0.4.0`. |
| README claims of browser/Office compatibility without test qualification | Stale and must be updated | Replaced with verified Notepad/Word scope and qualified browser wording. |
| README 20,000-character behavior described only as paragraph-aware | Stale and must be updated | Replaced with final adaptive paragraph/sentence/clause/whitespace behavior, deadline, retry, and fallback. |
| Installer/setup text suggesting transient or separate provisioning only | Stale and must be updated | Replaced with application-level controller, persistent state, Hide/reopen/focus, and duplicate-worker prevention. |
| Generic Start-menu text that did not match the actual shortcut | Stale and must be updated | User message now says **Set up intelligent revision**. |
| Old active benchmark architecture/performance claims, installer sizes/hashes, test counts, terminal/manual model commands, unsupported-large-document claims, obsolete paths/dependencies | Obsolete and should be removed | Removed from current docs; old measurements remain only inside the explicitly historical v0.3.0 record. |

Repository searches after editing are part of the release validation. Historical matches must be evaluated in their versioned context rather than rewritten as current behavior.
