"""bin/format, bin/lint, bin/test and bin/preview, run as another repository runs them:
from the root of a fabricated repository whose bin/ links to them."""

import os
import pathlib
import subprocess
import textwrap
import time
import urllib.error
import urllib.request

import temp_tree

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"

# A generous bound on any one child process, so a hang fails its test rather than
# stalling a worker; not a measure of how long anything should take.
WAIT_S = 300

UNSORTED_YAML = "---\nb: 1\na: 2\n"
SORTED_YAML = "---\na: 2\nb: 1\n"

UNFORMATTED_MARKDOWN = (
    "---\ntitle: a\n---\n\nOne\ntwo.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
)
FORMATTED_MARKDOWN = (
    "---\ntitle: a\n---\n\nOne two.\n\n| a   | b   |\n| --- | --- |\n| 1   | 2   |\n"
)
UNFORMATTED_DOCKERFILE = "from debian:13\nrun true"
FORMATTED_DOCKERFILE = "FROM debian:13\nRUN true\n"
VALID_COMPOSE = "services:\n  a:\n    image: debian\n"
VALID_WORKFLOW = (
    "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n"
    "      - run: echo a\n"
)
VALID_MAKEFILE = "all:\n\ttrue\nclean:\n\ttrue\ntest:\n\ttrue\n.PHONY: all clean test\n"
LONG_BODY_MAKEFILE = VALID_MAKEFILE.replace("all:\n\ttrue\n", "all:\n" + "\ttrue\n" * 6)
INVALID_WORKFLOW = (
    "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n"
    "      - run: echo ${{ undefined_context }}\n"
)

PASSING_TEST = (
    "import unittest\n\n\nclass A(unittest.TestCase):\n    def test_a(self):\n"
    "        pass\n"
)
SKIPPING_TEST = (
    "import unittest\n\n\nclass A(unittest.TestCase):\n    def test_a(self):\n"
    "        self.skipTest('missing')\n"
)


class Repository(temp_tree.TempTreeTestCase):
    def setUp(self):
        super().setUp()
        (self.root / "bin").mkdir()
        for runner in ("format", "lint", "test", "preview"):
            (self.root / "bin" / runner).symlink_to(BIN / runner)

    def write(self, name, text):
        return super().write(name, textwrap.dedent(text).lstrip())

    def settings(self, text):
        self.write("bin/settings", "# shellcheck shell=bash\n" + text)

    def run_bin(self, runner, *args, env=None):
        return subprocess.run(
            [f"bin/{runner}", *args],
            cwd=self.root,
            env={**os.environ, **(env or {})},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=WAIT_S,
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
        self.assertEqual(
            (self.root / "a.yml").read_text(encoding="utf-8"), UNSORTED_YAML
        )

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
        self.assertFails(
            self.run_bin("lint"), "bin/settings: references_args failed (1)"
        )

    def test_a_failing_set_fails_even_before_its_last_command(self):
        self.write("a.html", "<p>a</p>\n")
        self.settings(
            "function references_args { false; printf '%s\\0' --root . a.html; }\n"
        )
        self.assertFails(
            self.run_bin("lint"), "bin/settings: references_args failed (1)"
        )

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

    def test_the_linked_runners_lint_clean_with_no_bin_runner_beside_them(self):
        self.settings("function shell_files { printf '%s\\0' bin/*; }\n")
        result = self.run_bin("lint")
        self.assertPasses(result)
        self.assertIn("checking 5 shell file(s)", result.stderr)

    def test_each_new_set_runs_its_own_pass_alone(self):
        self.write("a.md", "a\n")
        self.write("a.toml", "a = 1\n")
        self.write("Dockerfile", FORMATTED_DOCKERFILE)
        self.write("compose.yml", VALID_COMPOSE)
        self.write("workflow.yml", VALID_WORKFLOW)
        self.write("Makefile", VALID_MAKEFILE)
        cases = (
            ("md_files", "a.md", ["FORMAT MARKDOWN"], ["LINT MARKDOWN"]),
            ("toml_files", "a.toml", ["FORMAT TOML"], ["LINT TOML"]),
            ("docker_files", "Dockerfile", ["FORMAT DOCKER"], ["LINT DOCKER"]),
            ("compose_files", "compose.yml", [], ["LINT COMPOSE"]),
            ("workflow_files", "workflow.yml", [], ["LINT WORKFLOWS"]),
            ("make_files", "Makefile", [], ["LINT MAKE"]),
        )
        for name, path, formats, lints in cases:
            with self.subTest(name=name):
                self.settings(f"function {name} {{ printf '%s\\0' {path}; }}\n")
                for runner, sections in (("format", formats), ("lint", lints)):
                    result = self.run_bin(runner)
                    self.assertPasses(result)
                    self.assertEqual(self.sections(result), sections)
                self.settings(f"function {name} {{ :; }}\n")
                self.assertFails(
                    self.run_bin("lint"), f"bin/settings: {name} names nothing"
                )


class FormatThenLintTest(Repository):
    """Each writing pass: lint fails on the unformatted file, format fixes it, and lint
    then passes."""

    def assertFormatFixes(self, name, before, after, section, count=1):
        self.write(name, before)
        self.assertNotEqual(self.run_bin("lint").returncode, 0)
        result = self.run_bin("format")
        self.assertPasses(result)
        self.assertIn(f"FORMAT {section}", self.sections(result))
        self.assertRegex(result.stderr, rf"formatting {count} .* file\(s\)")
        self.assertEqual((self.root / name).read_text(encoding="utf-8"), after)
        result = self.run_bin("lint")
        self.assertPasses(result)
        self.assertRegex(result.stderr, rf"checking {count} .* file\(s\)")

    def test_shell(self):
        self.settings("function shell_files { printf '%s\\0' a.sh; }\n")
        self.assertFormatFixes(
            "a.sh",
            "#!/bin/bash\nf() {\n  echo a > /dev/null\n}\n",
            "#!/bin/bash\nf() {\n    echo a >/dev/null\n}\n",
            "SHELL",
        )

    def test_yaml(self):
        self.settings("function yaml_files { printf '%s\\0' a.yml; }\n")
        self.assertFormatFixes("a.yml", UNSORTED_YAML, SORTED_YAML, "YAML")

    def test_a_file_name_starting_with_a_dash_is_not_read_as_an_option(self):
        self.settings("function yaml_files { printf '%s\\0' -a.yml; }\n")
        self.assertFormatFixes("-a.yml", UNSORTED_YAML, SORTED_YAML, "YAML")
        self.settings("function json_files { printf '%s\\0' -a.json; }\n")
        self.assertFormatFixes("-a.json", '{"b": 1}', '{\n  "b": 1\n}\n', "JSON")

    def test_yaml_with_sorted_keys_still_fails_yamllint(self):
        self.settings("function yaml_files { printf '%s\\0' a.yml; }\n")
        for text, rule in (
            ("a: 2\nb: 1\n", "document-start"),
            ("---\na: yes\nb: 1\n", "truthy"),
        ):
            with self.subTest(rule=rule):
                self.write("a.yml", text)
                self.assertFails(self.run_bin("lint"), rule)

    def test_json(self):
        self.settings("function json_files { printf '%s\\0' a.json; }\n")
        # Numbers keep their literals, which a formatter going through floats would
        # round or respell.
        self.assertFormatFixes(
            "a.json",
            '{"b": 1, "a": "é", "n": 12345678901234567890123, "f": 1.0, "z": -0}',
            '{\n  "a": "é",\n  "b": 1,\n  "f": 1.0,\n'
            '  "n": 12345678901234567890123,\n  "z": -0\n}\n',
            "JSON",
        )

    def test_json_that_does_not_parse_fails_the_lint(self):
        self.settings("function json_files { printf '%s\\0' a.json; }\n")
        self.write("a.json", '{"a": }\n')
        self.assertFails(self.run_bin("lint"), "a.json: not formatted")

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
            "a.css",
            "a{color:red}\n",
            "a {\n  color: red;\n}\n",
            "CSS AND HTML",
            count=2,
        )

    def test_css_alone(self):
        self.settings("function css_files { printf '%s\\0' a.css; }\n")
        self.assertFormatFixes(
            "a.css", "a{color:red}\n", "a {\n  color: red;\n}\n", "CSS AND HTML"
        )

    def test_markdown(self):
        # A paragraph is joined onto one line, and the front matter and the GFM table
        # come through as front matter and a table, padded to their columns, rather than
        # as a thematic break and a paragraph.
        self.settings("function md_files { printf '%s\\0' a.md; }\n")
        self.write("a.md", UNFORMATTED_MARKDOWN)
        self.assertFails(self.run_bin("lint"), "a.md")
        self.assertFormatFixes(
            "a.md", UNFORMATTED_MARKDOWN, FORMATTED_MARKDOWN, "MARKDOWN"
        )

    def test_markdown_split_over_lines_fails_the_lint(self):
        self.settings("function md_files { printf '%s\\0' a.md; }\n")
        self.write("a.md", "One\ntwo.\n")
        self.assertFails(self.run_bin("lint"), "a.md")
        self.write("a.md", "One two.\n")
        self.assertPasses(self.run_bin("lint"))

    def test_toml(self):
        self.settings("function toml_files { printf '%s\\0' a.toml; }\n")
        self.assertFormatFixes("a.toml", "a=1\n", "a = 1\n", "TOML")

    def test_dockerfile(self):
        self.settings("function docker_files { printf '%s\\0' Dockerfile; }\n")
        # hadolint passes this file, so the failure is dockerfmt's check.
        self.write("Dockerfile", UNFORMATTED_DOCKERFILE)
        self.assertFails(self.run_bin("lint"), "Dockerfile is not formatted")
        self.assertFormatFixes(
            "Dockerfile", UNFORMATTED_DOCKERFILE, FORMATTED_DOCKERFILE, "DOCKER"
        )

    def test_palette(self):
        self.settings(
            "function palette_args {\n"
            "    printf '%s\\0' p.yml --prefix x --json p.json --css p.css\n"
            "}\n"
        )
        self.write("p.yml", 'a: "#000000"\n')
        self.assertPasses(self.run_bin("format"))
        self.assertIn(
            "--x-a: #000000;", (self.root / "p.css").read_text(encoding="utf-8")
        )
        self.assertPasses(self.run_bin("lint"))
        self.write("p.yml", 'a: "#FFFFFF"\n')
        self.assertFails(self.run_bin("lint"), "palette files are stale.")


class CheckingPassTest(Repository):
    """The passes that only check: each passes a sound tree and fails a broken one."""

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

    def test_hadolint(self):
        self.settings("function docker_files { printf '%s\\0' Dockerfile; }\n")
        self.write("Dockerfile", "FROM debian:13\nRUN cd /tmp\n")
        self.assertFails(self.run_bin("lint"), "DL3003")
        self.write("Dockerfile", "FROM debian:13\nWORKDIR /tmp\n")
        self.assertPasses(self.run_bin("lint"))

    def test_compose(self):
        self.settings("function compose_files { printf '%s\\0' a.yml; }\n")
        self.write("a.yml", "services:\n  a:\n    image: debian\n    bogus: 1\n")
        self.assertFails(self.run_bin("lint"), "a.yml: not a valid Compose file")
        self.write("a.yml", VALID_COMPOSE)
        result = self.run_bin("lint")
        self.assertPasses(result)
        self.assertEqual(self.sections(result), ["LINT COMPOSE"])

    def test_compose_checks_each_file_on_its_own(self):
        # a.yml gives its service no image, which b.yml supplies: merged, as Compose
        # merges every -f, the two would pass.
        self.settings("function compose_files { printf '%s\\0' a.yml b.yml; }\n")
        self.write("a.yml", "services:\n  a:\n    init: true\n")
        self.write("b.yml", VALID_COMPOSE)
        self.assertFails(self.run_bin("lint"), "a.yml: not a valid Compose file")
        self.write("b.yml", "services:\n  b:\n    init: true\n")
        result = self.run_bin("lint")
        self.assertFails(result, "a.yml: not a valid Compose file")
        self.assertIn("b.yml: not a valid Compose file", result.stderr)

    def test_compose_checks_services_under_a_profile(self):
        self.settings("function compose_files { printf '%s\\0' a.yml; }\n")
        self.write(
            "a.yml",
            "services:\n  a:\n    image: debian\n    profiles: [p]\n"
            "    depends_on: [missing]\n",
        )
        self.assertFails(self.run_bin("lint"), 'undefined service "missing"')
        self.write(
            "a.yml",
            "services:\n  a:\n    image: debian\n    profiles: [p]\n"
            "    depends_on: [b]\n  b:\n    image: debian\n",
        )
        self.assertPasses(self.run_bin("lint"))

    def test_workflows(self):
        self.settings("function workflow_files { printf '%s\\0' a.yml; }\n")
        self.write("a.yml", INVALID_WORKFLOW)
        self.assertFails(self.run_bin("lint"), "undefined_context")
        self.write("a.yml", VALID_WORKFLOW)
        result = self.run_bin("lint")
        self.assertPasses(result)
        self.assertEqual(self.sections(result), ["LINT WORKFLOWS"])

    def test_make(self):
        self.settings("function make_files { printf '%s\\0' Makefile; }\n")
        self.write("Makefile", LONG_BODY_MAKEFILE)
        self.assertFails(self.run_bin("lint"), "maxbodylength")
        self.write("Makefile", VALID_MAKEFILE)
        result = self.run_bin("lint")
        self.assertPasses(result)
        self.assertEqual(self.sections(result), ["LINT MAKE"])

    def test_make_reads_the_repository_s_checkmake_ini(self):
        self.settings("function make_files { printf '%s\\0' Makefile; }\n")
        self.write("Makefile", "build:\n\ttrue\n.PHONY: build\n")
        self.assertFails(self.run_bin("lint"), "minphony")
        self.write("checkmake.ini", "[minphony]\ndisabled = true\n")
        self.assertPasses(self.run_bin("lint"))


class GateTest(Repository):
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
    def test_refuses_without_a_start(self):
        self.settings("")
        self.assertFails(self.run_bin("preview"), "preview_start is not set")
        self.settings("# shellcheck disable=SC2034\npreview_start=\n")
        self.assertFails(self.run_bin("preview"), "preview_start is not set")

    def test_refuses_a_start_written_as_a_function(self):
        self.settings("function preview_start { printf '%s\\0' /pages/; }\n")
        self.assertFails(
            self.run_bin("preview"), "preview_start must be a variable, not a function"
        )

    def test_serves_every_address_and_redirects_to_the_start(self):
        self.settings("# shellcheck disable=SC2034\npreview_start=/pages/\n")
        self.write("pages/index.html", "<p>page</p>")
        server = subprocess.Popen(
            ["bin/preview"],
            cwd=self.root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(server.wait, timeout=WAIT_S)
        self.addCleanup(server.terminate)
        deadline = time.monotonic() + 30
        while True:
            if server.poll() is not None:
                self.fail(f"bin/preview exited {server.returncode}")
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:8080/", timeout=10
                ) as response:
                    self.assertEqual(response.url, "http://127.0.0.1:8080/pages/")
                    self.assertIn(b"page", response.read())
                break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        # The test container has no network but loopback, so the wildcard bind is read
        # from the kernel's socket table: 0.0.0.0:8080, listening.
        table = (
            pathlib.Path("/proc/net/tcp").read_text(encoding="utf-8").splitlines()[1:]
        )
        listening = {(line.split()[1], line.split()[3]) for line in table}
        self.assertIn(("00000000:1F90", "0A"), listening)
