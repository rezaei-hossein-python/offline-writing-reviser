# Phase 25 checkpoint 1: v0.4.0 baseline

Measured on 2026-08-07 before any production-code change. The raw results are
in `benchmarks/baselines/phase25-v0.4.0-baseline.json` and
`benchmarks/baselines/phase25-v0.4.0-first-token.json`; the fixed fixture and
reproducible harnesses are `benchmarks/phase25_baseline_cases.json`,
`benchmarks/run_phase25_baseline.py`, and
`benchmarks/measure_phase25_first_token.py`.

## Release and repository state

| Item | Recorded value |
| --- | --- |
| Starting branch | `main` |
| Starting HEAD | `147793a16206958641bd36d8afb8d7a00b6d383f` |
| `origin/main` | `147793a16206958641bd36d8afb8d7a00b6d383f` |
| `v0.4.0` tag target | `147793a16206958641bd36d8afb8d7a00b6d383f` |
| Starting worktree | clean |
| Experimental branch | `language-tool-lightweight-paraphrasing` |
| Application version | `0.4.0` |
| Installer version | `0.4.0` (`0.4.0.0` numeric) |
| Installer | `dist/installer/OfflineWritingReviser-Setup.exe` |
| Installer size | 32,382,737 bytes (30.88 MiB) |
| Installer SHA-256 | `B6DA380442BF7C387BEB1F7EEC8329171F96E4B16E3A4ECDA2FC59291072F867` |
| Production model | `gemma3:4b`, 4.3B parameters, Q4_K_M |
| Model disk size | 3,338,801,804 bytes (3.11 GiB; `ollama list` rounds to 3.3 GB) |
| Collected tests | 248 |

The tag was inspected only. It was not moved, recreated, or modified.

## Hardware and runtime

| Item | Recorded value |
| --- | --- |
| Computer | Microsoft Surface Laptop 5 |
| CPU | Intel Core i5-1235U, 10 cores / 12 logical processors |
| RAM | 8,405,794,816 bytes (7.83 GiB) |
| GPU | Intel Iris Xe Graphics (integrated) |
| Ollama | 0.32.6 |
| Runtime backend | CPU (`size_vram=0`) |
| Loaded model runtime size | 3,040,692,141 bytes |
| `llama-server` working set after run | 2,579,410,944 bytes (2.40 GiB) |
| `llama-server` private bytes after run | 4,434,796,544 bytes (4.13 GiB) |
| VRAM used by model | 0 bytes reported by Ollama |

Ollama reports CPU/GPU/partial-GPU classification but does not identify a
compute backend or device name. First-token time is not exposed by the v0.4.0
provider, so a benchmark-only streaming probe used the exact production prompt,
generation settings, model, and spelling input. Cold first-token time was
21.400 seconds and cold wall time was 21.925 seconds. An immediate identical-
input repeat measured 0.870 seconds to first token and 1.456 seconds wall time;
that repeat benefits from prompt/model caching and is not substituted for the
varied-input warm median below. The full-set first cold request reported 15.301
seconds of model load, 10.446 seconds of prompt evaluation, and 0.611 seconds of
generation.

## Packaging and provisioning profile

v0.4.0 is a per-user, x64-compatible Inno Setup bootstrap. It bundles the
windowed PyInstaller application, Python/PySide6 runtime, icon, licence, and
notices. It does not bundle Ollama, the model, Java, LanguageTool, benchmarks,
or tests. The installer registers one quoted HKCU startup entry, starts the
hidden application, and launches Model Setup independently after installation.

Model Setup reuses or installs shared Ollama, pulls `gemma3:4b`, verifies the
installed-model list, performs minimal inference, and then persists Ready.
Provisioning uses one guarded worker with persistent progress/retry state. The
uninstaller stops the application and removes application files, shortcuts,
and startup registration while preserving Ollama, all models, settings, and
logs.

## Fixed-set performance

The model was explicitly unloaded before the first case. The fixed set contains
21 spelling, grammar, punctuation, ESL, style, correct-text, protected-data,
question, commitment, and multi-paragraph cases. Each warm figure represents a
different case through the same production service and resident model.

| Metric | Result |
| --- | ---: |
| Cold latency | 26.632 s |
| Warm median | 17.291 s |
| Warm P95 | 21.987 s |
| Short-set requests | 21 (one per case) |
| Short-set total duration | 377.443 s |
| Short-set prompt evaluation | 277.990 s |
| Short-set generation duration | 37.149 s |
| Short-set aggregate reported load duration | 59.695 s |
| Timeout rate | 0/21 (0%) |
| Unsafe rejection rate | 8/21 (38.1%) |
| Unchanged rate | 10/21 (47.6%) |
| Deterministic semantic preservation | 21/21 (100%) |

The aggregate Ollama load figure is the sum of per-request `load_duration`, not
the one-time cold load; the cold-load value is 15.301 seconds. The v0.4.0
provider uses a large 400-plus-token system prompt even for short selections,
making prompt evaluation the dominant warm cost. No request timed out.

## Quality review

Exact-string acceptance was 13/21 (61.9%), but manual review was also performed
because useful paraphrases need not match one reference string.

| Classification | Count | Notes |
| --- | ---: | --- |
| Correct unchanged/protected | 8 | Names, dates, amounts, identifiers, negation, modality, questions, and commitments remained safe. |
| Correct deterministic correction | 5 | Spelling, simple grammar, article, and both ESL examples matched the expected correction. |
| Useful non-exact paraphrase | 4 | Awkward wording, vocabulary, redundancy, and the short multi-paragraph message improved naturally. |
| Unnecessary rewrite | 2 | Strong correct text was rewritten; the URL/email sentence was needlessly rephrased. |
| Missed correction after unsafe fallback | 2 | Punctuation and subject-verb agreement inputs remained incorrect. |
| Accepted semantic/factual regression | 0 | No accepted output changed protected meaning or facts in the short set. |
| Timeout or malformed result | 0 | No short request timed out or produced a pasted malformed result. |

The safe fallback is effective but expensive: eight short outputs were rejected
and restored. This prevented semantic drift but also left two straightforward
mechanical errors unfixed. The already-correct sentence was changed solely for
lexical variety (`starts` to `begins`), contrary to the desired no-change
behavior.

## Approximately 500 words

The structured long fixture contains 474 words, six paragraphs, protected
names, organizations, dates, times, numbers, amounts, URLs, email addresses,
identifiers, negation, modality, questions, and commitments.

| Metric | Result |
| --- | ---: |
| Duration | 239.664 s (3 min 59.7 s) |
| Model requests | 9 sequential requests |
| Model wall time | 239.447 s |
| Prompt evaluation | 147.134 s |
| Generation duration | 72.671 s |
| Aggregate reported load duration | 19.162 s |
| Unsafe or reconstruction rollbacks | 8 of 9 sections |
| Timeouts | 0 |
| Deterministic semantic preservation | accepted |

Structure and factual anchors survived, and no partial result was returned.
Quality was poor: only one section was accepted, most spelling/grammar/ESL
errors remained, and the accepted section contained a mojibake apostrophe
(`Iâ€™ve`). The deterministic validator judged the final document semantically
safe, but that does not make it a satisfactory revision. Total measured time
for the short set plus the long fixture was 617.107 seconds.

## Validation

- `python -m pytest --collect-only -q`: 248 tests collected.
- `python -m pytest -q`: 248 passed in 4.85 seconds on the final run.
- `python -m py_compile` over `src`, `benchmarks`, and `tests`: passed.
- `git diff --check`: passed.
- The first full-suite attempt used an invalid sandbox temp root and encountered
  setup errors; rerunning with `%TEMP%` isolated those environmental failures.
- Production source and behavior were not changed in this checkpoint.

## Baseline conclusion

v0.4.0 is semantically conservative but too slow for interactive correction on
this laptop. It can produce good short rewrites, yet simple errors may survive
when the one-model output is rejected. Long selections amplify both problems:
sequential requests take minutes and conservative rollbacks discard nearly all
potential improvements. This supports measuring a single deterministic
LanguageTool correction followed by a narrow lightweight paraphrasing stage,
without restoring the former complex hybrid architecture.
