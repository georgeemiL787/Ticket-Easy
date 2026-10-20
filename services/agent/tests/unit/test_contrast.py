import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_contrast.py"


def test_the_light_theme_tokens_keep_wcag_aa() -> None:
    spec = importlib.util.spec_from_file_location("check_contrast", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main() == 0


def test_the_light_theme_is_the_default_and_the_pages_load_it() -> None:
    shared = SCRIPT.parents[1] / "web" / "shared"
    assert (shared / "aurora.css").read_text(encoding="utf-8").startswith('@import url("aurora-light.css")')
    light = (shared / "aurora-light.css").read_text(encoding="utf-8")
    assert "\n:root {" in light and "data-theme" not in light.split("*/", 1)[1]  # no attribute is needed
    assert "color-scheme: light" in light
    assert "color-scheme: dark" not in (shared / "aurora.css").read_text(encoding="utf-8")
