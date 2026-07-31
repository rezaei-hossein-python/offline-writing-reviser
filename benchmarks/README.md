# Production revision benchmark

`run_revision_benchmark.py` imports and exercises the exact production service.
It never pulls a model or changes settings.

```powershell
python benchmarks/run_revision_benchmark.py
python benchmarks/run_revision_benchmark.py --long-text
```

The report records exact acceptable outputs, semantic preservation, cold
latency, warm mean/median/P95, and optional approximately 100/500/1,000/2,000
word completeness and latency. Generated results are ignored and are not
packaged.
