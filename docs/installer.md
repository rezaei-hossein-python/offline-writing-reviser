# Release build and installation

## Dependency inputs

The build expects these intentionally untracked directories:

```text
vendor\java
vendor\languagetool
```

Phase 19 was validated with Java 17.0.19 and LanguageTool 6.6. Verify applicable
licenses before substituting versions. Java and LanguageTool are copied into
the private installed runtime; neither is registered system-wide.

Ollama and `gemma3:4b` are not embedded. Setup reuses a compatible Ollama
installation or offers the official Windows installer, then obtains explicit
consent before Ollama pulls the missing model. This keeps setup practical and
allows Ollama to update hardware support independently. It also means initial
setup needs internet access when either component is missing.

## Reproducible commands

Install Python 3.11+, Inno Setup 6, and project build dependencies:

```powershell
python -m pip install -e ".[dev,build]"
.\scripts\build-installer.ps1
```

The script runs the test suite, builds the PyInstaller onedir distribution,
compiles the Inno installer, and writes a SHA-256 file beside it. Build and
`dist` outputs are disposable and ignored by Git.

To rebuild only setup after an already-validated application build:

```powershell
.\scripts\build-installer.ps1 -SkipApplicationBuild
```

## Installed behavior

The default per-user location is:

```text
%LOCALAPPDATA%\Programs\Offline Writing Reviser
```

Setup creates Start Menu entries for the app, Settings, Diagnostics, and
Uninstall. Provisioning validates the local services before normal startup.
Silent setup deliberately skips interactive model provisioning; managed
deployment must provision Ollama/model separately or run `--provision-model`
interactively afterward.

Uninstall sends `--exit`, removes application-owned files and shortcuts, and
preserves settings/logs plus shared Ollama/model data. This prevents an
application uninstall from destroying resources another program may use.

## Smoke-test checklist

1. Install into a path containing spaces.
2. Run `--version`, `--validate-startup`, and `--diagnostics --gemma-test`.
3. Launch the background app twice and verify one instance.
4. Open Settings and verify keyboard/UIA behavior.
5. Proofread a correct sentence, a misspelling, and contextual grammar.
6. Exercise Exit and Restart.
7. Uninstall while the background app is running; verify its Java child exits.
8. Reinstall and repeat startup diagnostics.

Run the final NVDA listening and real foreground-hotkey checks on a clean target
machine; automated tests cannot fully emulate those interactions.

## GPU acceptance on another computer

1. Install the identical setup artifact; do not rebuild it.
2. Run `OfflineWritingReviser.exe --diagnostics --gemma-test`.
3. Confirm `size_vram` and acceleration classification reflect the loaded
   model. Record exact device/backend only if Ollama exposes it.
4. Proofread the standard contextual test and record total, load,
   prompt-evaluation, and generation duration from diagnostics/logs.
5. Compare with CPU telemetry and confirm the final correction is identical.
6. Check that no `OWR_OLLAMA_EXECUTABLE` wrapper or Ollama environment setting
   forces CPU execution.

Vulkan support can be version/platform dependent. Report unknown rather than
claiming acceleration when Ollama exposes insufficient telemetry.

## Known deployment limitations

- Setup downloads the current official Ollama installer over HTTPS but does not
  pin its changing binary hash.
- A fully offline bundle is not produced because Ollama plus model data would
  add several gigabytes and complicate licensing/update behavior.
- Disk-full, driver-specific GPU failure, and clean-VM upgrade behavior require
  final validation on representative deployment hardware.
- Code signing is not configured; the generated artifact is release-candidate
  quality but should be Authenticode-signed before public distribution.
