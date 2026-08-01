# Changelog

All notable changes supported by repository history are recorded here.

## [0.4.0] - 2026-08-01

### Added

- Automatic Ollama installation/reuse and `gemma3:4b` provisioning with persistent, accessible Model Setup progress and Ready state.
- Adaptive sequential large-document sections, progress announcements, retry/continuation, and safe full-document reconstruction.
- Numeric-context, fact, identifier, name, date, amount, URL, email, negation, modality, intent, and structure safeguards.

### Changed

- Unified production editing into one meaning-preserving Intelligent Revision action on `Ctrl+Alt+P`.
- Broadened safe revision to include grammar, spelling, punctuation, vocabulary, clarity, naturalness, and sentence restructuring.
- Made the application a silent single-instance background process with Start-menu Settings and Model Setup access.
- Hardened foreground capture, modifier release, clipboard restoration, and target-focus verification.

### Fixed

- Prevented partial replacement, duplicate hotkey workers, duplicate model pulls, hidden setup discoverability failures, and orphan application processes.
- Preserved later-section processing when one section times out or is rejected.

### Removed

- LanguageTool, private Java, the hybrid/rule routing service, the separate paraphrase action, and the former secondary production hotkey.

### Security / safety

- Production logs exclude selected and revised text and retain metadata only.
- Unsafe/malformed output falls back to unchanged source text; semantic equivalence remains a risk-reduction mechanism, not a proof.

### Known limitations

- Unsigned installer; browser support not fully manually verified; hardware-dependent local inference; large documents may take several minutes; conservative validation may preserve some sections unchanged.

## [0.3.0] - historical release-candidate work

Repository history documents an installer-era hybrid architecture with LanguageTool/Java, provisioning, and lifecycle hardening. It was superseded before v0.4.0; see [historical validation notes](docs/release-0.3.0.md).

## [0.2.0] - historical

The tagged v0.2.0 release added hidden background lifecycle, accessible Settings, persisted per-user configuration, diagnostics, and packaged application hardening. See [release notes](docs/release-0.2.0.md).
