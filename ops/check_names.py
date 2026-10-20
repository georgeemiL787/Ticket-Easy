"""Fail when an old name comes back: a letter name for a service, the old word for the agent's core, or the old word for
a built-in fake service. It reads every tracked file and every file and folder name.

Run from anywhere:  python ops/check_names.py [more paths to check instead of the whole repository]

What it looks for (case-insensitive, whole words, so "upstream" and "understanding" never match):
  - the old word for a group of people followed by a, b or c, written with a space, underscore, dash or nothing
    (and the same glued to a class name), and the settings that began with it;
  - that group word on its own, except in the short list of customer-facing phrases where it means a shop's own staff;
  - the old work-stream letters, the old name for the agent's core, and the old name for the built-in fake services.
The patterns are built from parts, so this file does not trip its own check.
"""

import fnmatch
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GROUP = "te" + "am"
CORE = "br" + "ain"
TRACK = "tr" + "ack"
FAKE = "stand" + "-in"

# Phrases where the group word means the shop's own staff: they appear in shop policies and in replies customers read.
ALLOWED_PHRASES = [f"customer care {GROUP}", f"customer service {GROUP}", f"support {GROUP}", f"our {GROUP}", f"care {GROUP}"]
ALLOWED = re.compile("|".join(re.escape(p) for p in ALLOWED_PHRASES), re.IGNORECASE)

PATTERNS = [
    ("a letter name", re.compile(GROUP + r"[ _-]?[abc](?![a-z])", re.IGNORECASE)),
    ("a letter name glued to a class name", re.compile(GROUP.capitalize() + r"[ABC](?![a-z])")),
    ("the old group word", re.compile(r"\b" + GROUP + r"s?\b", re.IGNORECASE)),
    ("a work-stream letter", re.compile(r"\b" + TRACK + r"[ _-]?[ab](?![a-z])", re.IGNORECASE)),
    ("the old name for the core", re.compile(r"\b" + CORE + r"\b", re.IGNORECASE)),
    ("the old name for the fake services", re.compile(r"\bstand[-_]?ins?\b", re.IGNORECASE)),
]

# Files that may keep a match, each with the reason. Keep this short; fix the file instead of widening it.
EXCEPTIONS: dict[str, str] = {
    "docs/reports/eval_runs/knowledge/db-build_noon_eg.txt": "recorded output of a command run before the rename: it shows the old folder in a path",
}


def tracked_files() -> list[str]:
    out = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8")
    return [p for p in out.split("\0") if p]


def is_excepted(rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for pattern in EXCEPTIONS)


def hits_in(text: str):
    masked = ALLOWED.sub(lambda m: " " * len(m.group(0)), text)
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(masked):
            yield kind, match


def main(argv: list[str]) -> int:
    names = argv or tracked_files()
    problems: list[str] = []
    for rel in names:
        rel = rel.replace("\\", "/")
        if is_excepted(rel):
            continue
        for kind, match in hits_in(rel):
            problems.append(f"{rel}: the name has {kind}: {match.group(0)!r}")
        path = ROOT / rel
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            continue  # a binary file
        text = data.decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            for kind, match in hits_in(line):
                shown = line.strip()
                shown = shown if len(shown) <= 100 else shown[max(0, match.start() - 40) : match.end() + 40].strip()
                problems.append(f"{rel}:{number}: {kind}: {match.group(0)!r}  {shown}")
    for problem in problems:
        print(problem)
    print(f"{len(problems)} old name(s) found in {len(names)} files")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
