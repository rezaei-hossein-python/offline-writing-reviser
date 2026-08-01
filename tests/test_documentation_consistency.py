from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CURRENT_DOCS = (
    ROOT / "README.md",
    ROOT / "THIRD_PARTY_NOTICES.md",
    ROOT / "benchmarks" / "README.md",
    ROOT / "docs" / "accessibility.md",
    ROOT / "docs" / "architecture.md",
    ROOT / "docs" / "development.md",
    ROOT / "docs" / "installation.md",
    ROOT / "docs" / "installer.md",
    ROOT / "docs" / "release-0.4.0.md",
    ROOT / "docs" / "troubleshooting.md",
    ROOT / "docs" / "user-guide.md",
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_current_documentation_describes_only_unified_production_action():
    combined = "\n".join(_text(path) for path in CURRENT_DOCS)
    assert "Ctrl+Alt+P" in combined
    assert "Ctrl+Alt+W" not in combined
    assert "gemma3:4b" in combined
    for stale_instruction in (
        "install Java",
        "install LanguageTool",
        "start LanguageTool",
        "ollama pull gemma3:4b",
    ):
        assert stale_instruction.lower() not in combined.lower()


def test_final_version_is_consistent_in_release_metadata():
    assert 'version = "0.4.0"' in _text(ROOT / "pyproject.toml")
    assert '__version__ = "0.4.0"' in _text(
        ROOT / "src" / "offline_writing_reviser" / "version.py"
    )
    installer = _text(ROOT / "installer" / "OfflineWritingReviser.iss")
    assert '#define AppVersion "0.4.0"' in installer
    assert '#define AppNumericVersion "0.4.0.0"' in installer


def test_release_notes_identify_current_artifact():
    notes = _text(ROOT / "docs" / "release-0.4.0.md")
    assert "OfflineWritingReviser-Setup.exe" in notes
    assert "32,382,737 bytes" in notes
    assert (
        "B6DA380442BF7C387BEB1F7EEC8329171F96E4B16E3A4ECDA2FC59291072F867"
        in notes
    )


def test_provisioning_and_browser_scope_stay_qualified():
    combined = "\n".join(_text(path) for path in CURRENT_DOCS)
    assert "persistent" in combined.lower()
    assert "reopen" in combined.lower()
    assert "not fully manually verified" in combined.lower()
    assert "Notepad" in combined and "Microsoft Word" in combined


def test_current_installer_paths_are_documented():
    combined = _text(ROOT / "README.md") + _text(ROOT / "docs" / "development.md")
    assert "dist\\installer\\OfflineWritingReviser-Setup.exe" in combined
    assert "%LOCALAPPDATA%\\Programs\\Offline Writing Reviser" in _text(
        ROOT / "docs" / "troubleshooting.md"
    )


def test_local_markdown_links_resolve():
    markdown_files = [ROOT / "README.md", ROOT / "CHANGELOG.md", *CURRENT_DOCS]
    for document in dict.fromkeys(markdown_files):
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", _text(document)):
            if "://" in target or target.startswith("#"):
                continue
            relative = target.split("#", 1)[0]
            assert (document.parent / relative).resolve().exists(), (
                f"Broken local link in {document.relative_to(ROOT)}: {target}"
            )
