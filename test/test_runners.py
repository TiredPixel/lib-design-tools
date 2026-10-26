"""bin/format, bin/lint, bin/test and bin/preview, run as a consumer runs them: from
the root of a fabricated repository whose bin/ links to them."""

import os
import pathlib
import subprocess
import tempfile
import textwrap
import time
import unittest
import urllib.error
import urllib.request

import tools_loader

BIN = tools_loader.TOOLS.parent / "bin"

UNSORTED_YAML = "b: 1\na: 2\n"
SORTED_YAML = "a: 2\nb: 1\n"


class Consumer(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = pathlib.Path(directory.name)
        (self.root / "bin").mkdir()
        for runner in ("format", "lint", "test", "preview"):
            (self.root / "bin" / runner).symlink_to(BIN / runner)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip())
        return path

    def paths(self, text):
        self.write("bin/format-paths", "# shellcheck shell=bash\n" + text)

    def run_bin(self, runner, *args, env=None):
        return subprocess.run(
            [f"bin/{runner}", *args],
            cwd=self.root,
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            check=False,
        )

    def assertPasses(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assertFails(self, result, message):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(message, result.stdout + result.stderr)

    def sections(self, result):
        return [
            line.removeprefix("=== ")
            for line in result.stdout.splitlines()
            if line.startswith("=== ")
        ]


class PassSelectionTest(Consumer):
    def test_only_the_defined_passes_run(self):
        self.write("README.md", "# A\n\nOne paragraph.\n")
        self.paths("function markdown_args { printf '%s\\0' README.md; }\n")
        result = self.run_bin("lint")
        self.assertPasses(result)
        self.assertEqual(
            self.sections(result), ["LINT SHELL", "LINT FILES", "LINT MARKDOWN"]
        )

    def test_format_with_nothing_defined_runs_no_pass(self):
        self.paths("")
        result = self.run_bin("format")
        self.assertPasses(result)
        self.assertEqual(self.sections(result), [])

    def test_a_set_naming_nothing_fails(self):
        self.paths("function python_files { :; }\n")
        self.assertFails(
            self.run_bin("lint"), "bin/format-paths: python_files names nothing"
        )

    def test_a_failing_set_fails_even_after_output(self):
        self.write("README.md", "# A\n")
        self.paths("function markdown_args { printf '%s\\0' README.md; false; }\n")
        self.assertFails(self.run_bin("lint"), "bin/format-paths: markdown_args failed")

    def test_a_shell_problem_in_format_paths_fails(self):
        self.paths("function yaml_files { echo $1; }\nx=1\n")
        self.assertFails(self.run_bin("lint"), "SC2086")

    def test_shell_files_are_linted(self):
        self.paths("function shell_files { printf '%s\\0' extra.sh; }\n")
        self.write("extra.sh", "# shellcheck shell=bash\necho $1\n")
        self.assertFails(self.run_bin("lint"), "SC2086")

    def test_an_unsorted_env_example_fails(self):
        self.paths("")
        self.write(".env.example", "# B=1\n# A=1\n")
        self.assertFails(self.run_bin("lint"), "disorder")
        self.write(".env.example", "# A=1\n# B=1\n")
        self.assertPasses(self.run_bin("lint"))


class FormatThenLintTest(Consumer):
    """Each writing pass: lint fails on the unformatted file, format fixes it, and lint
    then passes."""

    def assertFormatFixes(self, name, before, after, section):
        self.write(name, before)
        self.assertNotEqual(self.run_bin("lint").returncode, 0)
        result = self.run_bin("format")
        self.assertPasses(result)
        self.assertIn(f"FORMAT {section}", self.sections(result))
        self.assertEqual((self.root / name).read_text(), after)
        self.assertPasses(self.run_bin("lint"))

    def test_yaml(self):
        self.paths("function yaml_files { printf '%s\\0' a.yml; }\n")
        self.assertFormatFixes("a.yml", UNSORTED_YAML, SORTED_YAML, "YAML")

    def test_json(self):
        self.paths("function json_files { printf '%s\\0' a.json; }\n")
        self.assertFormatFixes(
            "a.json", '{"b": 1, "a": "é"}', '{\n  "a": "é",\n  "b": 1\n}\n', "JSON"
        )

    def test_python(self):
        self.paths("function python_files { printf '%s\\0' a.py; }\n")
        self.assertFormatFixes("a.py", "x = ( 1 )\n", "x = 1\n", "PYTHON")

    def test_javascript(self):
        self.paths("function js_files { printf '%s\\0' a.js; }\n")
        self.assertFormatFixes(
            "a.js",
            "const a = 1\nconsole.log(a)\n",
            "const a = 1;\nconsole.log(a);\n",
            "JAVASCRIPT",
        )

    def test_css_and_html(self):
        self.paths(
            "function css_files { printf '%s\\0' a.css; }\n"
            "function html_files { printf '%s\\0' a.html; }\n"
        )
        self.write("a.html", "<!doctype html>\n<p>a</p>\n")
        self.assertFormatFixes(
            "a.css", "a{color:red}\n", "a {\n  color: red;\n}\n", "CSS AND HTML"
        )

    def test_palette(self):
        self.paths(
            "function palette_args {\n"
            "    printf '%s\\0' p.yml --prefix x --json p.json --css p.css\n"
            "}\n"
        )
        self.write("p.yml", 'a: "#000000"\n')
        self.assertPasses(self.run_bin("format"))
        self.assertIn("--x-a: #000000;", (self.root / "p.css").read_text())
        self.assertPasses(self.run_bin("lint"))
        self.write("p.yml", 'a: "#FFFFFF"\n')
        self.assertFails(self.run_bin("lint"), "stale: run make format")


class CheckingPassTest(Consumer):
    """The passes that only check: each passes a clean tree and fails a broken one."""

    def test_charset(self):
        self.paths("function charset_files { printf '%s\\0' a.css; }\n")
        self.write("a.css", "a {}\n")
        self.assertFails(self.run_bin("lint"), "a.css")
        self.write("a.css", '@charset "UTF-8";\na {}\n')
        self.assertPasses(self.run_bin("lint"))

    def test_lfs(self):
        self.paths(
            "function lfs_args { printf '%s\\0' --attributes .gitattributes img; }\n"
        )
        self.write(".gitattributes", "*.png filter=lfs -text\n")
        self.write(
            "img/a.png",
            "version https://git-lfs.github.com/spec/v1\noid sha256:0\nsize 1\n",
        )
        self.assertFails(self.run_bin("lint"), "img/a.png")
        (self.root / "img/a.png").write_bytes(b"\x89PNG\r\n")
        self.assertPasses(self.run_bin("lint"))

    def test_references(self):
        self.paths(
            "function references_args {\n"
            "    printf '%s\\0' --no-external --root . a.html\n"
            "}\n"
        )
        self.write("a.html", '<link rel="stylesheet" href="gone.css">\n')
        self.assertFails(self.run_bin("lint"), "gone.css")
        self.write("gone.css", '@charset "UTF-8";\n')
        self.assertPasses(self.run_bin("lint"))

    def test_markdown(self):
        self.paths("function markdown_args { printf '%s\\0' a.md; }\n")
        self.write("a.md", "Wrapped\nparagraph.\n")
        self.assertFails(self.run_bin("lint"), "a.md")
        self.write("a.md", "One paragraph.\n")
        self.assertPasses(self.run_bin("lint"))

    def test_comments(self):
        self.paths("function comment_files { printf '%s\\0' a.py; }\n")
        self.write("a.py", "# a — b\n")
        self.assertFails(self.run_bin("lint"), "a.py")
        self.write("a.py", "# a -- b\n")
        self.assertPasses(self.run_bin("lint"))


class TestRunnerTest(Consumer):
    def setUp(self):
        super().setUp()
        self.paths("")

    def test_named_tests_skip_the_lint_and_run_under_the_shared_settings(self):
        self.write(
            "test/test_a.py",
            """
            import unittest

            import design_render
            import helper


            class A(unittest.TestCase):
                def test_a(self):
                    self.assertTrue(design_render and helper.OK)
            """,
        )
        self.write("test/helper.py", "OK = True\n")
        result = self.run_bin("test", "test/test_a.py")
        self.assertPasses(result)
        self.assertEqual(self.sections(result), ["TEST PYTHON"])
        self.assertIn("1 passed", result.stdout)

    def test_the_whole_gate_lints_first(self):
        self.write(
            "test/test_a.py",
            "import unittest\n\n\nclass A(unittest.TestCase):\n"
            "    def test_a(self):\n        pass\n",
        )
        result = self.run_bin("test")
        self.assertPasses(result)
        self.assertEqual(
            self.sections(result), ["LINT SHELL", "LINT FILES", "TEST PYTHON"]
        )

    def test_a_skip_fails_the_gate(self):
        self.write(
            "test/test_a.py",
            "import unittest\n\n\nclass A(unittest.TestCase):\n"
            "    def test_a(self):\n        self.skipTest('missing')\n",
        )
        self.assertFails(
            self.run_bin("test", "test/test_a.py"), "skipped, which --no-skips fails"
        )

    def test_a_worker_count_that_is_not_a_number_fails(self):
        for count in ("0", "two", ""):
            with self.subTest(count=count):
                self.assertFails(
                    self.run_bin(
                        "test", "test", env={"PYTEST_XDIST_AUTO_NUM_WORKERS": count}
                    ),
                    "not a whole number",
                )


class PreviewTest(Consumer):
    def test_refuses_without_an_index(self):
        self.paths("")
        self.assertFails(self.run_bin("preview"), "preview_index is not set")

    def test_serves_every_address_and_redirects_to_the_index(self):
        self.paths("preview_index=/pages/\n")
        self.write("pages/index.html", "<p>page</p>")
        server = subprocess.Popen(
            ["bin/preview"],
            cwd=self.root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(server.wait)
        self.addCleanup(server.terminate)
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen("http://127.0.0.1:8080/") as response:
                    self.assertEqual(response.url, "http://127.0.0.1:8080/pages/")
                    self.assertIn(b"page", response.read())
                break
            except (urllib.error.URLError, ConnectionError):
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        # The test container has no network but loopback, so the wildcard bind is
        # read from the kernel's socket table: 0.0.0.0:8080, listening.
        table = pathlib.Path("/proc/net/tcp").read_text().splitlines()[1:]
        listening = {(line.split()[1], line.split()[3]) for line in table}
        self.assertIn(("00000000:1F90", "0A"), listening)
