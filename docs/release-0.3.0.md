# 0.3.0 release-candidate validation (historical)

> This document records the superseded 0.3.0 release-candidate architecture
> and measurements. It is not current product or v0.4.0 installation guidance.

Phase 19 integrates the Phase 18 hybrid policy into the production hotkey path
and adds private LanguageTool/Java lifecycle management, Ollama telemetry,
diagnostics, accessible model provisioning, and an online Windows installer.

## Quality parity

The 105-case production-service benchmark produced:

- exact correction: 59.375% (38/64);
- exact preservation: 100% (35/35);
- over-edit rate: 0% (0/41);
- formatting preservation: 100% (10/10);
- Gemma calls: 21/105, with 0/35 already-correct cases routed;
- Gemma accepted/fallback: 18/3;
- accepted non-exact outputs and regressions: 0/0;
- total mean/median/P95: 1.795/0.093/8.037 seconds.

This matches Phase 18D quality behavior. Performance varies with model residency
and hardware; CPU inference remains the dominant routed latency.

## Validation

- Python 3.13.9 / pytest 8.4.2: 185 passed.
- Changed Python files compiled successfully.
- PyInstaller onedir startup and bundled runtime resolution passed.
- Installed `--version`, `--validate-startup`, and diagnostics passed.
- Installed Gemma health test passed with Ollama 0.32.4.
- Diagnostics observed CPU execution (`size_vram=0`) on the validation machine;
  no GPU performance claim is made.
- Silent install, duplicate launch, Settings routing, Restart, Exit, uninstall,
  reinstall, and path-with-spaces behavior passed.
- Final restart/uninstall test left zero application/Java processes and removed
  the complete application-owned directory.
- Existing Settings/UIA/accessibility tests passed. A final interactive NVDA
  listening pass remains required on representative release hardware.

The real foreground `Ctrl+Alt+W` replacement contract is covered by controller,
clipboard, focus, and production-policy tests plus the 105-case service
benchmark. A final clean-machine manual test remains required because this
automated environment cannot safely own a user's foreground editor.

## Defects found and fixed

- A concurrent startup health check could restart LanguageTool after shutdown,
  orphaning Java and leaving runtime files during uninstall. Shutdown is now
  irreversible, retry is blocked after it begins, diagnostics cleanup is
  unconditional, and regression/installed-lifecycle tests cover the race.
- Ollama executable probing now tolerates inaccessible candidate paths while
  still using a reachable loopback API.
- Installer silent mode no longer opens interactive model provisioning.
- Uninstall shutdown has a stable Inno `RunOnceId`.

## Artifact

```text
dist\installer\OfflineWritingReviser-Setup.exe
Size: 283,058,881 bytes
SHA-256: 313CB444EB7F3B5734F711B9B200AA28D5DBE409866BB10D8BFE37CCE2C4BF36
```

The artifact is not Authenticode-signed and is not automatically published.
Sign it and repeat clean-machine/NVDA/GPU acceptance before a public release.

## Remaining limitations

- Ollama/model provisioning is online and the current official installer binary
  is not hash-pinned.
- No GPU-equipped system was available. Ollama chooses acceleration; the
  acceptance procedure in `docs/installer.md` must be run elsewhere.
- The Ollama API may establish VRAM offload without exposing exact
  vendor/device/backend details.
- Low-disk, driver-specific GPU initialization failure, Windows reboot startup,
  and a truly clean non-development VM still require representative-system
  testing.
- Offline Writing Reviser does not remove shared Ollama/model data or per-user
  settings/logs during uninstall.
