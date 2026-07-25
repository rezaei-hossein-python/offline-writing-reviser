# Proofreading Model Benchmark

Generated: 2026-07-25T07:15:53.640120+00:00

Installed Ollama models: llama3.2:3b
Candidate models unavailable: qwen3.5:4b, qwen3:4b, gemma3:4b
Models benchmarked: llama3.2:3b

## Configuration

- Quality cases: 105 (already_correct: 35, grammar: 30, spelling: 20, mixed: 10, formatting: 10)
- Temperature: 0; seed: 0; context: 8,192 tokens; maximum generation: 4,096 tokens
- Thinking/reasoning: disabled with `think: false` for every model
- Prompt (identical system message for every model):

```text
Correct only objective spelling and grammatical errors.

Make the minimum changes necessary.

Do not paraphrase, improve style, change tone, change vocabulary, or restructure sentences.

If the text is already grammatically and orthographically correct, return it exactly unchanged.

Preserve punctuation, capitalization, formatting, line breaks, and paragraph structure whenever they are already correct.

Return only the resulting text.
```

## Quality and latency

| Rank | Model | Weighted | Preserve | Correct | Over-edit | Missed | Format | Median s | Mean s | P95 s | Tok/s | Error/timeout |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | llama3.2:3b | 58.0% | 42.9% | 56.2% | 63.4% | 43.8% | 40.0% | 1.697 | 2.048 | 3.912 | 16.660 | 0.0% |

The weighted score is 40% exact preservation, 30% exact correction accuracy, 20% normalized speed, and 10% formatting preservation. Speed uses median quality-case latency and min-max normalization: `(slowest median - model median) / (slowest median - fastest median)`. A sole successful model receives 100% for speed; a failed model receives 0%.

## Findings

- Best preservation: llama3.2:3b
- Best correction: llama3.2:3b
- Fastest: llama3.2:3b
- Lowest over-edit: llama3.2:3b
- Best overall for this product: llama3.2:3b

- Recommendation: do not change the production default from this run. Only one candidate was available, so the ranking is not comparative. Its 42.9% preservation rate does not satisfy the product's exact-unchanged requirement reliably.
- llama3.2:3b competitiveness against the other candidates cannot be established because no other candidate was installed. Its absolute quality rates remain poor despite the stricter prompt.
- Compared with the current production prompt, the strict prompt changed llama3.2:3b preservation by +28.6 points and correction accuracy by +7.8 points. Material improvement: True.
- llama3.2:3b raw paired rates — strict: 42.9% preservation, 56.2% correction; production prompt: 14.3% preservation, 48.4% correction.
- Materiality rule: Average of preservation and correction deltas is at least +5 percentage points, with neither metric declining by more than 2 points.

## Model-specific issues

- llama3.2:3b: 54 exact failures, 26 unnecessary edits on expected-unchanged cases, 28 inexact/missed corrections, and 6 formatting-structure failures.
- Several llama3.2:3b outputs treated workplace sentences as requests directed at an assistant (for example, asking for an attachment or answering a scheduling question) instead of proofreading them.

## Long-text timing

| Model | Words | Latency s | Tok/s | Unchanged | Impractical (>=30 s/timeout) | Error |
|---|---:|---:|---:|---:|---:|---|
| llama3.2:3b | 100 | 20.269 | 6.764 | False | False |  |
| llama3.2:3b | 500 | 50.774 | 4.106 | False | True |  |
| llama3.2:3b | 1000 | 29.733 | 9.136 | False | False |  |
| llama3.2:3b | 2000 | 38.238 | 9.226 | False | True |  |

4 of 4 long-text outputs were altered or truncated; this is reported as behavior evidence but is not included in semantic scoring.

Long samples contain already-correct repeated prose. They are timing probes, not part of exact semantic quality scoring. A request is labeled impractical at 30 seconds or on timeout.

Production model behavior, prompt, hotkey handling, and chunking were not changed.
