"""Tests for tools/html-embedded, with small Python scripts as formatters."""

from __future__ import annotations

import contextlib
import io
import pathlib
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest

import tools_loader

html_embedded = tools_loader.load("html-embedded")

FORMATTERS = {
    "upper": """
        path = pathlib.Path(sys.argv[1])
        path.write_text(path.read_text().upper())
    """,
    "identity": "",
    "fail": """
        print("lint error in", sys.argv[1])
        sys.exit(1)
    """,
    "silent-fail": "sys.exit(2)",
    "closer": """
        pathlib.Path(sys.argv[1]).write_text("x = '</SCRIPT>';\\n</style>\\n")
    """,
    "empty": "pathlib.Path(sys.argv[1]).write_text('')",
    "crlf": """
        path = pathlib.Path(sys.argv[1])
        text = path.read_text().replace("\\n", "\\r\\n")
        path.write_bytes(("\\r\\n\\r\\n" + text + "\\r\\n").encode())
    """,
    "record": """
        path = pathlib.Path(sys.argv[1])
        record = path.with_name("record.txt")
        previous = record.read_text() if record.exists() else ""
        record.write_text(previous + path.name + "\\n" + path.read_text() + "@@\\n")
    """,
}

PAGE = (
    "<!DOCTYPE html>\n"
    '<html lang="cs"><head><meta charset="utf-8">\n'
    "  <!-- <script>commented()</script> -->\n"
    "  <title>Čeština – test</title>\n"
    "  <style>\n"
    "    .a { color: red; }\n"
    "\n"
    "    .b > .c { margin: 0; }\n"
    "  </style>\n"
    '  <script src="lib.js"></script>\n'
    '  <script type="text/babel">const a = <b>x</b>;</script>\n'
    '  <script type="application/json">{"a": 1}</script>\n'
    "</head>\n"
    "<body data-x='a > b'>\n"
    '  <script type="module">\n'
    "    import x from './x.js';\n"
    "      x();\n"
    "  </script>\n"
    "  <p>after</p>\n"
    "</body></html>\n"
)


class EmbeddedTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = pathlib.Path(directory.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.site = self.root / "site"
        self.site.mkdir()

    def command(self, name: str) -> str:
        script = self.bin / f"{name}.py"
        script.write_text(
            "import pathlib, sys\n" + textwrap.dedent(FORMATTERS[name]),
            encoding="utf-8",
        )
        return shlex.join([sys.executable, str(script)])

    def page(self, content: str, name: str = "page.html") -> pathlib.Path:
        path = self.site / name
        path.write_bytes(content.encode("utf-8"))
        return path

    def run_main(self, *arguments: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = html_embedded.main(list(arguments))
        return status, stdout.getvalue(), stderr.getvalue()

    def format_with(
        self, path: pathlib.Path, js: str, css: str | None = None
    ) -> tuple[int, str, str]:
        return self.run_main(
            "format",
            "--js",
            self.command(js),
            "--css",
            self.command(css or js),
            str(path),
        )


class FindBlocksTest(unittest.TestCase):
    def test_selects_formattable_blocks(self) -> None:
        blocks = html_embedded.find_blocks(PAGE)
        self.assertEqual(
            [(block.language, block.tag, block.line) for block in blocks],
            [("css", "style", 5), ("js", "script", 15)],
        )
        self.assertEqual(
            PAGE[blocks[0].start : blocks[0].end],
            "\n    .a { color: red; }\n\n    .b > .c { margin: 0; }\n  ",
        )
        self.assertEqual(
            PAGE[blocks[1].start : blocks[1].end],
            "\n    import x from './x.js';\n      x();\n  ",
        )

    def test_script_types(self) -> None:
        cases = {
            "<script>a()</script>": ["js"],
            '<script type="">a()</script>': ["js"],
            '<script type=" Text/JavaScript ">a()</script>': ["js"],
            '<script type="MODULE">a()</script>': ["js"],
            '<script type="text/babel">a()</script>': [],
            '<script type="importmap">{}</script>': [],
            '<script src="a.js"> </script>': [],
            '<style media="print">a{}</style>': ["css"],
            "<p>no blocks</p></div>": [],
        }
        for markup, expected in cases.items():
            with self.subTest(markup=markup):
                blocks = html_embedded.find_blocks(markup)
                self.assertEqual([block.language for block in blocks], expected)


class FormatTest(EmbeddedTestCase):
    def test_splice_preserves_every_byte_outside_blocks(self) -> None:
        path = self.page(PAGE)
        blocks = html_embedded.find_blocks(PAGE)
        status, _, stderr = self.format_with(path, "upper")
        self.assertEqual(status, 0, stderr)
        self.assertEqual(stderr, "checked 1 file(s): reformatted 1, 0 problem(s)\n")
        result = path.read_bytes().decode("utf-8")
        first, second = blocks
        style = "\n    .A { COLOR: RED; }\n\n    .B > .C { MARGIN: 0; }\n  "
        script = "\n    IMPORT X FROM './X.JS';\n      X();\n  "
        self.assertEqual(
            result,
            PAGE[: first.start]
            + style
            + PAGE[first.end : second.start]
            + script
            + PAGE[second.end :],
        )

    def test_formatter_sees_dedented_source_in_a_sibling_file(self) -> None:
        path = self.page(PAGE)
        status, _, _ = self.format_with(path, "record")
        self.assertEqual(status, 0)
        record = (self.site / "record.txt").read_text().split("@@\n")
        style_name, style_source = record[0].split("\n", 1)
        script_name, script_source = record[1].split("\n", 1)
        self.assertRegex(style_name, r"^html-embedded-.*\.css$")
        self.assertRegex(script_name, r"^html-embedded-.*\.js$")
        self.assertEqual(style_source, ".a { color: red; }\n\n.b > .c { margin: 0; }\n")
        self.assertEqual(script_source, "import x from './x.js';\n  x();\n")
        leftovers = sorted(p.name for p in self.site.iterdir())
        self.assertEqual(leftovers, ["page.html", "record.txt"])
        self.assertEqual(path.read_text(encoding="utf-8"), PAGE)

    def test_identity_leaves_file_untouched(self) -> None:
        path = self.page(PAGE)
        status, _, stderr = self.format_with(path, "identity")
        self.assertEqual(status, 0)
        self.assertIn("reformatted 0", stderr)
        self.assertEqual(path.read_text(encoding="utf-8"), PAGE)

    def test_inline_blocks_are_laid_out(self) -> None:
        path = self.page(
            "<head>\n    <style>a{}</style>\n\t<script>b();\n\t  c();</script>\n"
            "<style>d{}</style>\n"
        )
        status, _, _ = self.format_with(path, "identity")
        self.assertEqual(status, 0)
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            "<head>\n    <style>\n      a{}\n    </style>\n"
            "\t<script>\n\t\tb();\n\t\t  c();\n\t</script>\n"
            "<style>\n  d{}\n</style>\n",
        )

    def test_inline_block_with_shallower_continuation(self) -> None:
        path = self.page("    <script>  b();\n  c();\n</script>\n")
        status, _, _ = self.format_with(path, "identity")
        self.assertEqual(status, 0)
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            "    <script>\n      b();\n        c();\n    </script>\n",
        )

    def test_whitespace_only_blocks_are_skipped(self) -> None:
        path = self.page("<script>\n  </script><style></style>")
        status, _, _ = self.format_with(path, "fail")
        self.assertEqual(status, 0)

    def test_empty_formatter_output(self) -> None:
        path = self.page("<div>\n  <style>\n    a{}\n  </style>\n</div>")
        status, _, _ = self.format_with(path, "empty")
        self.assertEqual(status, 0)
        self.assertEqual(
            path.read_text(encoding="utf-8"), "<div>\n  <style>\n  </style>\n</div>"
        )

    def test_crlf_blocks_keep_crlf(self) -> None:
        content = "<p>\r\n  <script>\r\n    a();\r\n\r\n    b();\r\n  </script>\r\n"
        path = self.page(content)
        status, _, _ = self.format_with(path, "crlf")
        self.assertEqual(status, 0)
        self.assertEqual(path.read_bytes().decode("utf-8"), content)

    def test_failure_leaves_whole_file_untouched(self) -> None:
        path = self.page(PAGE)
        status, _, stderr = self.format_with(path, "upper", css="fail")
        self.assertEqual(status, 1)
        self.assertIn(f"{path}:5: <style>: ", stderr)
        self.assertIn("exited 1: lint error in <block>", stderr)
        self.assertIn("reformatted 0, 1 problem(s)", stderr)
        self.assertEqual(path.read_text(encoding="utf-8"), PAGE)

    def test_failure_without_output(self) -> None:
        path = self.page("<style>a{}</style>")
        status, _, stderr = self.format_with(path, "silent-fail")
        self.assertEqual(status, 1)
        self.assertIn("exited 2: no output", stderr)

    def test_output_holding_a_closing_tag_is_refused(self) -> None:
        path = self.page("<script>a()</script><style>b{}</style>")
        status, _, stderr = self.format_with(path, "closer")
        self.assertEqual(status, 1)
        self.assertIn("formatted <script> would contain its own closing tag", stderr)
        self.assertIn("formatted <style> would contain its own closing tag", stderr)
        self.assertEqual(
            path.read_text(encoding="utf-8"), "<script>a()</script><style>b{}</style>"
        )

    def test_missing_command(self) -> None:
        path = self.page("<style>a{}</style>")
        missing = str(self.root / "no-such-formatter")
        status, _, stderr = self.run_main(
            "format", "--js", missing, "--css", missing, str(path)
        )
        self.assertEqual(status, 2)
        self.assertIn("cannot run", stderr)
        self.assertEqual(sorted(p.name for p in self.site.iterdir()), ["page.html"])

    def test_not_utf8(self) -> None:
        path = self.site / "bad.html"
        path.write_bytes(b"<p>\xff</p>")
        status, _, stderr = self.format_with(path, "identity")
        self.assertEqual(status, 1)
        self.assertIn(f"{path}: not valid UTF-8", stderr)


class CheckTest(EmbeddedTestCase):
    def check_with(self, path: pathlib.Path, name: str) -> tuple[int, str, str]:
        return self.run_main(
            "check", "--js", self.command(name), "--css", self.command(name), str(path)
        )

    def test_reports_diff_and_writes_nothing(self) -> None:
        path = self.page(PAGE)
        status, stdout, stderr = self.check_with(path, "upper")
        self.assertEqual(status, 1)
        self.assertEqual(path.read_text(encoding="utf-8"), PAGE)
        self.assertIn(f"--- {path}\n+++ {path} (formatted)\n", stdout)
        self.assertIn("-    .a { color: red; }\n+    .A { COLOR: RED; }\n", stdout)
        self.assertIn(f"{path}: embedded code is not formatted", stderr)
        self.assertIn("would reformat 1, 1 problem(s)", stderr)

    def test_formatted_file_is_clean(self) -> None:
        path = self.page(PAGE)
        self.assertEqual(self.format_with(path, "upper")[0], 0)
        status, stdout, stderr = self.check_with(path, "upper")
        self.assertEqual(status, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "checked 1 file(s): would reformat 0, 0 problem(s)\n")

    def test_runs_as_a_command(self) -> None:
        path = self.page(PAGE)
        identity = self.command("identity")
        result = subprocess.run(
            [
                str(tools_loader.TOOLS / "html-embedded"),
                *("check", "--js", identity, "--css", identity, str(path)),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stderr, "checked 1 file(s): would reformat 0, 0 problem(s)\n"
        )

    def test_failure_is_reported(self) -> None:
        path = self.page("<style>a{}</style>")
        status, stdout, stderr = self.check_with(path, "fail")
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn(":1: <style>: ", stderr)


class UsageTest(EmbeddedTestCase):
    def test_usage_errors(self) -> None:
        path = self.page("<p></p>")
        cases = {
            "invalid choice": ["tidy", "--js", "x", "--css", "x", str(path)],
            "--js is empty": ["check", "--js", " ", "--css", "x", str(path)],
            "--css is empty": ["check", "--js", "x", "--css", "", str(path)],
            "is not a file": ["check", "--js", "x", "--css", "x", "missing.html"],
            "required": ["check", "--css", "x", str(path)],
            "FILE": ["check", "--js", "x", "--css", "x"],
        }
        for message, arguments in cases.items():
            with self.subTest(message=message):
                stderr = io.StringIO()
                with (
                    contextlib.redirect_stderr(stderr),
                    self.assertRaises(SystemExit) as caught,
                ):
                    html_embedded.main(arguments)
                self.assertEqual(caught.exception.code, 2)
                self.assertIn(message, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
