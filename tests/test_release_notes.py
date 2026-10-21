"""The release workflow takes the notes of a release from CHANGELOG.md."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("release_notes", ROOT / "tools" / "release_notes.py")
rn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rn)

LOG = """# Changelog

## [Unreleased]

### Added
- waiting for the next release

## [0.2.0] – 2026-11-01

### Fixed
- something

## [0.1.0] – first public release

- the first
"""


def test_a_release_gets_its_own_section():
    assert rn.notes("v0.2.0", LOG) == "### Fixed\n- something"
    assert rn.notes("0.1.0", LOG) == "- the first"


def test_a_pre_release_gets_the_notes_of_the_version_it_leads_to_else_what_is_unreleased():
    assert rn.notes("v0.2.0-beta.1", LOG) == "### Fixed\n- something"  # 0.2.0 is already written down
    assert rn.notes("v0.3.0-beta.1", LOG) == "### Added\n- waiting for the next release"  # 0.3.0 is not: what is waiting
    assert rn.notes("v0.2.0-beta.1", LOG.replace("## [0.2.0]", "## [0.2.0-beta.1]")) == "### Fixed\n- something"  # its own section wins


def test_a_release_without_a_section_gets_nothing_so_github_can_generate_notes():
    assert rn.notes("v0.9.0", LOG) == ""
    assert rn.notes("v0.9.0", "") == ""


def test_the_real_changelog_has_notes_for_the_current_version():
    assert rn.notes("v" + (ROOT / "VERSION").read_text().strip(), (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
