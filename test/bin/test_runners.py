"""The runners in bin/, run as another repository runs them.

They run from the root of a fabricated repository whose bin/ links to them.
"""

import os
import pathlib
import signal
import subprocess
import tempfile
import textwrap
import time
import urllib.error
import urllib.request

import temp_tree

BIN = pathlib.Path(__file__).resolve().parents[2] / "bin"

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


# Set by bin/coverage, for its own pytest and the shell kcov traces, and dropped for
# each runner run here. A nested pytest would take the options and write into the same
# data; and the trace descriptor BASH_XTRACEFD names is not open here, since xdist
# starts its workers without it, so a bash given BASH_ENV and BASH_XTRACEFD would write
# its trace, and a complaint, into the output tested.
COVERAGE_ENVIRONMENT = ("BASH_ENV", "BASH_XTRACEFD", "PYTEST_ADDOPTS")


def child_environment(dropping=COVERAGE_ENVIRONMENT):
    return {k: v for k, v in os.environ.items() if k not in dropping}


class Repository(temp_tree.TempTreeTestCase):
    def setUp(self):
        super().setUp()
        (self.root / "bin").mkdir()
        for runner in ("format", "lint", "test", "coverage", "preview"):
            (self.root / "bin" / runner).symlink_to(BIN / runner)

    def write(self, name, text):
        return super().write(name, textwrap.dedent(text).lstrip())

    def settings(self, text):
        self.write("bin/settings", "# shellcheck shell=bash\n" + text)

    def run_bin(self, runner, *args, env=None, dropping=COVERAGE_ENVIRONMENT):
        return subprocess.run(
            [f"bin/{runner}", *args],
            cwd=self.root,
            env={**child_environment(dropping), **(env or {})},
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

    def runner_lines(self, result):
        """The runners' own lines, each pass announcing itself on stderr."""
        return [
            line
            for line in result.stderr.splitlines()
            if line.startswith(("format: ", "lint: ", "test: ", "coverage: "))
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
                self.assertEqual(result.stdout, "")
                self.assertEqual(self.runner_lines(result), [])
        self.assertEqual(
            (self.root / "a.yml").read_text(encoding="utf-8"), UNSORTED_YAML
        )

    def test_a_pass_writes_nothing_of_its_own_to_stdout(self):
        # jq writes into the file and diff finds nothing, so stdout carries only what
        # the runner itself would add.
        self.write("a.json", '{"a": 1}')
        self.settings("function json_files { printf '%s\\0' a.json; }\n")
        for runner, line in (
            ("format", "format: formatting 1 JSON file(s)"),
            ("lint", "lint: checking 1 JSON file(s)"),
        ):
            with self.subTest(runner=runner):
                result = self.run_bin(runner)
                self.assertPasses(result)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, line + "\n")

    def test_only_the_defined_passes_run(self):
        self.write("a.html", "<p>a</p>\n")
        self.settings("function references_args { printf '%s\\0' --root . a.html; }\n")
        result = self.run_bin("lint")
        self.assertPasses(result)
        self.assertEqual(self.runner_lines(result), ["lint: running references-check"])

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
        self.assertEqual(self.runner_lines(result), ["lint: checking 1 shell file(s)"])
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
        self.assertEqual(self.runner_lines(result), ["lint: checking 6 shell file(s)"])

    def test_each_new_set_runs_its_own_pass_alone(self):
        self.write("a.md", "a\n")
        self.write("a.toml", "a = 1\n")
        self.write("a.env", "A=1\n")
        self.write("Dockerfile", FORMATTED_DOCKERFILE)
        self.write("compose.yml", VALID_COMPOSE)
        self.write("workflow.yml", VALID_WORKFLOW)
        self.write("Makefile", VALID_MAKEFILE)
        # Each case gives the kind the passes name, and whether bin/format has a pass.
        cases = (
            ("md_files", "a.md", "Markdown", True),
            ("toml_files", "a.toml", "TOML", True),
            ("dotenv_files", "a.env", "dotenv", True),
            ("docker_files", "Dockerfile", "Docker", True),
            ("compose_files", "compose.yml", "Compose", False),
            ("workflow_files", "workflow.yml", "workflow", False),
            ("make_files", "Makefile", "make", False),
        )
        for name, path, kind, formats in cases:
            with self.subTest(name=name):
                self.settings(f"function {name} {{ printf '%s\\0' {path}; }}\n")
                for runner, lines in (
                    (
                        "format",
                        [f"format: formatting 1 {kind} file(s)"] if formats else [],
                    ),
                    ("lint", [f"lint: checking 1 {kind} file(s)"]),
                ):
                    result = self.run_bin(runner)
                    self.assertPasses(result)
                    self.assertEqual(self.runner_lines(result), lines)
                self.settings(f"function {name} {{ :; }}\n")
                self.assertFails(
                    self.run_bin("lint"), f"bin/settings: {name} names nothing"
                )


class SetFileTest(Repository):
    """read_set's file is gone from TMPDIR before the settings function runs.

    The JSON lint pass reads the set, and neither jq nor diff writes to TMPDIR.
    """

    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = pathlib.Path(directory.name)
        self.write("a.json", '{\n  "a": 1\n}\n')

    def run_lint(self):
        return self.run_bin("lint", env={"TMPDIR": str(self.tmp)})

    def test_a_signal_leaves_no_file_behind(self):
        # $$ in the background job is still the runner's process ID, so the runner is
        # killed while it waits on the job, once the set is written.
        self.settings("function json_files { printf '%s\\0' a.json; kill -TERM $$; }\n")
        result = self.run_lint()
        self.assertEqual(
            result.returncode, -signal.SIGTERM, result.stdout + result.stderr
        )
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_the_file_is_gone_while_the_function_runs(self):
        self.settings(
            "function json_files {\n"
            '    if [ -n "$(ls -A -- "$TMPDIR")" ]; then\n'
            '        echo "TMPDIR is not empty" >&2\n'
            "        return 1\n"
            "    fi\n"
            "    printf '%s\\0' a.json\n"
            "}\n"
        )
        result = self.run_lint()
        self.assertPasses(result)
        self.assertEqual(self.runner_lines(result), ["lint: checking 1 JSON file(s)"])
        self.assertEqual(list(self.tmp.iterdir()), [])


class FormatThenLintTest(Repository):
    """Each writing pass: lint fails a file format would change or cannot fix."""

    def assertFormatFixes(self, name, before, after, kind, count=1):
        self.write(name, before)
        self.assertNotEqual(self.run_bin("lint").returncode, 0)
        result = self.run_bin("format")
        self.assertPasses(result)
        self.assertEqual(
            self.runner_lines(result), [f"format: formatting {count} {kind} file(s)"]
        )
        self.assertEqual((self.root / name).read_text(encoding="utf-8"), after)
        result = self.run_bin("lint")
        self.assertPasses(result)
        self.assertEqual(
            self.runner_lines(result), [f"lint: checking {count} {kind} file(s)"]
        )

    def test_shell(self):
        self.settings("function shell_files { printf '%s\\0' a.sh; }\n")
        self.assertFormatFixes(
            "a.sh",
            "#!/bin/bash\nf() {\n  echo a > /dev/null\n}\n",
            "#!/bin/bash\nf() {\n    echo a >/dev/null\n}\n",
            "shell",
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
        self.assertFormatFixes("a.py", "x = ( 1 )\n", "x = 1\n", "Python")

    def test_javascript(self):
        self.settings("function js_files { printf '%s\\0' a.js; }\n")
        self.assertFormatFixes(
            "a.js",
            "const a = 1\nconsole.log(a)\n",
            "const a = 1;\nconsole.log(a);\n",
            "JavaScript",
        )

    def test_javascript_lint_passes_semistandards_own_report(self):
        self.settings("function js_files { printf '%s\\0' a.js; }\n")
        self.write("a.js", "const a = 1\nconsole.log(a);\n")
        self.assertFails(self.run_bin("lint"), "a.js:1:12: Missing semicolon.")

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
            "CSS and HTML",
            count=2,
        )

    def test_css_alone(self):
        self.settings("function css_files { printf '%s\\0' a.css; }\n")
        self.assertFormatFixes(
            "a.css", "a{color:red}\n", "a {\n  color: red;\n}\n", "CSS and HTML"
        )

    def test_markdown(self):
        # A paragraph is joined onto one line, and the front matter and the GFM table
        # come through as front matter and a table, padded to their columns, rather than
        # as a thematic break and a paragraph.
        self.settings("function md_files { printf '%s\\0' a.md; }\n")
        self.write("a.md", UNFORMATTED_MARKDOWN)
        self.assertFails(self.run_bin("lint"), "a.md")
        self.assertFormatFixes(
            "a.md", UNFORMATTED_MARKDOWN, FORMATTED_MARKDOWN, "Markdown"
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

    def test_dotenv(self):
        self.settings("function dotenv_files { printf '%s\\0' a.env; }\n")
        self.assertFormatFixes("a.env", "a=1\n", "A=1\n", "dotenv")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["a.env", "bin"])

    def test_dotenv_finding_format_cannot_fix_fails_the_lint(self):
        self.settings("function dotenv_files { printf '%s\\0' a.env; }\n")
        self.write("a.env", "=1\n")
        self.assertPasses(self.run_bin("format"))
        self.assertFails(self.run_bin("lint"), "a.env:1 LeadingCharacter")

    def test_dockerfile(self):
        self.settings("function docker_files { printf '%s\\0' Dockerfile; }\n")
        # hadolint passes this file, so the failure is dockerfmt's check.
        self.write("Dockerfile", UNFORMATTED_DOCKERFILE)
        self.assertFails(self.run_bin("lint"), "Dockerfile is not formatted")
        self.assertFormatFixes(
            "Dockerfile", UNFORMATTED_DOCKERFILE, FORMATTED_DOCKERFILE, "Docker"
        )

    def test_palette(self):
        self.settings(
            "function palette_args {\n"
            "    printf '%s\\0' p.yml --prefix x --json p.json --css p.css\n"
            "}\n"
        )
        self.write("p.yml", 'a: "#000000"\n')
        result = self.run_bin("format")
        self.assertPasses(result)
        self.assertEqual(
            self.runner_lines(result), ["format: running palette-generate"]
        )
        self.assertIn(
            "--x-a: #000000;", (self.root / "p.css").read_text(encoding="utf-8")
        )
        result = self.run_bin("lint")
        self.assertPasses(result)
        checking = "lint: running palette-generate --check"
        self.assertEqual(self.runner_lines(result), [checking])
        self.write("p.yml", 'a: "#FFFFFF"\n')
        result = self.run_bin("lint")
        self.assertFails(result, "lint: generated palette files are stale")
        self.assertEqual(
            self.runner_lines(result),
            [checking, "lint: generated palette files are stale"],
        )


class FormatCarriesOnTest(Repository):
    """A failing pass fails the format run, and every later pass still runs."""

    def assertText(self, name, text):
        self.assertEqual((self.root / name).read_text(encoding="utf-8"), text)

    def test_an_unfixable_python_finding_is_still_formatted(self):
        self.settings(
            "function python_files { printf '%s\\0' a.py; }\n"
            "function yaml_files { printf '%s\\0' a.yml; }\n"
        )
        self.write("a.py", "x = ( undefined )\n")
        self.write("a.yml", UNSORTED_YAML)
        self.assertFails(self.run_bin("format"), "F821")
        self.assertText("a.py", "x = undefined\n")
        self.assertText("a.yml", SORTED_YAML)

    def test_a_python_syntax_error_leaves_the_later_passes_to_run(self):
        self.settings(
            "function python_files { printf '%s\\0' a.py; }\n"
            "function yaml_files { printf '%s\\0' a.yml; }\n"
        )
        self.write("a.py", "x = (\n")
        self.write("a.yml", UNSORTED_YAML)
        self.assertFails(self.run_bin("format"), "invalid-syntax")
        self.assertText("a.py", "x = (\n")
        self.assertText("a.yml", SORTED_YAML)

    def test_an_unfixable_javascript_finding_leaves_the_later_passes_to_run(self):
        self.settings(
            "function js_files { printf '%s\\0' a.js; }\n"
            "function toml_files { printf '%s\\0' a.toml; }\n"
        )
        self.write("a.js", "foo()\n")
        self.write("a.toml", "a=1\n")
        self.assertFails(self.run_bin("format"), "no-undef")
        self.assertText("a.js", "foo();\n")
        self.assertText("a.toml", "a = 1\n")

    def test_each_pass_s_failure_leaves_the_last_pass_to_run(self):
        # Each set names a file its tool fails on, or one that is missing, where before
        # is None. after is what the file holds once the run is over, and kind what the
        # pass says it formats, None for the palette, which counts no files.
        cases = (
            (
                "palette_args",
                "gone.yml --prefix x --json p.json --css p.css",
                None,
                None,
                None,
            ),
            ("shell_files", "a.sh", "f() {\n", "f() {\n", "shell"),
            ("python_files", "a.py", "x = (\n", "x = (\n", "Python"),
            ("yaml_files", "a.yml", "a: [\n", "a: [\n", "YAML"),
            ("json_files", "a.json", '{"a": }\n', '{"a": }\n', "JSON"),
            ("js_files", "a.js", "foo()\n", "foo();\n", "JavaScript"),
            ("css_files", "gone.css", None, None, "CSS and HTML"),
            ("md_files", "gone.md", None, None, "Markdown"),
            ("toml_files", "a.toml", "a =\n", "a =\n", "TOML"),
        )
        for name, args, before, after, kind in cases:
            announcement = (
                "format: running palette-generate"
                if kind is None
                else f"format: formatting 1 {kind} file(s)"
            )
            with self.subTest(name=name):
                self.settings(
                    f"function {name} {{ printf '%s\\0' {args}; }}\n"
                    "function docker_files { printf '%s\\0' Dockerfile; }\n"
                )
                if before is not None:
                    self.write(args, before)
                self.write("Dockerfile", UNFORMATTED_DOCKERFILE)
                result = self.run_bin("format")
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(
                    self.runner_lines(result),
                    [announcement, "format: formatting 1 Docker file(s)"],
                )
                if after is not None:
                    self.assertText(args, after)
                self.assertText("Dockerfile", FORMATTED_DOCKERFILE)

    def test_a_failing_last_pass_fails_the_run(self):
        # The other tests end on a pass that succeeds, so only this one fails the last
        # pass, whose recorded failure no later pass can show.
        self.settings(
            "function toml_files { printf '%s\\0' a.toml; }\n"
            "function docker_files { printf '%s\\0' gone; }\n"
        )
        self.write("a.toml", "a=1\n")
        result = self.run_bin("format")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.runner_lines(result),
            [
                "format: formatting 1 TOML file(s)",
                "format: formatting 1 Docker file(s)",
            ],
        )
        self.assertText("a.toml", "a = 1\n")


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
        self.assertEqual(self.runner_lines(result), ["lint: running render-check"])

    def test_hadolint(self):
        self.settings("function docker_files { printf '%s\\0' Dockerfile; }\n")
        self.write("Dockerfile", "FROM debian:13\nRUN cd /tmp\n")
        self.assertFails(self.run_bin("lint"), "DL3003")
        self.write("Dockerfile", "FROM debian:13\nWORKDIR /tmp\n")
        self.assertPasses(self.run_bin("lint"))

    def test_dockerfile_locks(self):
        self.settings("function docker_files { printf '%s\\0' Dockerfile; }\n")
        # Pinned, so hadolint passes it and the failure is the lock lint's.
        self.write(
            "Dockerfile", "FROM debian:13\nRUN npm install --global prettier@3.9.10\n"
        )
        self.assertFails(
            self.run_bin("lint"), "Dockerfile:2: npm install resolves versions itself"
        )
        self.write("Dockerfile", "FROM debian:13\nRUN npm ci --prefix /opt/js\n")
        self.assertPasses(self.run_bin("lint"))

    def test_compose(self):
        self.settings("function compose_files { printf '%s\\0' a.yml; }\n")
        self.write("a.yml", "services:\n  a:\n    image: debian\n    bogus: 1\n")
        self.assertFails(self.run_bin("lint"), "a.yml: not a valid Compose file")
        self.write("a.yml", VALID_COMPOSE)
        result = self.run_bin("lint")
        self.assertPasses(result)
        self.assertEqual(
            self.runner_lines(result), ["lint: checking 1 Compose file(s)"]
        )

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
        self.assertEqual(
            self.runner_lines(result), ["lint: checking 1 workflow file(s)"]
        )

    def test_make(self):
        self.settings("function make_files { printf '%s\\0' Makefile; }\n")
        self.write("Makefile", LONG_BODY_MAKEFILE)
        self.assertFails(self.run_bin("lint"), "maxbodylength")
        self.write("Makefile", VALID_MAKEFILE)
        result = self.run_bin("lint")
        self.assertPasses(result)
        self.assertEqual(self.runner_lines(result), ["lint: checking 1 make file(s)"])

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
        self.assertEqual(self.runner_lines(result), ["test: running pytest"])
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
        self.assertEqual(
            self.runner_lines(result),
            ["lint: running references-check", "test: running pytest"],
        )

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
        # Each count as the message quotes it, shell-quoted.
        for count, quoted in (("0", "0"), ("two", "two"), ("", "''")):
            with self.subTest(count=count):
                result = self.run_bin(
                    "test", env={"PYTEST_XDIST_AUTO_NUM_WORKERS": count}
                )
                self.assertFails(result, "not a whole number")
                self.assertEqual(
                    self.runner_lines(result),
                    [
                        (
                            f"test: PYTEST_XDIST_AUTO_NUM_WORKERS is {quoted}, not a"
                            " whole number of at least 1"
                        )
                    ],
                )


class CoverageTest(Repository):
    # A test that runs the repository's own shell with kcov's trace descriptor, which
    # subprocess would otherwise close, so kcov can trace it.
    TRACING_TEST = """
        import os
        import subprocess
        import unittest


        class A(unittest.TestCase):
            def test_a(self):
                trace = int(os.environ["BASH_XTRACEFD"])
                subprocess.run(["bin/hello"], pass_fds=(trace,), check=True)
    """

    def setUp(self):
        super().setUp()
        self.out = pathlib.Path(os.path.realpath(self.root)) / "tmp" / "coverage"

    def run_coverage(self, *args):
        # Without the coverage of a run around this one, so the run measures on its own.
        return self.run_bin(
            "coverage",
            *args,
            dropping=(
                *COVERAGE_ENVIRONMENT,
                "COVERAGE_FILE",
                *(k for k in os.environ if k.startswith("COV_CORE_")),
            ),
        )

    def tree(self):
        """Every path outside tmp/, so a run's writes elsewhere show."""
        return sorted(
            str(p.relative_to(self.root))
            for p in self.root.rglob("*")
            if p.relative_to(self.root).parts[0] != "tmp"
        )

    def test_writes_both_reports_of_the_repository_s_own_files(self):
        self.settings("function pytest_args { :; }\n")
        self.write("test/test_a.py", self.TRACING_TEST)
        self.write("bin/hello", "#!/usr/bin/env bash\necho hello\n").chmod(0o755)
        before = self.tree()
        result = self.run_coverage("test/test_a.py")
        self.assertPasses(result)
        self.assertEqual(
            self.runner_lines(result),
            [
                "coverage: running bin/test under kcov",
                "test: running pytest",
                "coverage: shell report written to tmp/coverage/shell/index.html",
                "coverage: python report written to tmp/coverage/python/index.html",
            ],
        )
        self.assertIn("1 passed", result.stdout)
        self.assertEqual(self.tree(), before)
        (shell,) = self.out.glob("shell/test.*/cobertura.xml")
        shell_report = shell.read_text(encoding="utf-8")
        self.assertIn("hello", shell_report)
        # This repository's runners, which a linking repository does not own.
        for runner in ("runner", "settings", "test"):
            with self.subTest(runner=runner):
                self.assertNotIn(f'filename="{runner}"', shell_report)
                self.assertNotIn(str(BIN / runner), shell_report)
        self.assertIn(
            "test_a.py",
            (self.out / "python" / "index.html").read_text(encoding="utf-8"),
        )
        self.assertTrue((self.out / "python.data").is_file())

    def test_replaces_the_last_report(self):
        self.settings("")
        self.write("tmp/coverage/stale", "")
        self.write("tmp/coverage/shell/stale", "")
        self.write("tmp/kept", "")
        result = self.run_coverage()
        self.assertPasses(result)
        self.assertEqual(self.runner_lines(result)[0], f"coverage: removing {self.out}")
        self.assertFalse((self.out / "stale").exists())
        self.assertFalse((self.out / "shell" / "stale").exists())
        self.assertTrue((self.out / "shell" / "index.html").is_file())
        self.assertTrue((self.root / "tmp" / "kept").is_file())

    def test_without_pytest_args_writes_only_the_shell_report(self):
        self.settings("")
        result = self.run_coverage()
        self.assertPasses(result)
        self.assertEqual(
            self.runner_lines(result),
            [
                "coverage: running bin/test under kcov",
                "coverage: shell report written to tmp/coverage/shell/index.html",
            ],
        )
        self.assertFalse((self.out / "python").exists())

    def test_a_failing_test_fails_the_run(self):
        self.settings("function pytest_args { :; }\n")
        self.write(
            "test/test_a.py",
            PASSING_TEST.replace("pass", "self.fail('fails')"),
        )
        result = self.run_coverage("test/test_a.py")
        self.assertFails(result, "1 failed")
        self.assertNotIn("report written", result.stderr)

    def test_named_tests_without_pytest_args_fail(self):
        self.settings("")
        self.write("test/test_a.py", PASSING_TEST)
        self.assertFails(self.run_coverage("test/test_a.py"), "pytest_args is not set")


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
            env=child_environment(),
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
