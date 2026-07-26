# Proofreading model benchmark

This standalone harness compares already-installed Ollama models for the
Offline Writing Reviser. It does not import or change production behavior,
download models, alter the configured default, or implement chunking.

## Run

Start Ollama, then run:

```powershell
python benchmarks/run_proofreading_benchmark.py
```

The harness attempts `ollama list` first, then uses the local Ollama HTTP API
at `http://127.0.0.1:11434` to discover installed models and collect native
timing/token metrics. Only exact candidate names reported by `/api/tags` are
run. Missing candidates are reported as unavailable and are never pulled.

Useful options:

```powershell
python benchmarks/run_proofreading_benchmark.py --help
python benchmarks/run_proofreading_benchmark.py --models llama3.2:3b gemma3:4b
python benchmarks/run_proofreading_benchmark.py --skip-performance
python benchmarks/run_proofreading_benchmark.py --skip-llama-baseline
python benchmarks/run_proofreading_benchmark.py --limit 5 --skip-performance
```

`--limit` is only for harness smoke tests. A comparable official run should
use the complete dataset and the default performance samples.

## Dataset and metrics

`proofreading_cases.json` contains deterministic, unambiguous cases. Exact
string equality is the quality criterion. Already-correct cases must be
returned byte-for-byte unchanged. Error cases count as exact corrections only
when their output equals the expected output.

Per-case results include exact match, latency, character lengths, change
behavior, unnecessary edits, missed corrections, formatting preservation,
Ollama evaluation metrics, provider errors, and timeouts.

A missed correction is a case that required a change but did not exactly reach
the deterministic expected output. This intentionally counts partial fixes
and fixes with additional changes as failures under the strict product rule.

Summary metrics include exact preservation, exact correction accuracy,
over-edit and missed-error rates, formatting preservation, mean/median/P95
latency, tokens per second when supplied by Ollama, and error/timeout rate.

The weighted score is:

- 40% exact preservation of already-correct text
- 30% exact correction accuracy on grammar, spelling, and mixed cases
- 20% speed
- 10% formatting preservation

Speed is inverse min-max normalized from median quality-case latency:
`(slowest median - model median) / (slowest median - fastest median)`.
The fastest model scores 1 and the slowest scores 0. If only one model
successfully runs, it scores 1 for speed. Raw metrics are always retained.

Formatting preservation compares line breaks, blank-line positions, trailing
newline state, and structural line prefixes (indentation/bullets/numbering)
with the expected output.

## Generation settings

Every model receives the required prompt as the identical system message and
the case input as the user message. Settings are temperature 0, seed 0,
8,192-token context, 4,096-token output cap, and `think: false`. The generated
JSON records any model-specific differences; the harness currently applies
none.

When `llama3.2:3b` is installed, a separate paired pass uses a verbatim snapshot
of the current production prompt. Its records do not enter the model ranking.
The report calls the strict prompt a material improvement only if the average
of its preservation and correction-accuracy deltas is at least 5 percentage
points and neither metric declines by more than 2 points.

Long samples of approximately 100, 500, 1,000, and 2,000 words are separate
timing probes and do not affect semantic quality scores. A single request is
flagged as impractical at 30 seconds or on timeout.

Outputs are overwritten on each run:

- `results/latest.json` — full machine-readable configuration and results
- `results/latest.csv` — flat per-quality-case records
- `results/latest.md` — human-readable ranking and findings

## LanguageTool 6.6 raw and SAFE-filter benchmark

Phase 18B established the raw LanguageTool suggestion baseline. Phase 18C uses
the same 105 cases to classify every observed rule as SAFE, AMBIGUOUS, or
IGNORE and compare the raw reachability ceiling with actual output from a
conservative deterministic filter:

```powershell
python benchmarks/run_languagetool_benchmark.py
```

The harness resolves Java only from `vendor/java/bin/java.exe` and the server
only from `vendor/languagetool/languagetool-server.jar` by default. It does not
require system Java, `PATH`, or `language-tool-python`. It starts its own
loopback-only server, waits for readiness, sends every `/v2/check` request with
the explicit language `en-US`, and stops only the process it started. Use
`--help` to override paths, port, timeouts, or the generated-results directory.
`--limit` is available only for smoke testing.

### Rule classification

Classification is based on the observed rule/case evidence, not LanguageTool's
category label:

- SAFE requires enough evidence for a deterministic constrained policy and no
  observed expected-unchanged trigger.
- AMBIGUOUS covers sparse evidence, multiple context-dependent choices, valid
  alternatives, or changes that may alter tone.
- IGNORE covers observed wrong, conflicting, optional, or offset/whitespace-
  unsafe behavior.

The initial policy intentionally leaves grammar rules with only one or two
positive cases AMBIGUOUS even when those cases were exactly reachable.
`MORFOLOGIK_RULE_EN_US` is the only SAFE rule, and SAFE status does not mean
that every match from that rule is accepted.

### Deterministic SAFE policy

The filter:

1. considers only explicitly SAFE rules;
2. requires a token-local alphabetic replacement that preserves the source
   token's case pattern;
3. accepts exactly one candidate, or one explicit evidence-backed lexical
   choice for `adress`, `imediately`, and `recieved`;
4. rejects missing, unclassified, AMBIGUOUS, IGNORE, unresolved
   multi-candidate, overlapping, or conflicting matches;
5. applies independent accepted edits from the end of the text backward so
   original LanguageTool offsets remain valid.

The filter never generally takes the first replacement and never applies all
LanguageTool suggestions. If confidence is insufficient, the source range is
left untouched. Text with no accepted correction is returned byte-for-byte
unchanged, including whitespace, punctuation, line endings, and formatting.

### Metrics and evidence

The report includes both:

- raw exact-correction reachability, where the expected output may be
  constructed from any non-overlapping subset of returned candidates; and
- filtered SAFE-mode exact accuracy against the filter's actual output.

Both modes report exact preservation, exact correction, over-edit rate,
formatting preservation, and latency. SAFE mode additionally reports cases
changed, corrections applied, rejected matches, rejection reasons, and
per-match accept/reject decisions.

The generated, gitignored `results/languagetool/` directory contains:

- `latest.json` — complete raw responses, normalized matches, candidate
  replacements, rule/category evidence, classification rationales,
  `MORFOLOGIK_RULE_EN_US` failure analysis, SAFE outputs and audit decisions
- `latest.csv` — flat raw and SAFE per-case metrics
- `latest.md` — raw-versus-filter comparison, all rule classifications,
  category counts, and important failures

Limitations: the 105 cases provide no expected-unchanged triggers for any of
the 23 observed rule IDs, most grammar rules have only one or two positive
examples, and exact benchmark expectations do not cover every valid English
alternative. The policy therefore favors preservation and defers uncertain
grammar/context decisions instead of maximizing correction coverage.

This remains benchmark-only. Nothing is connected to the production hotkey,
revision service, prompt, sanitizer, Ollama provider, configured model, Gemma
behavior, settings, UI, clipboard, packaging, or runtime behavior under
`src/`.
