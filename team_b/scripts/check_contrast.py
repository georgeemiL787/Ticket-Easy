"""WCAG contrast check for the light aurora tokens (team_b/web/shared/aurora-light.css).

Reads the tokens from the css, checks every text role (>= 4.5:1) on the page, white and tinted backgrounds, and the
non-text accents (>= 3:1). Exit 1 on a failure. Usage: python scripts/check_contrast.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "web" / "shared" / "aurora-light.css"

TEXT_ON_BACKGROUNDS = {
    "text": ["bg", "surface", "teal-soft", "green-soft", "amber-soft", "red-soft"],
    "text-2": ["bg", "surface"],
    "muted-text": ["bg", "surface"],
    "teal-text": ["bg", "surface", "teal-soft"],
    "amber-text": ["bg", "surface", "amber-soft"],
    "green-text": ["bg", "surface", "green-soft"],
    "red": ["bg", "surface"],
    "blue": ["bg", "surface"],
    "magenta": ["bg", "surface"],
}
WHITE_ON = ["teal-text", "red"]  # button fills with a white label
NON_TEXT = ["teal", "magenta", "blue", "amber", "green", "red"]  # icons, edges, focus ring
EXTRA_PAIRS = [
    ("#b91c1c", "red-soft"),
    ("#991b1b", "red-soft"),
    ("#ffffff", "#115e59"),
]  # danger button/badge, primary hover


def tokens() -> dict[str, str]:
    block = re.search(r"^:root \{(.*?)\n\}", CSS.read_text(encoding="utf-8"), re.S | re.M)
    assert block, "token block not found"
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", block.group(1)))


def luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def resolve(name: str, table: dict[str, str]) -> str:
    return name if name.startswith("#") else table[name]


def main() -> int:
    table = tokens()
    failures: list[str] = []

    def check(fg: str, bg: str, need: float) -> None:
        got = ratio(resolve(fg, table), resolve(bg, table))
        status = "ok " if got >= need else "FAIL"
        print(f"{status} {fg:>12} on {bg:<10} {got:5.2f} (need {need})")
        if got < need:
            failures.append(f"{fg} on {bg}: {got:.2f} < {need}")

    for fg, backgrounds in TEXT_ON_BACKGROUNDS.items():
        for bg in backgrounds:
            check(fg, bg, 4.5)
    for fill in WHITE_ON:
        check("#ffffff", fill, 4.5)
    for fg, bg in EXTRA_PAIRS:
        check(fg, bg, 4.5)
    for accent in NON_TEXT:
        check(accent, "bg", 3.0)
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
