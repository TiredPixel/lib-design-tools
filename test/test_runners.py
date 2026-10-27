"""bin/format, bin/lint, bin/test and bin/preview, run as another repository runs them:
from the root of a fabricated repository whose bin/ links to them."""

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

PASSING_TEST = (
    "import unittest\n\n\nclass A(unittest.TestCase):\n    def test_a(self):\n"
    "        pass\n"
)
SKIPPING_TEST = (
    "import unittest\n\n\nclass A(unittest.TestCase):\n    def test_a(self):\n"
    "        self.skipTest('missing')\n"
)


class Repository(unittest.TestCase):
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

    def settings(self, text):
        self.write("bin/settings", "# shellcheck shell=bash\n" + text)

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


class PassSelectionTest(Repository):
    def test_nothing_runs_that_the_settings_do_not_name(self):
        self.settings("")
        self.write(".env.example", "# B=1\n# A=1\n")
        self.write("script.sh", "#!/bin/bash\necho $1\n")
        self.write("a.yml", UNSORTED_YAML)
        self.write("test/test_a.py", SKIPPING_TEST)
        for runner in ("format", "lint", "test"):
            with self.subTest(runner=runner):
                result = self.run_bin(runner)
                self.assertPasses(result)
                self.assertEqual(self.sections(result), [])
        self.assertEqual((self.root / "a.yml").read_text(), UNSORTED_YAML)

    def test_only_the_defined_passes_run(self):
        self.write("a.html", "<p>a</p>\n")
        self.settings("function references_args { printf '%s\\0' --root . a.html; }\n")
        result = self.run_bin("lint")
        self.assertPasses(result)
        self.assertEqual(self.sections(result), ["LINT REFERENCES"])

    def test_a_set_naming_nothing_fails(self):
        self.settings("function python_files { :; }\n")
        self.assertFails(
            self.run_bin("lint"), "bin/settings: python_files names nothing"
        )

    def test_a_failing_set_fails_even_after_output(self):
        self.write("a.html", "<p>a</p>\n")
        self.settings(
            "function references_args { printf '%s\\0' --root . a.html; false; }\n"
        )
        self.assertFails(self.run_bin("lint"), "bin/settings: references_args failed")

    def test_the_shell_pass_checks_only_the_named_files(self):
        self.write("ok.sh", '# shellcheck shell=bash\necho "$1"\n')
        self.write("unnamed.sh", "#!/bin/bash\necho $1\n")
        self.write("bin/unnamed", "#!/bin/bash\necho $1\n")
        self.settings(
            "function shell_files { printf '%s\\0' ok.sh; }\n"
            "function helper { echo $1; }\n"
        )
        result = self.run_bin("lint")
        self.assertPasses(result)
        self.assertEqual(self.sections(result), ["LINT SHELL"])
        self.assertIn("checking 1 shell file(s)", result.stderr)
        self.settings(
            "function shell_files { printf '%s\\0' ok.sh bin/settings; }\n"
            "function helper { echo $1; }\n"
        )
        self.assertFails(self.run_bin("lint"), "SC2086")
        self.settings("function shell_files { printf '%s\\0' unnamed.sh; }\n")
        self.assertFails(self.run_bin("lint"), "SC2086")


class FormatThenLintTest(Repository):
    """Each writing pass: lint fails on the unformatted file, format fixes it, and lint
    then passes."""

    def assertFormatFixes(self, name, before, after, section):
        self.write(name, before)
        self.assertNotEqual(self.run_bin("lint").returncode, 0)
        result = self.run_bin("format")
        self.assertPasses(result)
        self.assertIn(f"FORMAT {section}", self.sections(result))
        self.assertRegex(result.stderr, r"formatting [12] .* file\(s\)")
        self.assertEqual((self.root / name).read_text(), after)
        result = self.run_bin("lint")
        self.assertPasses(result)
        self.assertRegex(result.stderr, r"checking [12] .* file\(s\)")

    def test_yaml(self):
        self.settings("function yaml_files { printf '%s\\0' a.yml; }\n")
        self.assertFormatFixes("a.yml", UNSORTED_YAML, SORTED_YAML, "YAML")

    def test_a_file_name_starting_with_a_dash_is_not_read_as_an_option(self):
        self.settings("function yaml_files { printf '%s\\0' -a.yml; }\n")
        self.assertFormatFixes("-a.yml", UNSORTED_YAML, SORTED_YAML, "YAML")
        self.settings("function json_files { printf '%s\\0' -a.json; }\n")
        self.assertFormatFixes("-a.json", '{"b": 1}', '{\n  "b": 1\n}\n', "JSON")

    def test_json(self):
        self.settings("function json_files { printf '%s\\0' a.json; }\n")
        self.assertFormatFixes(
            "a.json", '{"b": 1, "a": "é"}', '{\n  "a": "é",\n  "b": 1\n}\n', "JSON"
        )

    def test_python(self):
        self.settings("function python_files { printf '%s\\0' a.py; }\n")
        self.assertFormatFixes("a.py", "x = ( 1 )\n", "x = 1\n", "PYTHON")

    def test_javascript(self):
        self.settings("function js_files { printf '%s\\0' a.js; }\n")
        self.assertFormatFixes(
            "a.js",
            "const a = 1\nconsole.log(a)\n",
            "const a = 1;\nconsole.log(a);\n",
            "JAVASCRIPT",
        )

    def test_css_and_html(self):
        self.settings(
            "function css_files { printf '%s\\0' a.css; }\n"
            "function html_files { printf '%s\\0' a.html; }\n"
        )
        self.write("a.html", "<!doctype html>\n<p>a</p>\n")
        self.assertFormatFixes(
            "a.css", "a{color:red}\n", "a {\n  color: red;\n}\n", "CSS AND HTML"
        )

    def test_palette(self):
        self.settings(
            "function palette_args {\n"
            "    printf '%s\\0' p.yml --prefix x --json p.json --css p.css\n"
            "}\n"
        )
        self.write("p.yml", 'a: "#000000"\n')
        self.assertPasses(self.run_bin("format"))
        self.assertIn("--x-a: #000000;", (self.root / "p.css").read_text())
        self.assertPasses(self.run_bin("lint"))
        self.write("p.yml", 'a: "#FFFFFF"\n')
        self.assertFails(self.run_bin("lint"), "palette files are stale.")


class CheckingPassTest(Repository):
    """The passes that only check: each passes a sound tree and fails a broken one."""

    def test_lfs(self):
        self.settings(
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
        self.settings(
            "function references_args {\n"
            "    printf '%s\\0' --no-external --root . a.html\n"
            "}\n"
        )
        self.write("a.html", '<link rel="stylesheet" href="gone.css">\n')
        self.assertFails(self.run_bin("lint"), "gone.css")
        self.write("gone.css", "a {}\n")
        self.assertPasses(self.run_bin("lint"))

    def test_render(self):
        self.settings("function render_args { printf '%s\\0' --root . a.html; }\n")
        self.write("a.html", "<!doctype html>\n<script>missing();</script>\n")
        self.assertFails(self.run_bin("lint"), "a.html")
        self.write("a.html", "<!doctype html>\n<p>a</p>\n")
        result = self.run_bin("lint")
        self.assertPasses(result)
        self.assertEqual(self.sections(result), ["LINT RENDER"])


class TestRunnerTest(Repository):
    def test_named_tests_skip_the_lint_and_reach_the_tools(self):
        self.settings(
            "function pytest_args { printf '%s\\0' --no-skips; }\n"
            "function references_args { printf '%s\\0' --root . gone.html; }\n"
        )
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
        self.write("a.html", "<p>a</p>\n")
        self.settings(
            "function pytest_args { printf '%s\\0' --no-skips; }\n"
            "function references_args { printf '%s\\0' --root . a.html; }\n"
        )
        self.write("test/test_a.py", PASSING_TEST)
        result = self.run_bin("test")
        self.assertPasses(result)
        self.assertEqual(self.sections(result), ["LINT REFERENCES", "TEST PYTHON"])

    def test_the_repository_s_own_pytest_configuration_applies(self):
        self.settings("function pytest_args { :; }\n")
        self.write("pytest.ini", "[pytest]\npython_files = check_*.py\n")
        self.write("test/check_a.py", PASSING_TEST)
        self.write("test/test_ignored.py", "raise SystemExit('collected')\n")
        result = self.run_bin("test")
        self.assertPasses(result)
        self.assertIn("1 passed", result.stdout)

    def test_a_skip_fails_only_with_no_skips(self):
        self.write("test/test_a.py", SKIPPING_TEST)
        self.settings("function pytest_args { :; }\n")
        self.assertPasses(self.run_bin("test", "test/test_a.py"))
        self.settings("function pytest_args { printf '%s\\0' --no-skips; }\n")
        self.assertFails(
            self.run_bin("test", "test/test_a.py"), "skipped, which --no-skips fails"
        )

    def test_naming_tests_without_pytest_args_fails(self):
        self.settings("")
        self.write("test/test_a.py", PASSING_TEST)
        self.assertFails(
            self.run_bin("test", "test/test_a.py"), "pytest_args is not set"
        )

    def test_a_worker_count_that_is_not_a_number_fails(self):
        self.settings("")
        for count in ("0", "two", ""):
            with self.subTest(count=count):
                self.assertFails(
                    self.run_bin("test", env={"PYTEST_XDIST_AUTO_NUM_WORKERS": count}),
                    "not a whole number",
                )


class PreviewTest(Repository):
    def test_refuses_without_an_index(self):
        self.settings("")
        self.assertFails(self.run_bin("preview"), "preview_index is not set")

    def test_serves_every_address_and_redirects_to_the_index(self):
        self.settings("# shellcheck disable=SC2034\npreview_index=/pages/\n")
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
