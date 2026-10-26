"""The preview page, rendered in headless Chromium."""

import pathlib
import re
import unittest

import design_render

ROOT = pathlib.Path(__file__).resolve().parent.parent


class PreviewTest(unittest.TestCase):
    def test_shows_a_swatch_per_colour_without_errors(self) -> None:
        result = design_render.render(ROOT, ROOT / "preview" / "index.html")
        self.assertEqual(result.errors, [])
        self.assertEqual(design_render.find_marked(result.dom, "load-error"), [])
        css = (ROOT / "src" / "palette.css").read_text(encoding="utf-8")
        colours = re.findall(r"^  --ex-", css, re.MULTILINE)
        swatches = design_render.find_marked(result.dom, "swatch")
        self.assertEqual(len(swatches), len(colours))
