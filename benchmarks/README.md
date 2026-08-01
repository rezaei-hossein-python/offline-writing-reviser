# Production revision benchmark

`run_revision_benchmark.py` imports the single production `OfflineWritingService`; it is not an older rule/router or separate proofreading architecture. The historical dataset filename `proofreading_cases.json` is retained as an internal test asset name. The harness never pulls a model or changes Settings.

```powershell
python benchmarks\run_revision_benchmark.py
python benchmarks\run_revision_benchmark.py --long-text
```

Reports cover accepted output, semantic/fact preservation, unchanged cases, cold/warm latency, and optional approximately 100/500/1,000/2,000-word completeness. Generated results are ignored and not packaged. Latency is hardware-, model-residency-, and document-dependent; never present it as a universal guarantee.
