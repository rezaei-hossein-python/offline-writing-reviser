# Accessibility

Offline Writing Reviser is keyboard-first: select text in the target application and press `Ctrl+Alt+P`. No tray icon, mouse-only surface, persistent terminal, or notification-center workflow is required. Settings, Model Setup, and uninstall are discoverable from the Start menu.

Settings and Model Setup use native Qt widgets exposed through Windows UI Automation. Controls have accessible names/descriptions, associated labels, logical tab order, keyboard activation, and deliberate focus placement. Reopening an existing window raises it and moves focus to useful status/model content. Errors use standard dialogs and status text.

Revision announces section progress and completion politely. Model Setup announces stage changes, Ready/failure, and download progress at meaningful percentage intervals; byte counters are 64-bit safe. Hiding the active setup does not cancel it, and reopening reconnects to the same job.

The UI contract and accessibility events are covered by automated structural tests and were designed for NVDA-compatible operation. Manual product validation covered Notepad and Microsoft Word hotkey use. Do not infer tested screen-reader support for every editor, browser, Windows accessibility tool, DPI/theme combination, or remote-desktop environment. Browser support is not fully manually verified. Very frequent third-party clipboard or focus changes can still interfere with capture, and local model latency can delay announcements between sections.
