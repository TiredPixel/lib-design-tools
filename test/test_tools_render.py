"""Tests for tools/design_render.py and tools/render-check.

Chromium itself is replaced by a fake executable everywhere except the tests in
``RealChromiumTest``, which run only where ``chromium`` is on PATH.
"""

from __future__ import annotations

import contextlib
import html
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import urllib.error
import urllib.request

import design_render
import tools_loader

render_check = tools_loader.load("render-check")

CLEAN_DOM = '<html data-render-errors="[]"><head></head><body></body></html>'


def errors_dom(entries: list, body: str = "") -> str:
    value = html.escape(json.dumps(entries), quote=True)
    return f'<html data-render-errors="{value}"><body>{body}</body></html>'


class TempDirTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = pathlib.Path(directory.name)

    def write(self, name: str, content: str | bytes) -> pathlib.Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def fake_chromium(self, body: str) -> str:
        """Write an executable standing in for Chromium; return its path."""
        path = self.root / "bin" / "fake-chromium"
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys, time, urllib.request\n"
            + textwrap.dedent(body),
            encoding="utf-8",
        )
        path.chmod(0o755)
        return str(path)

    def printing_chromium(self, dom: str) -> str:
        return self.fake_chromium(f"sys.stdout.write({dom!r})\n")


class InjectCaptureTest(unittest.TestCase):
    def test_goes_first_inside_head(self) -> None:
        page = '<!DOCTYPE html><html><head lang="en"><title>t</title></head></html>'
        result = design_render.inject_capture(page)
        self.assertEqual(
            result,
            '<!DOCTYPE html><html><head lang="en">'
            + design_render.CAPTURE_SCRIPT
            + "<title>t</title></head></html>",
        )

    def test_head_tag_spanning_lines(self) -> None:
        page = '<html>\n<head\n  data-x="1">\n<meta charset="utf-8">'
        result = design_render.inject_capture(page)
        self.assertTrue(
            result.startswith(
                '<html>\n<head\n  data-x="1">' + design_render.CAPTURE_SCRIPT
            )
        )

    def test_ignores_header_and_commented_head(self) -> None:
        page = "<html><!-- <head> --><body><header>h</header></body></html>"
        result = design_render.inject_capture(page)
        self.assertEqual(
            result,
            "<html>" + design_render.CAPTURE_SCRIPT + page.removeprefix("<html>"),
        )

    def test_without_html_goes_after_doctype(self) -> None:
        page = "<!doctype html>\n<p>hello</p>"
        result = design_render.inject_capture(page)
        self.assertEqual(
            result,
            "<!doctype html>" + design_render.CAPTURE_SCRIPT + "\n<p>hello</p>",
        )

    def test_bare_fragment_gets_it_first(self) -> None:
        result = design_render.inject_capture("<p>hello</p>")
        self.assertEqual(result, design_render.CAPTURE_SCRIPT + "<p>hello</p>")

    def test_script_names_the_attribute(self) -> None:
        self.assertIn("data-render-errors", design_render.CAPTURE_SCRIPT)
        self.assertIn("addEventListener('error'", design_render.CAPTURE_SCRIPT)
        self.assertIn("}, true);", design_render.CAPTURE_SCRIPT)
        self.assertIn("unhandledrejection", design_render.CAPTURE_SCRIPT)
        self.assertIn("console.error =", design_render.CAPTURE_SCRIPT)


class ParseRenderErrorsTest(unittest.TestCase):
    def test_empty_list(self) -> None:
        self.assertEqual(design_render.parse_render_errors(CLEAN_DOM), [])

    def test_escaped_entries(self) -> None:
        entries = [{"kind": "console.error", "message": 'a "quoted" <b>'}]
        self.assertEqual(
            design_render.parse_render_errors(errors_dom(entries)), entries
        )

    def test_missing_attribute(self) -> None:
        with self.assertRaisesRegex(design_render.RenderError, "did not run"):
            design_render.parse_render_errors("<html><body></body></html>")

    def test_no_html_element(self) -> None:
        with self.assertRaisesRegex(design_render.RenderError, "did not run"):
            design_render.parse_render_errors("")

    def test_invalid_json(self) -> None:
        with self.assertRaisesRegex(design_render.RenderError, "not valid JSON"):
            design_render.parse_render_errors('<html data-render-errors="[">')

    def test_not_a_list(self) -> None:
        with self.assertRaisesRegex(design_render.RenderError, "not a JSON array"):
            design_render.parse_render_errors('<html data-render-errors="{}">')

    def test_only_first_html_element_counts(self) -> None:
        dom = '<html data-render-errors="[]"><html data-render-errors="[1]">'
        self.assertEqual(design_render.parse_render_errors(dom), [])


class DescribeErrorTest(unittest.TestCase):
    def test_resource(self) -> None:
        entry = {"kind": "resource", "tag": "img", "url": "http://x/a.png"}
        self.assertEqual(
            design_render.describe_error(entry), "failed to load <img> http://x/a.png"
        )

    def test_error_with_source(self) -> None:
        entry = {"kind": "error", "message": "boom", "source": "a.js", "line": 3}
        self.assertEqual(
            design_render.describe_error(entry), "uncaught error: boom (at a.js:3)"
        )

    def test_error_without_source(self) -> None:
        entry = {"kind": "error", "message": "boom", "source": "", "line": 0}
        self.assertEqual(design_render.describe_error(entry), "uncaught error: boom")

    def test_rejection_and_console(self) -> None:
        for kind in ("unhandledrejection", "console.error"):
            with self.subTest(kind=kind):
                entry = {"kind": kind, "message": "m"}
                self.assertEqual(design_render.describe_error(entry), f"{kind}: m")

    def test_unknown_kind(self) -> None:
        self.assertEqual(
            design_render.describe_error({"kind": "x"}),
            'unrecognised entry: {"kind": "x"}',
        )

    def test_not_a_dict(self) -> None:
        self.assertEqual(
            design_render.describe_error("text"), 'unrecognised entry: "text"'
        )


class ClassSelectorTest(unittest.TestCase):
    def test_simple_class(self) -> None:
        self.assertEqual(
            design_render.parse_class_selector(".load-error"), "load-error"
        )
        self.assertEqual(design_render.parse_class_selector(".-x_1"), "-x_1")

    def test_rejects_anything_richer(self) -> None:
        for selector in ("load-error", ".a.b", "div.a", "#id", ".", ".a b", ".1a"):
            with (
                self.subTest(selector=selector),
                self.assertRaisesRegex(ValueError, "unsupported selector"),
            ):
                design_render.parse_class_selector(selector)


class FindMarkedTest(unittest.TestCase):
    def test_matches_whole_class_tokens_only(self) -> None:
        dom = (
            "<html>\n"
            '<div class="card load-error">a</div>\n'
            '<p class="load-error-free">b</p>\n'
            '<span class="x">c</span><img class="load-error"/>\n'
            "<i>d</i><b class>e</b>\n"
        )
        self.assertEqual(
            design_render.find_marked(dom, "load-error"), [("div", 2), ("img", 4)]
        )

    def test_none(self) -> None:
        self.assertEqual(design_render.find_marked(CLEAN_DOM, "load-error"), [])


class ServeTest(TempDirTestCase):
    def fetch(self, url: str) -> tuple[bytes, str]:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.read(), response.headers["Content-Type"]

    def test_injects_into_html_only(self) -> None:
        self.write("pages/a b.html", "<html><head></head><body>é</body></html>")
        self.write("style.css", "a { color: red }")
        with design_render.serve(self.root) as base_url:
            self.assertRegex(base_url, r"^http://127\.0\.0\.1:\d+/$")
            page = self.root / "pages/a b.html"
            url = design_render.page_url(base_url, self.root, page)
            self.assertTrue(url.endswith("/pages/a%20b.html"))
            body, content_type = self.fetch(url)
            self.assertEqual(
                body.decode("utf-8"),
                "<html><head>"
                + design_render.CAPTURE_SCRIPT
                + "</head><body>é</body></html>",
            )
            self.assertEqual(content_type, "text/html; charset=utf-8")
            css, _ = self.fetch(base_url + "style.css")
            self.assertEqual(css, b"a { color: red }")
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.fetch(base_url + "missing.html")
            caught.exception.close()
            self.assertEqual(caught.exception.code, 404)

    def test_bytes_that_are_not_utf8_pass_through(self) -> None:
        self.write("latin.htm", b"<head></head><p>\xe9</p>")
        with design_render.serve(self.root) as base_url:
            body, _ = self.fetch(base_url + "latin.htm")
        expected = "<head>" + design_render.CAPTURE_SCRIPT + "</head><p>"
        self.assertEqual(body, expected.encode("utf-8") + b"\xe9</p>")

    def test_page_outside_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "not inside the served root"):
            design_render.page_url("http://h/", self.root / "sub", self.root / "a.html")


class DumpDomTest(TempDirTestCase):
    def test_passes_flags_and_returns_stdout(self) -> None:
        chromium = self.fake_chromium(
            """
            arguments = sys.argv[1:]
            profile = next(a for a in arguments if a.startswith("--user-data-dir="))
            record = {"arguments": arguments,
                      "profile_exists": os.path.isdir(profile.split("=", 1)[1])}
            pathlib.Path(sys.argv[0]).with_name("record.json").write_text(
                json.dumps(record))
            sys.stdout.write("<html>dom</html>")
            """
        )
        dom = design_render.dump_dom(
            "http://x/p.html", chromium=chromium, budget_ms=1234
        )
        self.assertEqual(dom, "<html>dom</html>")
        record = json.loads((self.root / "bin" / "record.json").read_text())
        self.assertTrue(record["profile_exists"])
        arguments = record["arguments"]
        self.assertEqual(
            arguments[:3], ["--headless", "--no-sandbox", "--disable-dev-shm-usage"]
        )
        self.assertEqual(
            arguments[4:],
            ["--virtual-time-budget=1234", "--dump-dom", "http://x/p.html"],
        )
        profile = arguments[3].split("=", 1)[1]
        self.assertFalse(pathlib.Path(profile).exists())

    def test_non_zero_exit(self) -> None:
        chromium = self.fake_chromium("sys.stderr.write('crashed\\n')\nsys.exit(3)\n")
        with self.assertRaisesRegex(design_render.RenderError, "exited 3 .*crashed"):
            design_render.dump_dom("http://x/", chromium=chromium)

    def test_non_zero_exit_without_stderr(self) -> None:
        chromium = self.fake_chromium("sys.exit(4)\n")
        with self.assertRaisesRegex(design_render.RenderError, "no output on stderr"):
            design_render.dump_dom("http://x/", chromium=chromium)

    def test_timeout(self) -> None:
        chromium = self.fake_chromium("time.sleep(30)\n")
        with self.assertRaisesRegex(design_render.RenderError, "within 0.5s"):
            design_render.dump_dom("http://x/", chromium=chromium, timeout_s=0.5)

    def test_missing_executable(self) -> None:
        with self.assertRaisesRegex(design_render.RenderError, "cannot run"):
            design_render.dump_dom("http://x/", chromium=str(self.root / "none"))


class PrintPdfTest(TempDirTestCase):
    def test_passes_flags_and_returns_the_pdf(self) -> None:
        chromium = self.fake_chromium(
            """
            arguments = sys.argv[1:]
            pathlib.Path(sys.argv[0]).with_name("record.json").write_text(
                json.dumps(arguments))
            out = next(a for a in arguments if a.startswith("--print-to-pdf="))
            pathlib.Path(out.split("=", 1)[1]).write_bytes(b"%PDF-1.4 fake")
            """
        )
        pdf = design_render.print_pdf("http://x/p.html", chromium=chromium)
        self.assertEqual(pdf, b"%PDF-1.4 fake")
        arguments = json.loads((self.root / "bin" / "record.json").read_text())
        self.assertIn("--no-pdf-header-footer", arguments)
        self.assertEqual(arguments[-1], "http://x/p.html")

    def test_no_pdf_written(self) -> None:
        chromium = self.fake_chromium("pass\n")
        with self.assertRaisesRegex(design_render.RenderError, "printed no PDF"):
            design_render.print_pdf("http://x/", chromium=chromium)

    def test_failure_is_a_render_error(self) -> None:
        chromium = self.fake_chromium("sys.exit(2)\n")
        with self.assertRaisesRegex(design_render.RenderError, "exited 2"):
            design_render.print_pdf("http://x/", chromium=chromium)


class SheetCountTest(unittest.TestCase):
    def test_counts_page_objects_not_the_page_tree(self) -> None:
        pdf = b"<< /Type /Pages /Kids [] >> << /Type /Page >> << /Type/Page/X >>"
        self.assertEqual(design_render.sheet_count(pdf), 2)

    def test_none(self) -> None:
        self.assertEqual(design_render.sheet_count(b"%PDF"), 0)


class RenderTest(TempDirTestCase):
    def test_render_through_server(self) -> None:
        page = self.write("p.html", "<html><head></head><body><b>x</b></body></html>")
        chromium = self.fake_chromium(
            f"""
            page = urllib.request.urlopen(sys.argv[-1]).read().decode()
            assert {design_render.CAPTURE_SCRIPT!r} in page
            sys.stdout.write(page.replace("<html>", '<html data-render-errors="[]">'))
            """
        )
        result = design_render.render(self.root, page, chromium=chromium)
        self.assertEqual(result.errors, [])
        self.assertIn("<b>x</b>", result.dom)
        self.assertTrue(result.url.endswith("/p.html"))

    def test_render_url_reports_errors(self) -> None:
        entries = [{"kind": "console.error", "message": "m"}]
        chromium = self.printing_chromium(errors_dom(entries))
        result = design_render.render_url("http://x/", chromium=chromium)
        self.assertEqual(result.errors, entries)


class RenderCheckTest(TempDirTestCase):
    def run_main(self, *arguments: str) -> tuple[int, str]:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = render_check.main(list(arguments))
        return status, stderr.getvalue()

    def run_usage_error(self, *arguments: str) -> str:
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as caught,
        ):
            render_check.main(list(arguments))
        self.assertEqual(caught.exception.code, 2)
        return stderr.getvalue()

    def test_clean_pages(self) -> None:
        self.write("a.html", "<p>a</p>")
        self.write("b/c.html", "<p>c</p>")
        chromium = self.printing_chromium(CLEAN_DOM)
        status, output = self.run_main(
            "--root",
            str(self.root),
            "--error-marker",
            ".load-error",
            "--chromium",
            chromium,
            str(self.root / "a.html"),
            str(self.root / "b/c.html"),
        )
        self.assertEqual(status, 0)
        self.assertEqual(output, "checked 2 page(s): 0 problem(s)\n")

    def test_captured_errors_fail(self) -> None:
        page = self.write("a.html", "<p>a</p>")
        entries = [
            {"kind": "resource", "tag": "link", "url": "http://h/x.css"},
            {"kind": "unhandledrejection", "message": "nope"},
        ]
        chromium = self.printing_chromium(errors_dom(entries))
        status, output = self.run_main(
            "--root", str(self.root), "--chromium", chromium, str(page)
        )
        self.assertEqual(status, 1)
        self.assertEqual(
            output,
            f"{page}: failed to load <link> http://h/x.css\n"
            f"{page}: unhandledrejection: nope\n"
            "checked 1 page(s): 2 problem(s)\n",
        )

    def test_error_marker_fails(self) -> None:
        page = self.write("a.html", "<p>a</p>")
        dom = errors_dom([], '\n<div class="chip load-error">x</div>')
        chromium = self.printing_chromium(dom)
        status, output = self.run_main(
            "--root",
            str(self.root),
            "--error-marker",
            ".load-error",
            "--chromium",
            chromium,
            str(page),
        )
        self.assertEqual(status, 1)
        self.assertIn(
            f"{page}: rendered <div> matching .load-error (rendered DOM line 2)\n",
            output,
        )

    def test_marker_ignored_without_option(self) -> None:
        page = self.write("a.html", "<p>a</p>")
        chromium = self.printing_chromium(errors_dom([], '<i class="load-error">'))
        status, _ = self.run_main(
            "--root", str(self.root), "--chromium", chromium, str(page)
        )
        self.assertEqual(status, 0)

    def test_chromium_failure_fails_page(self) -> None:
        page = self.write("a.html", "<p>a</p>")
        chromium = self.fake_chromium("sys.exit(1)\n")
        status, output = self.run_main(
            "--root", str(self.root), "--chromium", chromium, str(page)
        )
        self.assertEqual(status, 1)
        self.assertIn(f"{page}: ", output)
        self.assertIn("exited 1", output)

    def test_timeout_option(self) -> None:
        page = self.write("a.html", "<p>a</p>")
        chromium = self.fake_chromium("time.sleep(30)\n")
        status, output = self.run_main(
            "--root",
            str(self.root),
            "--chromium",
            chromium,
            "--timeout-s",
            "0.5",
            "--budget-ms",
            "100",
            str(page),
        )
        self.assertEqual(status, 1)
        self.assertIn("within 0.5s", output)

    def test_missing_capture_fails_page(self) -> None:
        page = self.write("a.html", "<p>a</p>")
        chromium = self.printing_chromium("<html><body></body></html>")
        status, output = self.run_main(
            "--root", str(self.root), "--chromium", chromium, str(page)
        )
        self.assertEqual(status, 1)
        self.assertIn("capture script did not run", output)

    def test_usage_errors(self) -> None:
        page = self.write("a.html", "<p>a</p>")
        outside = self.enterContext(tempfile.NamedTemporaryFile(suffix=".html"))
        chromium = self.printing_chromium(CLEAN_DOM)
        root = str(self.root)
        cases = {
            "unsupported selector": ["--error-marker", "div"],
            "must be positive": ["--budget-ms", "0"],
            "is not a directory": ["--root", str(page)],
            "is not a file": [str(self.root / "missing.html")],
            "is not inside --root": [outside.name],
            "cannot find Chromium": ["--chromium", str(self.root / "none")],
        }
        for message, extra in cases.items():
            with self.subTest(message=message):
                arguments = ["--root", root, "--chromium", chromium, str(page)]
                if extra[0].startswith("--"):
                    arguments += extra
                else:
                    arguments.append(extra[0])
                self.assertIn(message, self.run_usage_error(*arguments))

    def test_requires_files(self) -> None:
        self.assertIn("FILE", self.run_usage_error("--root", str(self.root)))

    def test_runs_as_a_command(self) -> None:
        page = self.write("a.html", "<p>a</p>")
        chromium = self.printing_chromium(CLEAN_DOM)
        result = subprocess.run(
            [
                str(tools_loader.TOOLS / "render-check"),
                *("--root", str(self.root), "--chromium", chromium, str(page)),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "checked 1 page(s): 0 problem(s)\n")


@unittest.skipUnless(
    shutil.which(design_render.DEFAULT_CHROMIUM), "needs chromium on PATH"
)
class RealChromiumTest(TempDirTestCase):
    def render(self, body: str, head: str = "") -> design_render.Render:
        page = self.write(
            "page.html",
            '<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
            f"{head}</head><body>{body}</body></html>\n",
        )
        return design_render.render(self.root, page)

    def kinds(self, result: design_render.Render) -> list[str]:
        return [entry["kind"] for entry in result.errors]

    def test_clean_page_with_script_built_dom(self) -> None:
        self.write("ok.css", "body { color: black }")
        result = self.render(
            "<div id=out></div><script>"
            "document.getElementById('out').innerHTML = '<i class=chip>1</i>';"
            "</script>",
            head='<link rel="stylesheet" href="ok.css">',
        )
        self.assertEqual(result.errors, [])
        self.assertIn('<i class="chip">1</i>', result.dom)

    def test_failed_loads(self) -> None:
        result = self.render(
            '<img src="missing.png">',
            head='<link rel="stylesheet" href="/missing.css">'
            '<script src="missing.js"></script>',
        )
        self.assertEqual(self.kinds(result), ["resource"] * 3)
        self.assertEqual(
            sorted(entry["tag"] for entry in result.errors), ["img", "link", "script"]
        )

    def test_script_errors(self) -> None:
        cases = {
            "error": "throw new Error('boom');",
            "unhandledrejection": "Promise.reject(new Error('boom'));",
            "console.error": "console.error('boom', {a: 1});",
        }
        for kind, script in cases.items():
            with self.subTest(kind=kind):
                result = self.render(f"<script>{script}</script>")
                self.assertEqual(self.kinds(result), [kind])
                self.assertIn("boom", result.errors[0]["message"])

    def test_render_check_marker(self) -> None:
        page = self.write("m.html", '<html><body><p class="load-error">x</p>')
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = render_check.main(
                ["--root", str(self.root), "--error-marker", ".load-error", str(page)]
            )
        self.assertEqual(status, 1)
        self.assertIn("matching .load-error", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
