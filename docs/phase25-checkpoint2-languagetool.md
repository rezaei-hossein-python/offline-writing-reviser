# Phase 25 checkpoint 2: deterministic LanguageTool correction

Measured on 2026-08-08 on the same Surface Laptop 5 used for checkpoint 1.
Raw machine-readable results are in
`benchmarks/baselines/phase25-languagetool-checkpoint2.json`; the reproducible
harness is `benchmarks/run_languagetool_checkpoint2.py`.

## Implemented architecture

`correct(text) -> LanguageToolCorrectionResult` performs one bounded request to
one private LanguageTool server, with explicit `en-US` configuration. The result
retains original and corrected text, applied and skipped edits, rule identifiers,
categories, timings, runtime status, and structured failure information.

Matches are normalized from LanguageTool UTF-16 offsets, validated, and applied
once from right to left. Malformed, non-mechanical, overlapping, conflicting, or
protected-token-changing edits are skipped. The evidence-backed
`BEEN_PART_AGREEMENT` exclusion prevents the observed harmful `engineer` to
`engineered` suggestion. There is no hybrid router, SAFE policy, repeated pass,
proofreading mode, or alternative production output path. The v0.4.0 model and
Ctrl+Alt+P behavior remain unchanged until a later checkpoint.

## Private runtime

| Component | Pinned version | Archive | Installed size |
| --- | --- | ---: | ---: |
| Eclipse Temurin x64 JRE | 17.0.20+8 | 43,776,637 bytes | 130,895,115 bytes |
| LanguageTool standalone | 6.6 | 251,998,221 bytes | 405,072,177 bytes |
| Combined | — | 295,774,858 bytes | 535,967,292 bytes (511.14 MiB) |

`runtime-manifest.json` pins both download URLs and SHA-256 values. The build
requires the prepared private runtime and bundles its legal files. At runtime the
application launches only its bundled `javaw.exe`, on a dynamic loopback port,
with no window, and reuses one server. Startup, requests, and shutdown are
bounded. Shutdown targets only the process handle created by the application.

An isolated packaged application and installer were built outside `dist`. The
test installer was 283,099,149 bytes with SHA-256
`8FB4FDD15F754214E86A12B0A04CF4D7F3450C0F2CDD4D53EA8DBA2AA3DAA10B`.
Installed validation showed one `javaw.exe` from the private install path with a
zero main-window handle. Silent uninstall left zero application and Java
processes, removed the dedicated test install directory and startup entry, and
preserved the installed v0.4.0 release.

## Performance

| Metric | LanguageTool | v0.4.0 baseline where comparable |
| --- | ---: | ---: |
| Server socket ready | 1,787.67 ms | — |
| First startup plus English-rule warm-up | 6,135.28 ms | 26,632 ms cold revision |
| First correction after warm-up | 160.48 ms | — |
| Warm sentence median | 42.63 ms | 17,291 ms |
| Warm sentence P95 | 58.85 ms | 21,987 ms |
| Warm sentence maximum | 65.98 ms | — |
| Warm paragraph median | 80.25 ms | — |
| Warm paragraph P95 | 95.30 ms | — |
| Shutdown | 49.36 ms | — |

The warm sentence path is approximately 405 times faster than the varied-input
v0.4.0 warm median. Both requested warm targets were met with substantial
margin. The private server used one Java process, a 497,328,128-byte working set
and 537,231,360 private bytes at the end of the benchmark.

## Correction quality

| Input | Result |
| --- | --- |
| `I recieved the adress yesterday.` | `I received the address yesterday.` |
| `He go to work every day.` | `He goes to work every day.` |
| `We discussed about the project.` | `We discussed the project.` |
| `The meeting starts at nine tomorrow morning.` | unchanged |
| `The list of changes are attached.` | `The list of changes is attached.` |
| `I am writing this email for informing you about the issue.` | unchanged; not made worse |

Four of six required mechanical cases matched their complete expected output;
five received a useful correction. The punctuation case received one valid comma
but not every desirable comma. The article case was deliberately left unchanged
because LanguageTool's offered replacement was demonstrably harmful. Across all
12 benchmark cases, 10 were acceptable, no incorrect edit was applied, and all
12 protected-data checks passed. Names, organizations, numbers, dates, times,
money, URLs, email addresses, identifiers, and negation remained intact.

## Checkpoint decision

Yes: LanguageTool solves the mechanical-error portion quickly and safely enough
to justify continuing Phase 25. Its reach is intentionally incomplete, but the
measured latency is far below both targets, required spelling/grammar/ESL cases
are corrected, protected data is preserved, and conservative skips introduce no
observed regression. Checkpoint 3 should proceed only when explicitly requested.
