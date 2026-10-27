"""Tests for tools/references-check."""

from __future__ import annotations

import contextlib
import io
import pathlib
import subprocess
import tempfile
import unittest

import temp_tree
import tools_loader

references_check = tools_loader.load("references-check")

SRI = 'integrity="sha384-abc" crossorigin="anonymous"'


class ReferencesTestCase(temp_tree.TempTreeTestCase):
    def setUp(self) -> None:
        super().setUp()
        for name in ("site/a.css", "site/img/x.png", "top.js", "site/dir/index.html"):
            self.write(name, "")
        (self.root / "site/empty").mkdir()

    def run_main(self, *arguments: str) -> tuple[int, str]:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = references_check.main(list(arguments))
        return status, stderr.getvalue()

    def run_usage_error(self, *arguments: str) -> str:
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as caught,
        ):
            references_check.main(list(arguments))
        self.assertEqual(caught.exception.code, 2)
        return stderr.getvalue()

    def html_problems(
        self, markup: str, require_integrity: bool = False, no_external: bool = False
    ) -> list[tuple[int, str]]:
        page = self.write("site/page.html", markup)
        return references_check.check_html(
            page,
            self.root,
            no_external=no_external,
            require_integrity=require_integrity,
        )

    def css_problems(
        self, css: str, no_external: bool = False
    ) -> list[tuple[int, str]]:
        sheet = self.write("site/sheet.css", css)
        return references_check.check_css(sheet, self.root, no_external=no_external)


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
            "about:blank": ("ignore", ""),
            "blob:https://example.com/0f6e": ("ignore", ""),
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


class RefreshUrlTest(unittest.TestCase):
    def test_refresh_url(self) -> None:
        cases = {
            "": "",
            "5": "",
            " 5 ; ": "",
            "0x; url=a.html": "",
            "soon; url=a.html": "",
            "0; url=a.html": "a.html",
            "0;\n URL = 'a b.html' x": "a b.html",
            '0 "a.html"': "a.html",
            "0;url='a.html": "a.html",
            ".5,a.html": "a.html",
            "1.5.0 a.html": "a.html",
            "0; uploads/a.html": "uploads/a.html",
            "0; url a.html": "url a.html",
            "0; u'a.html'": "u'a.html'",
        }
        for content, expected in cases.items():
            with self.subTest(content=content):
                self.assertEqual(references_check.refresh_url(content), expected)


class SrcsetUrlsTest(unittest.TestCase):
    def test_srcset_urls(self) -> None:
        cases = {
            "": [],
            " , ,": [],
            "a.png": ["a.png"],
            "a.png 1x, b.png 2x": ["a.png", "b.png"],
            "a.png,b.png 2x": ["a.png,b.png"],
            "a.png,, b.png": ["a.png", "b.png"],
            "a.png 100w,\n  b.png 200w": ["a.png", "b.png"],
            "data:image/png;base64,AA 1x, b.png 2x": [
                "data:image/png;base64,AA",
                "b.png",
            ],
            "a.png calc(1, 2) 1x, b.png": ["a.png", "b.png"],
        }
        for srcset, expected in cases.items():
            with self.subTest(srcset=srcset):
                self.assertEqual(list(references_check.srcset_urls(srcset)), expected)


class CssUnescapeTest(unittest.TestCase):
    def test_unreadable_escapes_become_the_replacement_character(self) -> None:
        for escape in ("\\0", "\\110000", "\\d800"):
            with self.subTest(escape=escape):
                self.assertEqual(
                    references_check._css_unescape(f"a{escape} b"), "a\ufffdb"
                )


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

    def test_dot_dot_is_taken_against_the_url_not_a_symlink_target(self) -> None:
        self.write("site/assets/docs/page.html", '<img src="../image.svg">\n')
        (self.root / "site/docs").symlink_to("assets/docs")
        page = self.root / "site/docs/page.html"
        self.write("site/image.svg", "")
        self.assertEqual(references_check.check_html(page, self.root), [])
        (self.root / "site/image.svg").unlink()
        self.write("site/assets/image.svg", "")
        self.assertEqual(
            references_check.check_html(page, self.root),
            [(1, "../image.svg does not exist")],
        )

    def test_a_symlink_out_of_the_root_resolves_outside_it(self) -> None:
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (pathlib.Path(outside.name) / "x.png").write_bytes(b"")
        (self.root / "site/out.png").symlink_to(pathlib.Path(outside.name) / "x.png")
        self.assertEqual(
            self.html_problems('<img src="out.png">'),
            [(1, "out.png resolves outside --root")],
        )

    def test_every_loading_attribute_is_read(self) -> None:
        markup = (
            '<img srcset="gone-1.png 1x, img/x.png 2x">\n'
            '<picture><source srcset="gone-2.webp"></picture>\n'
            '<video src="gone-3.mp4" poster="gone-4.png">\n'
            '<track src="gone-5.vtt"></video>\n'
            '<audio src="gone-6.ogg"></audio>\n'
            '<embed src="gone-7.svg"><object data="gone-8.svg"></object>\n'
            '<input type="Image" src="gone-9.png">\n'
            '<input type="text" src="ignored.png">\n'
            '<svg><use href="gone-10.svg#a"></use>'
            '<image xlink:href="gone-11.png"></image></svg>\n'
        )
        self.assertEqual(
            self.html_problems(markup),
            [
                (1, "gone-1.png does not exist"),
                (2, "gone-2.webp does not exist"),
                (3, "gone-3.mp4 does not exist"),
                (3, "gone-4.png does not exist"),
                (4, "gone-5.vtt does not exist"),
                (5, "gone-6.ogg does not exist"),
                (6, "gone-7.svg does not exist"),
                (6, "gone-8.svg does not exist"),
                (7, "gone-9.png does not exist"),
                (9, "gone-10.svg does not exist"),
                (9, "gone-11.png does not exist"),
            ],
        )

    def test_areas_image_srcsets_and_refreshes_are_read(self) -> None:
        markup = (
            '<map><area href="gone-1.html"><area href="dir/"></map>\n'
            '<link rel="preload" imagesrcset="gone-2.png 1x, img/x.png 2x">\n'
            '<meta http-equiv="Refresh" content="0; url=gone-3.html">\n'
            '<meta http-equiv="refresh" content="5">\n'
            '<meta name="description" content="gone.html">\n'
            '<meta content="0;img/x.png" http-equiv="refresh">\n'
            '<meta http-equiv="refresh" content="soon; url=gone.html">\n'
            "<div style=\"background: image-set('gone-4.png' 1x)\"></div>\n"
        )
        self.assertEqual(
            self.html_problems(markup),
            [
                (1, "gone-1.html does not exist"),
                (2, "gone-2.png does not exist"),
                (3, "gone-3.html does not exist"),
                (8, "gone-4.png does not exist"),
            ],
        )

    def test_attribute_line_on_multiline_tag(self) -> None:
        markup = '<p>x</p>\n<img\n  alt="a"\n  src="gone.png">\n'
        self.assertEqual(self.html_problems(markup), [(4, "gone.png does not exist")])

    def test_attribute_line_skips_names_inside_other_values(self) -> None:
        markup = '<img alt="see src=below"\n  src="gone.png">\n'
        self.assertEqual(self.html_problems(markup), [(2, "gone.png does not exist")])

    def test_attribute_line_of_a_repeated_attribute_is_the_last(self) -> None:
        markup = '<img src="img/x.png"\n  src="gone.png">\n'
        self.assertEqual(self.html_problems(markup), [(2, "gone.png does not exist")])

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

    def test_external_loads_pass_without_require_integrity(self) -> None:
        markup = (
            '<link rel="stylesheet" href="https://cdn/b.css">\n'
            '<script src="//cdn/b.js"></script>\n'
        )
        self.assertEqual(self.html_problems(markup), [])

    def test_require_integrity_checks_external_stylesheets_and_scripts(self) -> None:
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
            self.html_problems(markup, require_integrity=True),
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

    def test_local_script_needs_no_integrity(self) -> None:
        self.assertEqual(
            self.html_problems(
                '<script src="/top.js"></script>', require_integrity=True
            ),
            [],
        )

    def test_no_external_refuses_every_load(self) -> None:
        markup = (
            '<link rel="stylesheet" href="https://cdn/a.css">\n'
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
            '<video src="https://cdn/v.mp4"></video>\n'
            '<img srcset="img/x.png 1x, https://cdn/x2.png 2x">\n'
        )
        refused = "which --no-external refuses"
        self.assertEqual(
            self.html_problems(markup, no_external=True),
            [
                (1, f"external https://cdn/a.css, {refused}"),
                (2, f"external //cdn/a.js, {refused}"),
                (3, f"external https://cdn/x.png, {refused}"),
                (4, f"external https://cdn/i.ico, {refused}"),
                (5, f"external https://cdn/b.css, {refused}"),
                (6, f"external https://cdn/norel.css, {refused}"),
                (7, f"external https://cdn/y.png, {refused}"),
                (11, "gone.png does not exist"),
                (12, f"external https://cdn/v.mp4, {refused}"),
                (13, f"external https://cdn/x2.png, {refused}"),
            ],
        )
        # A load refused outright is not reported again for lacking integrity.
        self.assertEqual(
            self.html_problems(markup, no_external=True, require_integrity=True),
            self.html_problems(markup, no_external=True),
        )

    def test_no_external_reads_areas_image_srcsets_refreshes_and_image_sets(
        self,
    ) -> None:
        markup = (
            '<map><area href="https://example.com/"></map>\n'
            '<meta http-equiv="refresh" content="0; url=https://example.com/">\n'
            '<link rel="preload" as="image" '
            'imagesrcset="img/x.png 1x, https://cdn/x2.png 2x">\n'
            '<iframe src="about:blank"></iframe>\n'
            '<img src="blob:https://example.com/0f6e">\n'
            "<p style=\"background: image-set('https://cdn/s.png' 1x)\"></p>\n"
        )
        refused = "which --no-external refuses"
        self.assertEqual(
            self.html_problems(markup, no_external=True),
            [
                (3, f"external https://cdn/x2.png, {refused}"),
                (6, f"external https://cdn/s.png, {refused}"),
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
        css = (
            ".c { background: url(https://cdn/x.png) }\n"
            '@import "//cdn/y.css";\n'
            ".d { background: url(img/x.png) }\n"
            '.e { background: image-set("https://cdn/z.png" 1x) }\n'
        )
        refused = "which --no-external refuses"
        self.assertEqual(
            self.css_problems(css, no_external=True),
            [
                (1, f"external https://cdn/x.png, {refused}"),
                (2, f"external //cdn/y.css, {refused}"),
                (4, f"external https://cdn/z.png, {refused}"),
            ],
        )

    def test_image_set_strings(self) -> None:
        css = (
            ".a { background: image-set('gone-1.png' 1x, \"img/x.png\" 2x) }\n"
            ".b { background: -webkit-image-set(url(gone-2.png) 1x,"
            " url('img/x.png') 2x) }\n"
            ".c { background: IMAGE-SET(\n"
            "  'img/x.png' type(\"image/avif\"),\n"
            "  'gone-3.png' type('image/png')) }\n"
            ".d { content: 'gone.png'; x: my-image-set('gone.png') }\n"
            "/* image-set('gone.png') */\n"
            ".e { background: image-set('img/x.png?a)' 1x, 'gone-4.png' 2x) }\n"
            ".f { background: image-set('never closed }\n"
        )
        self.assertEqual(
            self.css_problems(css),
            [
                (1, "gone-1.png does not exist"),
                (2, "gone-2.png does not exist"),
                (5, "gone-3.png does not exist"),
                (8, "gone-4.png does not exist"),
            ],
        )

    def test_custom_function_ending_in_url_is_not_a_reference(self) -> None:
        self.assertEqual(self.css_problems(".a { x: my-url(gone.png) }"), [])

    def test_strings_hold_no_references_or_comments(self) -> None:
        css = (
            '.a::before { content: "url(gone-1.png)"; }\n'
            ".b::before { content: '/*'; }\n"
            ".c { background: url(gone-2.png) }\n"
            '.d::after { content: "*/"; }\n'
            ".e::after { content: 'it\\'s url(gone-3.png)'; }\n"
            ".f::after { content: \"@import 'gone-4.css'\"; }\n"
            ".g::after { content: \"image-set('gone-5.png' 1x)\"; }\n"
            ".h { background: url(img/x.png) }\n"
        )
        self.assertEqual(self.css_problems(css), [(3, "gone-2.png does not exist")])

    def test_escapes_in_strings_are_read(self) -> None:
        css = (
            '.a { background: url("img/\\78.png") }\n'
            '.b { background: url("gone\\2d 6.png") }\n'
            ".c { background: url('img/\\\nx.png') }\n"
            ".d { background: url(gone-7.png) }\n"
        )
        self.assertEqual(
            self.css_problems(css),
            [(2, "gone-6.png does not exist"), (5, "gone-7.png does not exist")],
        )

    def test_an_unquoted_url_is_read_whole(self) -> None:
        css = (
            ".a { background: url(gone/*x*/.png) }\n"
            ".b { background: url(gone-8.png) }\n"
        )
        self.assertEqual(
            self.css_problems(css),
            [(1, "gone/*x*/.png does not exist"), (2, "gone-8.png does not exist")],
        )

    def test_escapes_in_unquoted_urls_are_read(self) -> None:
        self.write("site/img/a b.png", "")
        self.write("site/img/a).png", "")
        css = (
            ".a { background: url(img/\\78.png) }\n"
            ".b { background: url(img/a\\ b.png) }\n"
            ".c { background: url(img/a\\).png) }\n"
            ".d { background: url(  gone\\ 10.png  ) }\n"
            '.e { background: url(gone-11\\".png) }\n'
        )
        self.assertEqual(
            self.css_problems(css),
            [(4, "gone 10.png does not exist"), (5, 'gone-11".png does not exist')],
        )

    def test_an_invalid_unquoted_url_is_dropped_whole(self) -> None:
        css = (
            ".a { background: url(gone a.png) }\n"
            ".b { background: url(gone'b.png) }\n"
            ".c { background: url(gone(c.png) }\n"
            ".d { background: url(gone\\\nd.png) }\n"
            ".e { background: url(gone-12.png) }\n"
            ".f { background: url(gone-13.png"
        )
        self.assertEqual(self.css_problems(css), [(6, "gone-12.png does not exist")])

    def test_a_string_a_newline_ends_unclosed_is_dropped(self) -> None:
        css = ".a { content: 'never closed\n.b { background: url(gone-9.png) }\n"
        self.assertEqual(self.css_problems(css), [(2, "gone-9.png does not exist")])


class MainTest(ReferencesTestCase):
    def test_clean(self) -> None:
        page = self.write("site/p.html", '<a href="a.css">a</a>')
        status, output = self.run_main(
            "--root", str(self.root), str(page), str(self.root / "site/a.css")
        )
        self.assertEqual(status, 0)
        self.assertEqual(output, "checked 2 file(s): 0 problem(s)\n")

    def test_failures_are_reported_per_line(self) -> None:
        page = self.write("site/p.HTM", '\n<img src="gone.png">')
        sheet = self.write("site/s.css", "a { b: url(gone.svg) }")
        status, output = self.run_main("--root", str(self.root), str(page), str(sheet))
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
        status, output = self.run_main(
            "--root", str(self.root), "--no-external", str(page), str(sheet)
        )
        self.assertEqual(status, 1)
        self.assertEqual(
            output,
            f"{page}:1: external https://cdn/x.png, which --no-external refuses\n"
            f"{sheet}:1: external https://cdn/y.svg, which --no-external refuses\n"
            "checked 2 file(s): 2 problem(s)\n",
        )

    def test_require_integrity_option(self) -> None:
        page = self.write("site/p.html", '<script src="https://cdn/x.js"></script>')
        self.assertEqual(self.run_main("--root", str(self.root), str(page))[0], 0)
        status, output = self.run_main(
            "--root", str(self.root), "--require-integrity", str(page)
        )
        self.assertEqual(status, 1)
        self.assertEqual(
            output,
            f"{page}:1: external script https://cdn/x.js lacks integrity and "
            "crossorigin\n"
            "checked 1 file(s): 1 problem(s)\n",
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
        status, output = self.run_main("--root", str(self.root), str(page))
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
                self.assertIn(message, self.run_usage_error(*arguments))


if __name__ == "__main__":
    unittest.main()
