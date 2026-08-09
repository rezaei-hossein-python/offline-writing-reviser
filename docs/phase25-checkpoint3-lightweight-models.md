# Phase 25 checkpoint 3: lightweight paraphrasing evaluation

Measured on 2026-08-08 on the same Surface Laptop 5 as checkpoints 1 and 2.
This checkpoint changes no production code, default model, pipeline, or
provisioning behavior. Raw results are in
`benchmarks/baselines/phase25-lightweight-model-evaluation.json`, long-form
winner results are in
`benchmarks/baselines/phase25-lightweight-winner-long.json`, and every output's
manual classification is in
`benchmarks/phase25_lightweight_manual_review.json`.

## Decision

**WINNER: `qwen3:1.7b`.** It is the only candidate that combines target-level
latency with controlled paraphrasing and acceptable factual safety. It retained
13 strong inputs unchanged, produced four useful and three acceptable
paraphrases, made two unnecessary rewrites, and left one improvable sentence
weak. It had no manually identified semantic, factual, protected-token, or
modality regression in the short corpus. The current deterministic validator
accepted 22 of 23 outputs.

Gemma 1B was slower and repeatedly changed modality. Llama 1B was fast when
warm but failed the quality gate through hallucination, factual and modality
drift, protected-token corruption, commentary, refusal, and unrelated expansion.

## Machine and model inventory

The machine has an Intel Core i5-1235U, 7.83 GiB RAM, and integrated Intel Iris
Xe graphics. Ollama 0.32.6 ran all candidates on 100% CPU and reported zero
VRAM use.

Before evaluation, `ollama list` contained only `gemma3:4b`. Exact current tags
were resolved from the official Ollama library before download.

| Model/tag | Ollama ID | Parameters | Quantization | Download/installed logical size | Loaded RAM reported by Ollama |
| --- | --- | ---: | --- | ---: | ---: |
| `gemma3:1b` | `8648f39daa8f` | 999.89M | Q4_K_M | 815,319,791 bytes | 880,541,695 bytes |
| `qwen3:1.7b` | `8f68893c685c` | 2.0B | Q4_K_M | 1,359,293,444 bytes | 1,882,424,605 bytes |
| `llama3.2:1b` | `baf6a787fdff` | 1.2B | Q8_0 | 1,321,098,329 bytes | 1,519,292,251 bytes |

The logical size is Ollama's exact local tag size; command-line display rounds
these to 815 MB, 1.4 GB, and 1.3 GB. The Qwen tag is named `1.7b`, while the
installed metadata reports 2.0B parameters (the Ollama library reports 2.03B).

## Prompt and generation settings

All models received the same LanguageTool-corrected text and this system prompt:

> Rewrite the text in clear, natural, fluent English.
>
> Improve phrasing, vocabulary, clarity, conciseness, and sentence flow only
> where useful.
>
> Preserve the complete meaning, purpose, facts, names, organizations, numbers,
> dates, times, amounts, URLs, email addresses, identifiers, negation, modality,
> questions, commitments, and intent.
>
> Do not add information.
>
> If the text is already natural and well written, return it unchanged.
>
> Return only the final text.
>
> No explanation. No commentary. No Markdown. No labels. No quotation wrapper.
> No reasoning.

Comparable settings were temperature 0.2, top-p 0.9, repeat penalty 1.05, seed
25, 4,096-token context, 384-token output limit, streaming enabled, thinking
disabled, and ten-minute keep-alive. Every short run stopped normally before the
output limit. The 4,096-token context covers the bounded inputs without paying
for each model's much larger advertised window. Models remained visible in
`/api/ps` throughout their warm series; no cold reload was observed.

## Performance

Cold measurements followed an explicit unload. Warm figures contain 20 varied
sentence runs and three paragraph runs per model.

| Metric | Gemma 3 1B | Qwen 3 1.7B | Llama 3.2 1B |
| --- | ---: | ---: | ---: |
| Cold wall time | 8.729 s | 8.378 s | 11.081 s |
| Cold model load | 6.730 s | 5.342 s | 7.665 s |
| Cold first token | 8.389 s | 7.566 s | 10.401 s |
| Cold generation | 0.341 s | 0.809 s | 0.714 s |
| Warm sentence median | 4.203 s | 1.776 s | 1.732 s |
| Warm sentence P95 | 5.344 s | 2.379 s | 2.488 s |
| Warm paragraph median | 6.447 s | 4.460 s | 5.033 s |
| Warm paragraph P95 | 6.912 s | 4.825 s | 16.195 s |
| Several-paragraph input | 6.963 s | 4.865 s | 5.033 s |
| Median generation rate | 29.81 tok/s | 21.78 tok/s | 21.47 tok/s |
| Input/output tokens, 23 warm runs | 3,615 / 465 | 3,702 / 521 | 3,778 / 766 |

Model inference remains the dominant latency. Adding checkpoint 2's measured
LanguageTool median (42.63 ms sentence, 80.25 ms paragraph) gives these projected
sequential times before negligible sanitizer/validator overhead:

| Projected metric | Gemma 3 1B | Qwen 3 1.7B | Llama 3.2 1B | v0.4.0 |
| --- | ---: | ---: | ---: | ---: |
| Both stages cold | 14.864 s | 14.513 s | 17.216 s | 26.632 s |
| Warm sentence median | 4.246 s | 1.818 s | 1.775 s | 17.291 s |
| Warm sentence P95 | 5.387 s | 2.422 s | 2.531 s | 21.987 s |
| Warm paragraph median | 6.527 s | 4.540 s | 5.114 s | — |
| Warm paragraph P95 | 6.992 s | 4.905 s | 16.275 s | — |

Qwen's projected warm sentence median is about 9.5 times faster than v0.4.0.

## Short-corpus quality

Each of 23 outputs has exactly one manually reviewed primary classification.

| Outcome | Gemma 3 1B | Qwen 3 1.7B | Llama 3.2 1B |
| --- | ---: | ---: | ---: |
| Excellent unchanged | 5 | 13 | 2 |
| Useful paraphrase | 4 | 4 | 1 |
| Acceptable paraphrase | 2 | 3 | 2 |
| Unnecessary rewrite | 1 | 2 | 0 |
| Weaker wording | 3 | 1 | 0 |
| Grammatical regression | 0 | 0 | 1 |
| Semantic regression | 3 | 0 | 4 |
| Factual regression | 0 | 0 | 3 |
| Protected-token regression | 1 | 0 | 2 |
| Negation/modality regression | 4 | 0 | 5 |
| Commentary/wrapper | 0 | 0 | 3 |

Deterministic validator acceptance was 15/23 for Gemma, 22/23 for Qwen, and
5/23 for Llama. There were no timeouts or malformed transport responses.
LanguageTool alone was manually preferable in 12 Gemma cases, three Qwen cases,
and 18 Llama cases.

For the five cases with exactly matching v0.4.0 inputs, Qwen was better once,
equivalent three times, and worse once. The worse case was an unnecessary
`before` to `until` rewrite that the deterministic validator rejected. Broader
paraphrasing comparison is not claimed because the checkpoint 3 corpus differs.

## 474-word finalist test

Only Qwen was run because the short-corpus quality difference was decisive.
The existing six source paragraphs were corrected once by LanguageTool and sent
as six structure-preserving model requests.

| Metric | Qwen 3 1.7B | v0.4.0 baseline |
| --- | ---: | ---: |
| Words | 474 | 474 |
| Requests | 6 | 9 |
| Total model duration | 48.300 s | 239.664 s |
| Accepted paraphrases | 3/6 | 1/9 |
| Validator rejections/fallbacks | 3/6 | 8/9 |
| Timeouts | 0 | 0 |
| Structure preserved | yes | yes |

Sections 1, 3, and 6 used LanguageTool fallback. Section 1 was manually useful
but rejected by conservative relation/number-context guards. Section 3 changed a
question/request into an imperative, and section 6 deleted a constraint, so the
latter two were correctly better left as LanguageTool output. The run was about
five times faster than v0.4.0 while accepting three times as many sections.

## Cleanup and recommendation

After evaluation, exact rejected tags `gemma3:1b` and `llama3.2:1b` were removed
with `ollama rm`. The remaining inventory is exactly:

- `qwen3:1.7b` — retained experimental winner for checkpoint 4.
- `gemma3:4b` — unchanged existing production model.

There were no pre-existing unrelated models, and `gemma3:4b` was not removed or
modified. The production default remains `gemma3:4b`; provisioning and the
production pipeline remain unchanged.

Checkpoint 4 should proceed when explicitly requested, using `qwen3:1.7b` as the
provisional lightweight paraphraser. Its principal risks are conservative
unchanged output on some improvable prose, occasional unnecessary rewriting,
and long-section semantic fallbacks. Production integration must preserve the
LanguageTool fallback and validate against the original text.
