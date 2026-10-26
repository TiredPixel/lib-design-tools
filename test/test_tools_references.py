"""Tests for tools/references-check."""

from __future__ import annotations

import contextlib
import io
import pathlib
import subprocess
import tempfile
import unittest

import tools_loader

references_check = tools_loader.load("references-check")

SRI = 'integrity="sha384-abc" crossorigin="anonymous"'


class ReferencesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = pathlib.Path(directory.name)
        for name in ("site/a.css", "site/img/x.png", "top.js", "site/dir/index.html"):
            self.write(name, "")
        (self.root / "site/empty").mkdir()

    def write(self, name: str, content: str | bytes) -> pathlib.Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def run_main(self, *paths: pathlib.Path) -> tuple[int, str]:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = references_check.main(["--root", str(self.root), *map(str, paths)])
        return status, stderr.getvalue()

    def html_problems(self, markup: str) -> list[tuple[int, str]]:
        page = self.write("site/page.html", markup)
        return references_check.check_html(page, self.root)

    def css_problems(self, css: str) -> list[tuple[int, str]]:
        sheet = self.write("site/sheet.css", css)
        return references_check.check_css(sheet, self.root)


class ClassifyTest(unittest.TestCase):
    def test_kinds(self) -> None:
        cases = {
            "": ("ignore", ""),
            "  ": ("ignore", ""),
            "#top": ("ignore", ""),
            "?q=1": ("ignore", ""),
            "mailto:a@b": ("ignore", ""),
            "TEL:123": ("ignore", ""),
            "data:image/png;base64,AA": ("ignore", ""),
            "javascript:void(0)": ("ignore", ""),
            "https://cdn/x.js": ("external", ""),
            "http://cdn/x.js": ("external", ""),
            "//cdn/x.js": ("external", ""),
            "ftp://h/x": ("external", ""),
            "a%20b.css?v=2#frag": ("local", "a b.css"),
            "/root.css": ("local", "/root.css"),
        }
        for reference, expected in cases.items():
            with self.subTest(reference=reference):
                self.assertEqual(references_check.classify(reference), expected)


class HtmlTest(ReferencesTestCase):
    def test_every_referencing_attribute_resolves(self) -> None:
        markup = (
            '<link rel="stylesheet" href="a.css">\n'
            '<a href="img/x.png#frag">x</a>\n'
            '<script src="../top.js?v=1"></script>\n'
            '<img src="img/x.png"><source src="/site/img/x.png">\n'
            '<iframe src="dir/"></iframe><a href="dir">d</a>\n'
            '<a href="#top">t</a><a href="mailto:a@b">m</a><a href="">e</a>\n'
            '<img src="data:image/png;base64,AA" data-src="nope.png">\n'
            '<div data-href="nope.html"></div><a>no href</a>\n'
        )
        self.assertEqual(self.html_problems(markup), [])

    def test_missing_targets(self) -> None:
        markup = (
            '<link rel="icon" href="gone.ico">\n'
            '<a href="gone.html">x</a>\n'
            '<script src="gone.js"></script>\n'
            '<img src="gone.png">\n'
            '<source src="gone.webm">\n'
            '<iframe src="gone/"></iframe>\n'
            '<a href="/gone.html">r</a>\n'
        )
        self.assertEqual(
            self.html_problems(markup),
            [
                (1, "gone.ico does not exist"),
                (2, "gone.html does not exist"),
                (3, "gone.js does not exist"),
                (4, "gone.png does not exist"),
                (5, "gone.webm does not exist"),
                (6, "gone/ does not exist"),
                (7, "/gone.html does not exist"),
            ],
        )

    def test_directory_needs_index(self) -> None:
        self.assertEqual(
            self.html_problems('<a href="empty/">e</a>'),
            [(1, "empty/ is a directory with no index.html")],
        )

    def test_outside_root(self) -> None:
        self.assertEqual(
            self.html_problems('<a href="../../outside.html">o</a>'),
            [(1, "../../outside.html resolves outside --root")],
        )
        self.assertEqual(
            self.html_problems('<a href="/../outside.html">o</a>'),
            [(1, "/../outside.html resolves outside --root")],
        )

    def test_attribute_line_on_multiline_tag(self) -> None:
        markup = '<p>x</p>\n<img\n  alt="a"\n  src="gone.png">\n'
        self.assertEqual(self.html_problems(markup), [(4, "gone.png does not exist")])

    def test_base_element(self) -> None:
        problems = self.html_problems('<head><base href="/site/"></head>')
        self.assertEqual(len(problems), 1)
        self.assertIn("<base>", problems[0][1])

    def test_style_element_and_attribute(self) -> None:
        markup = (
            "<style>\n"
            "  /* url(commented.png) */\n"
            "  @import 'a.css';\n"
            '  .x { background: url("gone.png"); }\n'
            "</style>\n"
            '<div style="background: url(img/x.png)"></div>\n'
            "<div\n  style=\"background: url('missing.png')\"></div>\n"
            "<style></style>\n"
        )
        self.assertEqual(
            self.html_problems(markup),
            [(4, "gone.png does not exist"), (8, "missing.png does not exist")],
        )

    def test_external_stylesheet_and_script_need_sri(self) -> None:
        markup = (
            f'<link rel="stylesheet" href="https://cdn/a.css" {SRI}>\n'
            f'<script src="https://cdn/a.js" {SRI}></script>\n'
            '<link rel="stylesheet" href="https://cdn/b.css">\n'
            '<script src="//cdn/b.js" crossorigin></script>\n'
            '<script src="https://cdn/c.js" integrity="" crossorigin></script>\n'
            '<link rel="preload Stylesheet" href="http://cdn/d.css" integrity="x">\n'
            '<link rel="icon" href="https://cdn/i.ico">\n'
            '<link href="https://cdn/norel.css">\n'
            '<a href="https://example.com/">ext</a>\n'
            "<script>inline()</script>\n"
        )
        self.assertEqual(
            self.html_problems(markup),
            [
                (
                    3,
                    (
                        "external stylesheet https://cdn/b.css lacks integrity and "
                        "crossorigin"
                    ),
                ),
                (4, "external script //cdn/b.js lacks integrity"),
                (5, "external script https://cdn/c.js lacks integrity"),
                (6, "external stylesheet http://cdn/d.css lacks crossorigin"),
            ],
        )

    def test_local_script_needs_no_sri(self) -> None:
        self.assertEqual(self.html_problems('<script src="/top.js"></script>'), [])

    def test_no_external_refuses_every_load(self) -> None:
        markup = (
            f'<link rel="stylesheet" href="https://cdn/a.css" {SRI}>\n'
            '<script src="//cdn/a.js"></script>\n'
            '<img src="https://cdn/x.png">\n'
            '<link rel="icon" href="https://cdn/i.ico">\n'
            '<link rel="alternate stylesheet" href="https://cdn/b.css">\n'
            '<link href="https://cdn/norel.css">\n'
            '<p style="background: url(https://cdn/y.png)"></p>\n'
            '<a href="https://example.com/">ext</a>\n'
            '<link rel="Canonical" href="https://example.com/">\n'
            '<link rel="alternate" hreflang="cs" href="https://example.com/cs/">\n'
            '<img src="img/x.png"><img src="gone.png">\n'
        )
        page = self.write("site/page.html", markup)
        refused = "which --no-external refuses"
        self.assertEqual(
            references_check.check_html(page, self.root, no_external=True),
            [
                (1, f"external https://cdn/a.css, {refused}"),
                (2, f"external //cdn/a.js, {refused}"),
                (3, f"external https://cdn/x.png, {refused}"),
                (4, f"external https://cdn/i.ico, {refused}"),
                (5, f"external https://cdn/b.css, {refused}"),
                (6, f"external https://cdn/norel.css, {refused}"),
                (7, f"external https://cdn/y.png, {refused}"),
                (11, "gone.png does not exist"),
            ],
        )


class CssTest(ReferencesTestCase):
    def test_urls_and_imports(self) -> None:
        css = (
            '@charset "UTF-8";\n'
            '@import "a.css";\n'
            "@import url(gone.css);\n"
            "@IMPORT 'gone2.css' screen;\n"
            "/* url(x.png)\n   still a comment url(y.png) */\n"
            ".a { background: URL( 'img/x.png' ) }\n"
            '.b { background: url("/site/img/x.png#f"), url(data:image/png;b64,A) }\n'
            ".c { background: url(https://cdn/x.png) }\n"
            ".d { mask: url(missing.svg#icon) }\n"
            ".e { background: url() }\n"
        )
        self.assertEqual(
            self.css_problems(css),
            [
                (3, "gone.css does not exist"),
                (4, "gone2.css does not exist"),
                (10, "missing.svg does not exist"),
            ],
        )

    def test_no_external_refuses_urls_and_imports(self) -> None:
        sheet = self.write(
            "site/sheet.css",
            ".c { background: url(https://cdn/x.png) }\n"
            '@import "//cdn/y.css";\n'
            ".d { background: url(img/x.png) }\n",
        )
        refused = "which --no-external refuses"
        self.assertEqual(
            references_check.check_css(sheet, self.root, no_external=True),
            [
                (1, f"external https://cdn/x.png, {refused}"),
                (2, f"external //cdn/y.css, {refused}"),
            ],
        )

    def test_custom_function_ending_in_url_is_not_a_reference(self) -> None:
        self.assertEqual(self.css_problems(".a { x: my-url(gone.png) }"), [])


class MainTest(ReferencesTestCase):
    def test_clean(self) -> None:
        page = self.write("site/p.html", '<a href="a.css">a</a>')
        status, output = self.run_main(page, self.root / "site/a.css")
        self.assertEqual(status, 0)
        self.assertEqual(output, "checked 2 file(s): 0 problem(s)\n")

    def test_failures_are_reported_per_line(self) -> None:
        page = self.write("site/p.HTM", '\n<img src="gone.png">')
        sheet = self.write("site/s.css", "a { b: url(gone.svg) }")
        status, output = self.run_main(page, sheet)
        self.assertEqual(status, 1)
        self.assertEqual(
            output,
            f"{page}:2: gone.png does not exist\n"
            f"{sheet}:1: gone.svg does not exist\n"
            "checked 2 file(s): 2 problem(s)\n",
        )

    def test_no_external_option(self) -> None:
        page = self.write("site/p.html", '<img src="https://cdn/x.png">')
        sheet = self.write("site/s.css", "a { b: url(https://cdn/y.svg) }")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = references_check.main(
                ["--root", str(self.root), "--no-external", str(page), str(sheet)]
            )
        self.assertEqual(status, 1)
        self.assertEqual(
            stderr.getvalue(),
            f"{page}:1: external https://cdn/x.png, which --no-external refuses\n"
            f"{sheet}:1: external https://cdn/y.svg, which --no-external refuses\n"
            "checked 2 file(s): 2 problem(s)\n",
        )

    def test_runs_as_a_command(self) -> None:
        page = self.write("site/p.html", '<img src="gone.png">')
        result = subprocess.run(
            [
                str(tools_loader.TOOLS / "references-check"),
                *("--root", str(self.root), str(page)),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr,
            f"{page}:1: gone.png does not exist\nchecked 1 file(s): 1 problem(s)\n",
        )

    def test_not_utf8(self) -> None:
        page = self.write("site/p.html", b"<p>\xff</p>")
        status, output = self.run_main(page)
        self.assertEqual(status, 1)
        self.assertIn(f"{page}: not valid UTF-8", output)
        self.assertIn("1 problem(s)", output)

    def test_usage_errors(self) -> None:
        text = self.write("site/notes.txt", "")
        outside = self.enterContext(tempfile.NamedTemporaryFile(suffix=".html"))
        page = self.write("site/p.html", "")
        cases = {
            "is not a directory": ["--root", str(page), str(page)],
            "is not a file": ["--root", str(self.root), str(self.root / "no.html")],
            "neither HTML nor CSS": ["--root", str(self.root), str(text)],
            "is not inside --root": ["--root", str(self.root), outside.name],
            "required": [str(page)],
            "FILE": ["--root", str(self.root)],
        }
        for message, arguments in cases.items():
            with self.subTest(message=message):
                stderr = io.StringIO()
                with (
                    contextlib.redirect_stderr(stderr),
                    self.assertRaises(SystemExit) as caught,
                ):
                    references_check.main(arguments)
                self.assertEqual(caught.exception.code, 2)
                self.assertIn(message, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
