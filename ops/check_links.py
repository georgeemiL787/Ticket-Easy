"""Check that every relative link in the repository's Markdown files points at something that exists.

Run from anywhere:  python ops/check_links.py
It reads `git ls-files`, so only tracked Markdown is checked. For each link it checks that the file or folder exists and,
when the link has an #anchor into a Markdown file, that a heading with that anchor exists. Links to web addresses, mail
addresses and anything inside a code block or inline code are skipped. Exit code 1 and one line per broken link on
failure.
"""

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
REFERENCE = re.compile(r"^\s*\[(?!\^)[^\]]+\]:\s*(\S+)", re.MULTILINE)
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "tel:", "data:", "ftp:")


def tracked_markdown() -> list[Path]:
    names = subprocess.check_output(["git", "ls-files", "*.md"], cwd=ROOT, text=True, encoding="utf-8").splitlines()
    return [ROOT / n for n in names if (ROOT / n).is_file()]


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"`([^`]*)`", r"\1", heading)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def anchors_of(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    if path not in cache:
        found: set[str] = set()
        seen: dict[str, int] = {}
        in_code = False
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lstrip().startswith("```"):
                in_code = not in_code
                continue
            match = None if in_code else HEADING.match(line)
            if match:
                base = slug(match.group(2))
                count = seen.get(base, 0)
                seen[base] = count + 1
                found.add(base if count == 0 else f"{base}-{count}")
        cache[path] = found
    return cache[path]


def links_in(text: str) -> list[tuple[int, str]]:
    """(line number, target) for every link outside code blocks and inline code."""
    out = []
    in_code = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        stripped = re.sub(r"`[^`]*`", "", line)
        out.extend((number, m.group(1)) for m in LINK.finditer(stripped))
        ref = REFERENCE.match(stripped)
        if ref:
            out.append((number, ref.group(1)))
    return out


def main() -> int:
    cache: dict[Path, set[str]] = {}
    problems = []
    for md in tracked_markdown():
        text = md.read_text(encoding="utf-8", errors="replace")
        for number, target in links_in(text):
            if target.startswith(SKIP_PREFIXES) or target.startswith("<") and target.endswith(">") and target[1:-1].startswith(SKIP_PREFIXES):
                continue
            target = target.strip("<>")
            path_part, _, fragment = target.partition("#")
            where = f"{md.relative_to(ROOT)}:{number}"
            if path_part:
                resolved = (md.parent / unquote(path_part)).resolve()
                try:
                    resolved.relative_to(ROOT)
                except ValueError:
                    problems.append(f"{where}: {target} leaves the repository")
                    continue
                if not resolved.exists():
                    problems.append(f"{where}: {target} does not exist")
                    continue
            else:
                resolved = md
            if fragment and resolved.suffix == ".md" and resolved.is_file():
                if unquote(fragment).lower() not in anchors_of(resolved, cache):
                    problems.append(f"{where}: {target} has no such heading")
    for line in problems:
        print(line)
    print(f"{len(problems)} broken link(s) in {len(tracked_markdown())} Markdown files")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
