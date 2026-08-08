# Phase 25 checkpoint 4: sequential production pipeline

## Scope and decision

Checkpoint 4 replaces the experimental branch's single-model production path
with one linear pipeline:

`original -> one LanguageTool pass -> optional qwen3:1.7b paraphrase -> sanitize -> validate against original -> final`

The checkpoint succeeds. Mechanical corrections are now fast paths, useful
paraphrases are materially faster than v0.4.0, unsafe or unusable Qwen output
falls back per section, and installed Notepad/Word acceptance passed. Model
provisioning, upgrade migration, and removal of `gemma3:4b` remain explicitly
deferred to Checkpoint 5.

## Production behavior

`SequentialWritingService` retains `original_text`, `languagetool_text`,
`paraphrased_text`, and `final_text`. Each non-empty section receives exactly
one bounded LanguageTool request. Qwen receives only the accepted
LanguageTool-corrected text. Sanitized Qwen output is checked by the existing
deterministic semantic/factual validator against the original section, never
only against the corrected text.

The fallback order is safe Qwen output, safe LanguageTool output, then the
original text. A Qwen timeout, unavailable provider, malformed response,
commentary, factual/semantic rejection, or no useful change therefore cannot
discard a valid deterministic correction. Provider/model inference is called
at most once per selected section and partial streamed output is never pasted.
LanguageTool failure is explicit and leaves the original selection intact.

Paragraphs are independent production sections even when a multi-paragraph
selection is shorter than the 1,000-character target. Oversized paragraphs use
the existing word- and protected-token-safe boundary splitter. This makes a
model failure local to one paragraph while preserving separators, blank lines,
lists, indentation, headings, quotations, and line endings.

## Small deterministic model-skip rule

Qwen is skipped when LanguageTool reports no edit and no evidence-backed
awkward marker is present; for spelling, punctuation, or capitalization-only
edits; and for one clear grammar edit whose corrected result has no awkward
marker. Multiple or uncertain grammar edits and the finite tested awkward
phrase set default to Qwen. There is no learned classifier, score, retry,
router, SAFE policy, repeated LanguageTool pass, or separate revision mode.

## Qwen configuration

| Setting | Value |
|---|---:|
| Model | `qwen3:1.7b` |
| Context | 4,096 tokens |
| Output limit | 384 tokens per section |
| Temperature | 0.2 |
| Top-p | 0.9 |
| Repeat penalty | 1.05 |
| Seed | 25 |
| Thinking | disabled |
| Streaming | enabled; accumulated before validation/paste |
| Keep-alive | 10 minutes |
| Section target | 1,000 characters, paragraph-first |

Installed acceptance showed the Checkpoint 3 prompt could return the required
vague awkward sentence unchanged. One narrow task clarification was added:
replace vague qualifiers and generic nouns with clearer wording when meaning is
preserved. This changed the tested output to "The meeting was very good and we
discussed many important topics." without weakening the factual constraints.

## Final benchmark on the Surface

The reproducible raw evidence is
`benchmarks/baselines/phase25-checkpoint4-production.json`. The fixed corpus has
23 synthetic cases. Manual review found no unsafe accepted output or protected
token regression. Seven corpus outputs were useful or acceptable paraphrases,
one was a deterministic LanguageTool correction, twelve were correctly left
unchanged, and three Qwen attempts were safely rejected in favor of the
LanguageTool text. Five additional required awkward probes all produced useful
accepted improvements.

| Metric | Checkpoint 4 | v0.4.0 baseline |
|---|---:|---:|
| LanguageTool warmup | 6.571 s | not applicable |
| Cold Qwen-path wall time after LT warmup | 7.339 s | 26.632 s |
| Fast mechanical median / P95 | 47.68 / 96.95 ms | 17.291 / 21.987 s overall |
| Warm paraphrase median / P95 | 1.489 / 1.753 s | 17.291 / 21.987 s overall |
| Fixed-corpus median / P95 | 58.19 ms / 3.581 s | 17.291 / 21.987 s |
| Fixed-corpus LT-only / Qwen operations | 13 / 10 | not separated |
| Fixed-corpus Qwen fallbacks | 3/10 | not directly comparable |
| Unsafe accepted outputs | 0 | 0 |

The three required mechanical inputs produced the expected results with zero
Qwen calls. Across five repeats each, the overall median was 47.68 ms and P95
was 96.95 ms:

- `I recieved the adress yesterday.` -> `I received the address yesterday.`
- `He go to work every day.` -> `He goes to work every day.`
- `We discussed about the project.` -> `We discussed the project.`

The cold measurement unloads Qwen but warms LanguageTool first. A worst-case
immediate hotkey before the asynchronous LanguageTool warmup completes is
therefore approximately the measured 6.57-second LT warmup plus the 7.34-second
Qwen path. Normal startup does not block on either warmup, LanguageTool warms in
the background, and Qwen is not eagerly loaded on this 8 GB machine. After the
first Qwen operation, its ten-minute keep-alive is reused.

## 474-word structure test and chunking decision

The final 474-word, 2,902-character input preserved all structure. The selected
1,000-character paragraph policy made six Qwen requests and completed in
37.958 seconds: 0.551 seconds in LanguageTool, 37.387 seconds in Qwen, and
0.019 seconds in validation. Three sections accepted Qwen output and three used
safe fallback. This is 6.31 times faster than the v0.4.0 baseline of 239.664
seconds, reduces requests from nine to six, and reduces rejected/rolled-back
sections from eight to three.

Bounded alternatives on the same run were: 700-character paragraphs 39.662 s
(six requests); 1,000-character paragraphs 37.958 s (six); grouped paragraphs
at 1,000 characters 42.225 s (four); and grouped paragraphs at 1,400 characters
41.919 s (three). The 1,000-character paragraph policy was retained because it
was fastest in the final run and preserves finer section-level fallback.

## Memory and runtime lifecycle

The Surface reports 8,405,794,816 bytes (7.83 GiB) total RAM and CPU-only Qwen
execution (`size_vram=0`). In final installed acceptance, the app used about
17.6 MB working set / 27.2 MB private bytes, private LanguageTool Java used
about 324.2 MB / 508.6 MB, and Ollama reported the loaded Qwen allocation as
1,882,424,605 bytes. The accounted combined working allocation was therefore
about 2.07 GiB plus the Ollama host's smaller process overhead. `gemma3:4b` was
installed but not loaded concurrently.

Application exit completed in 839.6 ms. The final process counts were zero app
processes and zero `javaw.exe` processes; the LanguageTool log recorded a
46.81 ms private-runtime shutdown. No terminal window was created.

## Installed acceptance and artifact

The isolated per-user test installer had a unique application ID and install
directory, did not overwrite the public installation, and did not run Model
Setup. It was built with the bundled Eclipse Temurin 17.0.20+8 runtime and
LanguageTool 6.6 distribution.

| Artifact | Evidence |
|---|---|
| Installer | `OfflineWritingReviser-Phase25-CP4-Test-Setup.exe` |
| Size | 283,114,140 bytes |
| SHA-256 | `8D2298DA75CEC40F7D1F730F1EECB110232AB32D87663D31498F0F4A1C396F1D` |
| Uncompressed package | 651,401,063 bytes |

The seven cases—spelling, grammar, awkward, correct, protected facts, short
paragraph, and several paragraphs—passed in both Notepad and Word. All 14 had
successful capture, expected Qwen routing, expected output/change behavior,
protected-data preservation, structure preservation, clipboard restoration,
and Ready recovery. Changed outputs pasted successfully; unchanged cases used
the no-replacement completion path. Per-case timings and outputs are summarized
above; the production telemetry contains no selected or revised text.

The experimental installation and startup entry were removed after testing.
The public v0.4.0 installation remained present. `qwen3:1.7b` and
`gemma3:4b` both remained installed; no model was removed.

## Automated validation

- Sequential/LanguageTool/semantic/Windows targeted suite: 166 passed; the
  live LanguageTool test initially timed out while Qwen was deliberately kept
  loaded, then passed after `ollama stop qwen3:1.7b` released its keep-alive
  allocation. Qwen was unloaded, not removed.
- Final LanguageTool correction/integration rerun: 25 passed.
- Full suite: 309 passed in 10.00 seconds.
- `py_compile`: 53 source, test, and benchmark files compiled with bytecode
  directed to the ignored test directory.
- `git diff --check`: passed.

## Privacy-safe telemetry

One production summary records only input character count, section count,
LanguageTool duration/applied/skipped counts, Qwen invocation and duration,
accepted/rejected/fallback section counts, validation duration, paste duration,
total duration, and result category. It never records original, corrected,
paraphrased, or final text.

## Checkpoint boundary

The experimental branch default is now `qwen3:1.7b`. Existing persisted
v0.4.0 settings may still name `gemma3:4b`; upgrading those settings, verifying
the new model through Model Setup, rollback, and safe removal of the old
official model are Checkpoint 5 work and are not implemented here.
