"""Tests for tools/design_render.py and tools/render-check.

Chromium itself is replaced by a fake executable everywhere except the tests in
``RealChromiumTest``, which run only where ``chromium`` is on PATH.
"""

from __future__ import annotations

import contextlib
import html
import io
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest
import unittest.mock
import urllib.error
import urllib.request

import design_render
import temp_tree
import tools_loader

render_check = tools_loader.load("render-check")

CLEAN_DOM = '<html data-render-errors="[]"><head></head><body></body></html>'

# For a fake Chromium: start a child in its process group that keeps writing files
# into the profile, as Chromium's renderer and crashpad processes do, and record its
# pid beside the fake once it has started writing.
SPAWN_PROFILE_WRITER = """
profile = next(
    a for a in sys.argv[1:] if a.startswith("--user-data-dir=")
).split("=", 1)[1]
writer = (
    "import itertools, pathlib, sys, time\\n"
    "directory = pathlib.Path(sys.argv[1]) / 'Default'\\n"
    "directory.mkdir()\\n"
    "for n in itertools.count():\\n"
    "    (directory / str(n)).write_text('x', encoding='utf-8')\\n"
    "    time.sleep(0.001)\\n"
)
import subprocess
child = subprocess.Popen(
    [sys.executable, "-c", writer, profile],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
while not (pathlib.Path(profile) / "Default").is_dir():
    time.sleep(0.01)
pathlib.Path(sys.argv[0]).with_name("child.pid").write_text(str(child.pid), encoding="utf-8")
"""


def errors_dom(entries: list, body: str = "") -> str:
    value = html.escape(json.dumps(entries), quote=True)
    return f'<html data-render-errors="{value}"><body>{body}</body></html>'


class TempDirTestCase(temp_tree.TempTreeTestCase):
    def beside_fake(self, name: str) -> pathlib.Path:
        return self.root / "bin" / name

    def fake_chromium(self, body: str) -> str:
        """Write an executable standing in for Chromium; return its path."""
        path = self.beside_fake("fake-chromium")
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

    def assert_writer_gone(self) -> None:
        pid = int(self.beside_fake("child.pid").read_text(encoding="utf-8"))
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def run_main(self, *arguments: str) -> tuple[int, str]:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = render_check.main(list(arguments))
        return status, stderr.getvalue()


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

    def test_without_html_goes_after_the_first_doctype(self) -> None:
        page = "<!DOCTYPE html><!doctype x>\n<p>hello</p>"
        result = design_render.inject_capture(page)
        self.assertEqual(
            result,
            "<!DOCTYPE html>"
            + design_render.CAPTURE_SCRIPT
            + "<!doctype x>\n<p>hello</p>",
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

    def test_response(self) -> None:
        entry = {
            "kind": "response",
            "initiator": "css",
            "status": 404,
            "url": "http://x/f.woff2",
        }
        self.assertEqual(
            design_render.describe_error(entry),
            "failed to load http://x/f.woff2 (HTTP 404, started by css)",
        )

    def test_unsupported(self) -> None:
        entry = {"kind": "unsupported", "message": "no responseStatus"}
        self.assertEqual(
            design_render.describe_error(entry), "not checked: no responseStatus"
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


class ParseClassSelectorTest(unittest.TestCase):
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

    def assert_not_found(self, url: str, method: str = "GET") -> None:
        request = urllib.request.Request(url, method=method)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        caught.exception.close()
        self.assertEqual(caught.exception.code, 404)

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
            self.assert_not_found(base_url + "missing.html")

    def test_head_of_a_file_that_is_not_html_has_no_body(self) -> None:
        self.write("style.css", "a { color: red }")
        with design_render.serve(self.root) as base_url:
            request = urllib.request.Request(base_url + "style.css", method="HEAD")
            with urllib.request.urlopen(request, timeout=10) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Content-Length"], "16")
                self.assertEqual(response.read(), b"")

    def test_bytes_that_are_not_utf8_pass_through(self) -> None:
        self.write("latin.htm", b"<head></head><p>\xe9</p>")
        with design_render.serve(self.root) as base_url:
            body, _ = self.fetch(base_url + "latin.htm")
        expected = "<head>" + design_render.CAPTURE_SCRIPT + "</head><p>"
        self.assertEqual(body, expected.encode("utf-8") + b"\xe9</p>")

    def test_refuses_dot_segments_and_symlinks_leaving_the_tree(self) -> None:
        self.write(".env", "secret")
        self.write(".git/config", "secret")
        self.write("a.css", "a {}")
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (pathlib.Path(outside.name) / "x.css").write_text("secret", encoding="utf-8")
        (self.root / "public").symlink_to(".env")
        (self.root / "git").symlink_to(".git")
        (self.root / "out.css").symlink_to(pathlib.Path(outside.name) / "x.css")
        (self.root / "alias.css").symlink_to("a.css")
        (self.root / "secret").mkdir()
        (self.root / "secret/index.html").symlink_to("../.env")
        (self.root / "pages").mkdir()
        (self.root / "pages/index.html").symlink_to("../a.css")
        with design_render.serve(self.root) as base_url:
            for path in (
                ".env",
                "%2Egit/config",
                "public",
                "git/config",
                "out.css",
                "secret/",
            ):
                for method in ("GET", "HEAD"):
                    with self.subTest(path=path, method=method):
                        self.assert_not_found(base_url + path, method)
            self.assertEqual(self.fetch(base_url + "alias.css")[0], b"a {}")
            self.assertEqual(self.fetch(base_url + "pages/")[0], b"a {}")

    def test_refused(self) -> None:
        self.write("a.css", "")
        root = str(self.root)
        cases = {
            ("/a.css", self.root / "a.css"): False,
            ("/", self.root): False,
            ("/.env", self.root / ".env"): True,
            ("/x/%2e%2e/a.css", self.root / "a.css"): True,
            ("/b.css", self.root.parent / "b.css"): True,
        }
        for (url, path), expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(design_render.refused(root, url, str(path)), expected)


class ServedPathTest(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.site = self.root / "site"
        self.write("site/src/index.html", "<p>i</p>")
        (self.site / "pages").mkdir()

    def link(self, path: pathlib.Path, target: str) -> pathlib.Path:
        path.symlink_to(target)
        return path

    def test_symlinked_page_keeps_its_own_path(self) -> None:
        page = self.link(self.site / "pages/index.html", "../src/index.html")
        self.assertEqual(
            design_render.page_url("http://h/", self.site, page),
            "http://h/pages/index.html",
        )

    def test_symlinked_directory_keeps_its_own_path(self) -> None:
        directory = self.link(self.site / "docs", "src")
        self.assertEqual(
            design_render.served_path(self.site, directory / "index.html"),
            "docs/index.html",
        )

    def test_root_named_through_symlink(self) -> None:
        root = self.link(self.root / "alias", "site")
        for page in (root / "src/index.html", self.site / "src/index.html"):
            with self.subTest(page=page):
                self.assertEqual(
                    design_render.served_path(root, page), "src/index.html"
                )

    def test_page_named_through_symlinked_ancestor(self) -> None:
        alias = self.link(self.root / "alias", "site")
        self.assertEqual(
            design_render.served_path(self.site, alias / "src/index.html"),
            "src/index.html",
        )

    def test_relative_paths(self) -> None:
        page = self.link(self.site / "pages/index.html", "../src/index.html")
        self.assertEqual(
            design_render.served_path(
                pathlib.Path(os.path.relpath(self.site)),
                pathlib.Path(os.path.relpath(page)),
            ),
            "pages/index.html",
        )

    def test_outside_root_refused(self) -> None:
        outside = self.write("outside.html", "<p>o</p>")
        cases = {
            "file": outside,
            "dot-dot": self.site / "pages/../../outside.html",
            "link to inside": self.link(self.root / "link.html", "site/src/index.html"),
        }
        for name, page in cases.items():
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(ValueError, "not inside the served root"),
            ):
                design_render.served_path(self.site, page)

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
                json.dumps(record), encoding="utf-8")
            sys.stdout.write("<html>dom</html>")
            """
        )
        dom = design_render.dump_dom(
            "http://x/p.html", chromium=chromium, budget_ms=1234
        )
        self.assertEqual(dom, "<html>dom</html>")
        record = json.loads(self.beside_fake("record.json").read_text(encoding="utf-8"))
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

    def test_timeout_kills_the_whole_group(self) -> None:
        chromium = self.fake_chromium(SPAWN_PROFILE_WRITER + "time.sleep(60)\n")
        with self.assertRaisesRegex(design_render.RenderError, "within 3s"):
            design_render.dump_dom("http://x/", chromium=chromium, timeout_s=3)
        self.assert_writer_gone()

    def test_kills_children_left_after_exit(self) -> None:
        chromium = self.fake_chromium(
            SPAWN_PROFILE_WRITER + f"sys.stdout.write({CLEAN_DOM!r})\n"
        )
        dom = design_render.dump_dom("http://x/", chromium=chromium)
        self.assertEqual(dom, CLEAN_DOM)
        self.assert_writer_gone()

    def test_group_surviving_sigkill_fails(self) -> None:
        killpg = os.killpg

        def never_empty(pgid: int, number: int) -> None:
            if number:
                killpg(pgid, number)

        chromium = self.printing_chromium(CLEAN_DOM)
        with (
            unittest.mock.patch.object(design_render, "GROUP_EXIT_TIMEOUT_S", 0),
            unittest.mock.patch.object(
                design_render.os, "killpg", side_effect=never_empty
            ) as patched,
            self.assertRaisesRegex(design_render.RenderError, "survived SIGKILL"),
        ):
            design_render.dump_dom("http://x/", chromium=chromium)
        self.assertEqual(patched.call_args_list[0].args[1], signal.SIGKILL)


class PrintPdfTest(TempDirTestCase):
    def test_passes_flags_and_returns_the_pdf(self) -> None:
        chromium = self.fake_chromium(
            """
            arguments = sys.argv[1:]
            pathlib.Path(sys.argv[0]).with_name("record.json").write_text(
                json.dumps(arguments), encoding="utf-8")
            out = next(a for a in arguments if a.startswith("--print-to-pdf="))
            pathlib.Path(out.split("=", 1)[1]).write_bytes(b"%PDF-1.4 fake")
            """
        )
        pdf = design_render.print_pdf("http://x/p.html", chromium=chromium)
        self.assertEqual(pdf, b"%PDF-1.4 fake")
        arguments = json.loads(
            self.beside_fake("record.json").read_text(encoding="utf-8")
        )
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

    def test_timeout_leaves_no_process_and_checks_the_rest(self) -> None:
        hangs = self.write("hangs.html", "<p>h</p>")
        clean = self.write("clean.html", "<p>c</p>")
        chromium = self.fake_chromium(
            'if sys.argv[-1].endswith("/hangs.html"):\n'
            + textwrap.indent(SPAWN_PROFILE_WRITER + "time.sleep(60)\n", "    ")
            + f"sys.stdout.write({CLEAN_DOM!r})\n"
        )
        status, output = self.run_main(
            "--root",
            str(self.root),
            "--chromium",
            chromium,
            "--timeout-s",
            "3",
            str(hangs),
            str(clean),
        )
        self.assertEqual(status, 1)
        self.assertRegex(
            output,
            "^"
            + re.escape(f"{hangs}: {chromium} did not finish http://127.0.0.1:")
            + r"\d+"
            + re.escape("/hangs.html within 3.0s\nchecked 2 page(s): 1 problem(s)\n")
            + "$",
        )
        self.assert_writer_gone()

    def test_symlinked_page_inside_root(self) -> None:
        self.write("src/index.html", "<p>i</p>")
        page = self.root / "pages/index.html"
        page.parent.mkdir()
        page.symlink_to("../src/index.html")
        chromium = self.fake_chromium(
            f"""
            assert sys.argv[-1].endswith("/pages/index.html"), sys.argv[-1]
            sys.stdout.write({CLEAN_DOM!r})
            """
        )
        status, output = self.run_main(
            "--root", str(self.root), "--chromium", chromium, str(page)
        )
        self.assertEqual(status, 0, output)

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
            "unsupported selector": [
                *("--root", root, "--chromium", chromium, str(page)),
                *("--error-marker", "div"),
            ],
            "must be positive": [
                *("--root", root, "--chromium", chromium, str(page)),
                *("--budget-ms", "0"),
            ],
            "is not a directory": [
                *("--root", root, "--chromium", chromium, str(page)),
                *("--root", str(page)),
            ],
            "is not a file": [
                *("--root", root, "--chromium", chromium, str(page)),
                str(self.root / "missing.html"),
            ],
            "is not inside --root": [
                *("--root", root, "--chromium", chromium, str(page)),
                outside.name,
            ],
            "cannot find Chromium": [
                *("--root", root, "--chromium", chromium, str(page)),
                *("--chromium", str(self.root / "none")),
            ],
            "FILE": ["--root", root],
        }
        for message, arguments in cases.items():
            with self.subTest(message=message):
                self.assertIn(message, self.run_usage_error(*arguments))

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

    def test_failed_loads_started_from_css(self) -> None:
        cases = {
            "font": "@font-face { font-family: f; src: url(missing.woff2) }"
            " body { font-family: f }",
            "background": "body { background-image: url(missing.png) }",
            "import": "@import url(missing.css);",
        }
        for name, css in cases.items():
            with self.subTest(name=name):
                result = self.render("<p>text</p>", head=f"<style>{css}</style>")
                self.assertEqual(self.kinds(result), ["response"], result.errors)
                entry = result.errors[0]
                self.assertEqual((entry["initiator"], entry["status"]), ("css", 404))
                self.assertRegex(entry["url"], r"^http://127\.0\.0\.1:\d+/missing\.")

    def test_failed_import_in_a_linked_stylesheet(self) -> None:
        self.write("linked.css", "@import url(missing.css);")
        result = self.render(
            "<p>text</p>", head='<link rel="stylesheet" href="linked.css">'
        )
        self.assertEqual(
            sorted(
                (entry["kind"], entry["url"].rsplit("/", 1)[1])
                for entry in result.errors
            ),
            [("resource", "linked.css"), ("response", "missing.css")],
        )

    def test_clean_page_with_loads_started_from_css(self) -> None:
        self.write(
            "dot.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
        )
        self.write("imported.css", "p { color: black }")
        result = self.render(
            "<p>text</p>",
            head="<style>@import url(imported.css);"
            " body { background-image: url(dot.svg) }</style>",
        )
        self.assertEqual(result.errors, [])

    def test_failed_fetch_is_left_to_the_script(self) -> None:
        result = self.render(
            "<script>fetch('missing.json').then(function (r) {"
            " document.body.dataset.status = r.status; });</script>"
        )
        self.assertEqual(result.errors, [])
        self.assertIn('data-status="404"', result.dom)

    def test_symlinked_page_loads_beside_the_link(self) -> None:
        self.write(
            "src/index.html",
            '<!DOCTYPE html>\n<html><head><link rel="stylesheet" href="style.css">'
            "</head><body></body></html>\n",
        )
        self.write("pages/style.css", "body { color: black }")
        page = self.root / "pages/index.html"
        page.symlink_to("../src/index.html")
        result = design_render.render(self.root, page)
        self.assertEqual(result.errors, [])
        self.assertTrue(result.url.endswith("/pages/index.html"))

    def test_missing_icons_are_not_reported(self) -> None:
        for head in ("", '<link rel="shortcut icon" href="i.ico">'):
            with self.subTest(head=head):
                self.assertEqual(self.render("<p>x</p>", head=head).errors, [])

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
        status, output = self.run_main(
            "--root", str(self.root), "--error-marker", ".load-error", str(page)
        )
        self.assertEqual(status, 1)
        self.assertIn("matching .load-error", output)


if __name__ == "__main__":
    unittest.main()
