"""Check local Markdown links and heading anchors without network access."""

import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def anchors(text: str) -> set[str]:
    result = set()
    counts: dict[str, int] = {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.MULTILINE):
        slug = "".join(
            character
            for character in heading.lower()
            if character in "-_" or unicodedata.category(character)[0] not in "PS"
        ).replace(" ", "-")
        occurrence = counts.get(slug, 0)
        counts[slug] = occurrence + 1
        result.add(f"{slug}-{occurrence}" if occurrence else slug)
    return result


def main() -> int:
    documents = sorted(
        [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md"), *(ROOT / ".github").rglob("*.md")]
    )
    errors = []
    for document in documents:
        content = document.read_text(encoding="utf-8")
        links = re.findall(r"\]\(([^\s)]+)\)", content)
        links += re.findall(r'(?:src|href)="([^\"]+)"', content)
        for link in links:
            parsed = urlsplit(link)
            if parsed.scheme or parsed.netloc:
                continue
            target = (document.parent / unquote(parsed.path)).resolve()
            if not parsed.path:
                target = document
            if not target.is_relative_to(ROOT) or not target.exists():
                errors.append(f"{document.relative_to(ROOT)}: missing local target {link}")
            elif parsed.fragment and target.suffix == ".md":
                if unquote(parsed.fragment) not in anchors(target.read_text(encoding="utf-8")):
                    errors.append(f"{document.relative_to(ROOT)}: missing heading {link}")
    for error in errors:
        print(error)
    print(f"Checked {len(documents)} documents; {len(errors)} broken local links/anchors.")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
