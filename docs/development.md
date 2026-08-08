# Developer guide

## Environment

Use 64-bit Windows and Python 3.11 or newer. Ollama and `qwen3:1.7b` are needed
for Checkpoint 4 live inference and acceptance; `gemma3:4b` remains installed
until the later migration checkpoint. Unit tests use fakes and do not download
a model. The pinned private LanguageTool and Java archives are prepared
separately and never use system Java.

```powershell
git clone <repository-url>
cd offline-writing-reviser
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,build]"
```

Prepare the checksum-pinned private runtime once before live LanguageTool tests
or packaging:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\prepare-languagetool-runtime.ps1
```

Run from source:

```powershell
python -m offline_writing_reviser --validate-startup
python -m offline_writing_reviser --diagnostics
python -m offline_writing_reviser --provision-model
python -m offline_writing_reviser
```

Checkpoint 5 will update Model Setup and perform the verified migration from
`gemma3:4b`. Do not remove the old model while validating Checkpoint 4.

## Project structure

- `src/offline_writing_reviser/core`: revision service, prompt, chunking, sanitizer, and models.
- `src/offline_writing_reviser/correction`: bounded LanguageTool correction and private runtime ownership.
- `src/offline_writing_reviser/proofreading/semantic.py`: retained internal semantic-validation implementation; it is not a separate production mode.
- `src/offline_writing_reviser/providers`: local Ollama provider.
- `src/offline_writing_reviser/windows`: hotkey, focus, clipboard, control endpoint, and single-instance integration.
- `src/offline_writing_reviser/provisioning*.py`: persistent application-level Model Setup.
- `tests`: unit, reliability, safety, provisioning, architecture, and large-document tests.
- `benchmarks`: production-service quality/performance harness and cases.
- `installer`, `scripts`: Inno Setup and repeatable build scripts.
- `docs`: end-user, architecture, troubleshooting, accessibility, and release documentation.

## Tests and checks

```powershell
python -m pytest -p no:cacheprovider --basetemp .pytest-temp\full
python -m pytest tests\test_phase23_large_documents.py -p no:cacheprovider --basetemp .pytest-temp\phase23
python -m pytest tests\test_documentation_consistency.py -p no:cacheprovider --basetemp .pytest-temp\docs
python -m compileall -q src tests
git diff --check
```

The benchmark exercises the exact production service and never pulls a model or edits settings:

```powershell
python benchmarks\run_revision_benchmark.py
python benchmarks\run_revision_benchmark.py --long-text
```

Treat measured timings as machine-specific evidence, not guarantees.

The independent mechanical-correction benchmark uses the prepared private
runtime and does not invoke Ollama:

```powershell
python benchmarks\run_languagetool_checkpoint2.py
```

The Checkpoint 4 production-service benchmark uses only the synthetic Phase 25
corpus and records the LanguageTool fast path, Qwen path, chunking comparison,
474-word result, and memory evidence:

```powershell
python benchmarks\run_checkpoint4_production_pipeline.py
```

## Build and artifacts

Install Inno Setup 6, prepare the private runtime, then run:

```powershell
.\scripts\build.ps1
.\scripts\build-installer.ps1 -SkipApplicationBuild
# Or build and test everything in one command:
.\scripts\build-installer.ps1
```

Outputs:

- packaged app: `dist\OfflineWritingReviser\OfflineWritingReviser.exe`
- installer: `dist\installer\OfflineWritingReviser-Setup.exe`
- checksum: `dist\installer\OfflineWritingReviser-Setup.exe.sha256`

Generate/verify the checksum with `Get-FileHash .\dist\installer\OfflineWritingReviser-Setup.exe -Algorithm SHA256`.

## Logging and diagnostics

Source and installed builds use `%LOCALAPPDATA%\OfflineWritingReviser\logs\writing-reviser.log` unless `OWR_LOG_FILE` overrides it. Never add selected or revised content to logs. `--diagnostics`, `--diagnostics-json`, and optional `--gemma-test` report configuration, Ollama/API/model state, hardware memory, acceleration evidence, and a minimal health test.

## Version and release workflow

For a version change, update these together:

- `pyproject.toml` project version;
- `src/offline_writing_reviser/version.py`;
- `installer/OfflineWritingReviser.iss` display and numeric versions;
- `CHANGELOG.md` and the versioned release notes;
- documentation consistency assertions.

Then run the full checks, build a fresh installer, record its exact byte size and SHA-256 in the release notes, validate the packaged commands, and test install/setup/hotkey/restart/uninstall on another non-development 64-bit Windows machine. Verify Notepad and Word, CPU-only behavior, interrupted provisioning/reopen, login startup, and zero orphan application processes. Qualify browser/GPU/accessibility claims unless manually verified on that machine.

Keep the working tree clean and review `git diff`, `git diff --check`, and `git status --short` before committing. Do not commit `.venv`, `dist`, `build`, `.build-temp`, `.pytest-temp`, caches, logs, benchmark results, downloaded installers/models, generated spec files, or `vendor`; `.gitignore` covers these. Create a signed tag and GitHub Release only after the release commit is pushed and artifact validation is complete.
