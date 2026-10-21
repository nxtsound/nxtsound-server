"""Prints the release notes for a version tag from CHANGELOG.md (the release workflow puts them into the GitHub release).

    python tools/release_notes.py v0.2.0            the section "## [0.2.0]"
    python tools/release_notes.py v0.2.0-beta.1     its own section, else the section of the version it leads to ("## [0.2.0]"), else "## [Unreleased]"

Prints nothing if there are no notes (the workflow then lets GitHub generate them from the merged pull requests).
"""
import re
import sys
from pathlib import Path


def notes(tag: str, changelog: str) -> str:
    version = tag.strip().lstrip("vV")
    sections: dict[str, str] = {}
    for part in re.split(r"(?m)^## ", changelog)[1:]:
        head, _, body = part.partition("\n")
        m = re.match(r"\[([^\]]+)\]", head.strip())
        if m:
            sections[m.group(1)] = body.strip()
    if sections.get(version):
        return sections[version]
    if "-" in version:  # a pre-release has no section of its own: what the final version will bring
        return sections.get(version.split("-")[0]) or sections.get("Unreleased", "")
    return ""


if __name__ == "__main__":
    path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parents[1] / "CHANGELOG.md"
    print(notes(sys.argv[1], path.read_text(encoding="utf-8")))
