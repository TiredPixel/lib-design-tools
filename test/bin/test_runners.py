"""The runners in bin/, run as another repository runs them.

They run from the root of a fabricated repository whose lib/design-tools links to this
one, whose bin/ links to the runners through it, and whose bin/settings sources the
design layer, as a repository taking this one has them.
"""

import io
import os
import pathlib
import socket
import subprocess
import tarfile
import tempfile
import textwrap
import time
import unittest.mock
import urllib.request

import temp_tree

ROOT = pathlib.Path(__file__).resolve().parents[2]

LINKED_RUNNERS = ("format", "lint", "test", "coverage", "preview")

# A generous bound on any one child process, so a hang fails its test rather than
# stalling a worker; not a measure of how long anything should take.
WAIT_S = 300

UNFORMATTED_SVG = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>\n'
FORMATTED_SVG = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0" /></svg>\n'
# Laid out as an office application might write a part, which no formatter would
# leave alone: CRLF, a doubled space, single quotes and no final newline.
OOXML_PART = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
    "<w:document xmlns:w=\"u\"  w:a='1'><w:body/></w:document>"
)
MALFORMED_XML = "<a><b></a>\n"
# Every lint and format run takes the workflows, so each tree holds one.
WORKFLOW = (
    "---\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo a\n"
    '"on": push\n'
)
WORKFLOW_LINES = (
    "lint: checking 1 sorted YAML file(s)",
    "lint: checking 1 YAML file(s) with yamllint",
    "lint: checking 1 workflow(s)",
    "format: formatting 1 sorted YAML file(s)",
)

PASSING_TEST = (
    "import unittest\n\n\nclass A(unittest.TestCase):\n    def test_a(self):\n"
    "        pass\n"
)
SKIPPING_TEST = (
    "import unittest\n\n\nclass A(unittest.TestCase):\n    def test_a(self):\n"
    "        self.skipTest('missing')\n"
)


# Set by bin/coverage, for its own pytest, the shell kcov traces and the Node under the
# tests, and dropped for each runner run here. A nested pytest would take the options
# and write into the same data, and a nested Node would write into the run's own V8
# data; and the trace descriptor BASH_XTRACEFD names is not open here, since xdist
# starts its workers without it, so a bash given BASH_ENV and BASH_XTRACEFD would write
# its trace, and a complaint, into the output tested.
COVERAGE_ENVIRONMENT = (
    "BASH_ENV",
    "BASH_XTRACEFD",
    "COVERAGE_ROOT",
    "COVERAGE_RUN_DIR",
    "NODE_V8_COVERAGE",
    "PYTEST_ADDOPTS",
)


def child_environment(dropping=COVERAGE_ENVIRONMENT):
    return {
        k: v
        for k, v in os.environ.items()
        if k not in dropping and not k.startswith("COV_CORE_")
    }


# What runner/format-host sets alongside its binds, which bin/format refuses to run
# without; the fabricated repositories here are the tree itself, so need no bind.
MOUNTED = {"FORMAT_MOUNTS": "1"}


class Repository(temp_tree.TempTreeTestCase):
    def setUp(self):
        super().setUp()
        (self.root / "lib").mkdir()
        (self.root / "lib" / "design-tools").symlink_to(ROOT)
        (self.root / "bin").mkdir()
        for runner in LINKED_RUNNERS:
            (self.root / "bin" / runner).symlink_to(f"../lib/design-tools/bin/{runner}")
        self.write(".github/workflows/a.yml", WORKFLOW)

    def write(self, name, text):
        if isinstance(text, str):
            text = textwrap.dedent(text).lstrip()
        return super().write(name, text)

    def settings(self, text):
        self.write(
            "bin/settings",
            "# shellcheck shell=bash\nsource lib/design-tools/bin/layer\n" + text,
        )

    def run_bin(
        self, runner, *args, env=None, dropping=COVERAGE_ENVIRONMENT, text=True
    ):
        return subprocess.run(
            [f"bin/{runner}", *args],
            cwd=self.root,
            env={**child_environment(dropping), **MOUNTED, **(env or {})},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=text,
            check=False,
            timeout=WAIT_S,
        )

    def assertPasses(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assertFails(self, result, message):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(message, result.stdout + result.stderr)

    def runner_lines(self, result):
        """The runners' own lines, each pass announcing itself on stderr.

        Those of the workflow passes every run makes are left out.
        """
        return [
            line
            for line in result.stderr.splitlines()
            if line.startswith(("format: ", "lint: ", "test: ", "coverage: "))
            and line not in WORKFLOW_LINES
        ]


class DesignPassTest(Repository):
    """The passes the design layer adds."""

    def test_svg(self):
        self.settings("svg_files() { printf '%s\\0' a.svg; }\n")
        self.write("a.svg", UNFORMATTED_SVG)
        self.assertFails(self.run_bin("lint"), "lint: failed: prettier-svg")
        result = self.run_bin("format")
        self.assertPasses(result)
        self.assertEqual(
            self.runner_lines(result), ["format: formatting 1 SVG file(s)"]
        )
        self.assertEqual((self.root / "a.svg").read_text(), FORMATTED_SVG)
        result = self.run_bin("lint")
        self.assertPasses(result)
        self.assertEqual(self.runner_lines(result), ["lint: checking 1 SVG file(s)"])

    def test_svg_that_does_not_parse_fails_the_lint(self):
        self.settings("svg_files() { printf '%s\\0' a.svg; }\n")
        self.write("a.svg", "<svg><path\n")
        self.assertFails(self.run_bin("lint"), "a.svg: SyntaxError")

    def test_palette(self):
        self.settings(
            "palette_args() {\n"
            "    printf '%s\\0' p.yml --prefix x --json p.json --css p.css\n"
            "}\n"
        )
        self.write("p.yml", 'a: "#000000"\n')
        result = self.run_bin("format")
        self.assertPasses(result)
        self.assertEqual(
            self.runner_lines(result), ["format: running palette-generate"]
        )
        self.assertIn("--x-a: #000000;", (self.root / "p.css").read_text())
        result = self.run_bin("lint")
        self.assertPasses(result)
        checking = "lint: running palette-generate --check"
        self.assertEqual(self.runner_lines(result), [checking])
        self.write("p.yml", 'a: "#FFFFFF"\n')
        result = self.run_bin("lint")
        self.assertFails(
            result, "palette-generate: p.json or p.css is stale against p.yml"
        )
        self.assertEqual(
            self.runner_lines(result), [checking, "lint: failed: palette-generate"]
        )

    def test_palette_lint_passes_palette_generate_s_own_failure(self):
        # Neither is staleness, so lint adds no word of its own to the tool's.
        for args, message in (
            (
                "gone.yml --prefix x --json p.json --css p.css",
                "palette-generate: gone.yml: no such file",
            ),
            ("p.yml --bogus", "Usage: palette-generate"),
        ):
            with self.subTest(args=args):
                self.settings(f"palette_args() {{ printf '%s\\0' {args}; }}\n")
                result = self.run_bin("lint")
                self.assertFails(result, message)
                self.assertNotIn("stale", result.stderr)

    def test_references(self):
        self.settings(
            "references_args() {\n    printf '%s\\0' --no-external --root . a.html\n}\n"
        )
        self.write("a.html", '<link rel="stylesheet" href="gone.css">\n')
        self.assertFails(self.run_bin("lint"), "gone.css")
        self.write("gone.css", "a {}\n")
        self.assertPasses(self.run_bin("lint"))

    def test_render(self):
        self.settings("render_args() { printf '%s\\0' --root . a.html; }\n")
        self.write("a.html", "<!doctype html>\n<script>missing();</script>\n")
        self.assertFails(self.run_bin("lint"), "a.html")
        self.write("a.html", "<!doctype html>\n<p>a</p>\n")
        result = self.run_bin("lint")
        self.assertPasses(result)
        self.assertEqual(self.runner_lines(result), ["lint: running render-check"])

    def test_ooxml(self):
        self.settings("ooxml_args() { printf '%s\\0' gone.dotx; }\n")
        result = self.run_bin("lint")
        self.assertFails(result, "lint: failed: ooxml-check")
        self.assertEqual(
            self.runner_lines(result),
            ["lint: running ooxml-check", "lint: failed: ooxml-check"],
        )

    def test_lint_only_xml_is_checked_and_never_rewritten(self):
        self.settings("xml_lint_files() { printf '%s\\0' a.xml; }\n")
        for text, message in (
            (OOXML_PART, None),
            (MALFORMED_XML, "a.xml:1:8: mismatched tag"),
            ('<a xmlns:x="u"><y:b/></a>\n', "a.xml:1:15: unbound prefix"),
        ):
            with self.subTest(message=message):
                self.write("a.xml", text.encode())
                result = self.run_bin("lint")
                if message is None:
                    self.assertPasses(result)
                else:
                    self.assertFails(result, message)
                self.assertEqual(
                    self.runner_lines(result)[0],
                    "lint: checking 1 lint-only XML file(s)",
                )
                result = self.run_bin("format")
                self.assertPasses(result)
                self.assertEqual(self.runner_lines(result), [])
                # As bytes, since reading text would fold the CRLF.
                self.assertEqual((self.root / "a.xml").read_bytes(), text.encode())

    def test_lint_only_xml_reports_every_failing_file(self):
        self.settings("xml_lint_files() { printf '%s\\0' a.xml b.xml c.xml; }\n")
        self.write("a.xml", MALFORMED_XML)
        self.write("b.xml", OOXML_PART.encode())
        self.write("c.xml", "<a>\n")
        result = self.run_bin("lint")
        self.assertFails(result, "a.xml:1:8: mismatched tag")
        self.assertIn("c.xml:2:0: no element found", result.stdout + result.stderr)
        self.assertNotIn("b.xml", result.stdout + result.stderr)


class GateTest(Repository):
    def test_named_tests_skip_the_lint_and_reach_the_tools(self):
        self.settings(
            "pytest_args() { printf '%s\\0' --no-skips; }\n"
            "references_args() { printf '%s\\0' --root . gone.html; }\n"
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
        self.write("pytest.ini", "[pytest]\npythonpath = test\n")
        result = self.run_bin("test", "test/test_a.py")
        self.assertPasses(result)
        self.assertEqual(self.runner_lines(result), ["test: running pytest"])
        self.assertIn("1 passed", result.stdout)

    def test_the_whole_gate_lints_first(self):
        self.write("a.html", "<p>a</p>\n")
        self.settings(
            "pytest_args() { printf '%s\\0' --no-skips; }\n"
            "references_args() { printf '%s\\0' --root . a.html; }\n"
        )
        self.write("test/test_a.py", PASSING_TEST)
        # Confined, since pytest given no path would also collect lib/design-tools.
        self.write("pytest.ini", "[pytest]\ntestpaths = test\n")
        result = self.run_bin("test")
        self.assertPasses(result)
        self.assertEqual(
            self.runner_lines(result),
            ["lint: running references-check", "test: running pytest"],
        )

    def test_a_failing_lint_ends_the_gate(self):
        self.settings(
            "pytest_args() { :; }\n"
            "references_args() { printf '%s\\0' --root . gone.html; }\n"
        )
        self.write("test/test_a.py", PASSING_TEST)
        result = self.run_bin("test")
        self.assertFails(result, "lint: failed: references-check")
        self.assertNotIn("test: running pytest", result.stderr)

    def test_a_failing_test_fails_the_gate(self):
        self.settings("pytest_args() { :; }\n")
        self.write("test/test_a.py", PASSING_TEST.replace("pass", "self.fail('x')"))
        result = self.run_bin("test", "test/test_a.py")
        self.assertFails(result, "test: failed: python")

    def test_the_repository_s_own_pytest_configuration_applies(self):
        self.settings("pytest_args() { :; }\n")
        self.write("pytest.ini", "[pytest]\npython_files = check_*.py\n")
        self.write("test/check_a.py", PASSING_TEST)
        self.write("test/test_ignored.py", "raise SystemExit('collected')\n")
        result = self.run_bin("test")
        self.assertPasses(result)
        self.assertIn("1 passed", result.stdout)

    def test_a_skip_fails_only_with_no_skips(self):
        self.write("test/test_a.py", SKIPPING_TEST)
        self.settings("pytest_args() { :; }\n")
        self.assertPasses(self.run_bin("test", "test/test_a.py"))
        self.settings("pytest_args() { printf '%s\\0' --no-skips; }\n")
        self.assertFails(
            self.run_bin("test", "test/test_a.py"), "skipped, which --no-skips fails"
        )

    def test_naming_tests_without_pytest_args_fails(self):
        self.settings("")
        self.write("test/test_a.py", PASSING_TEST)
        self.assertFails(
            self.run_bin("test", "test/test_a.py"),
            "test: bin/settings: pytest_args is not set, so there are no tests to name",
        )


class CoverageTest(Repository):
    # A test that runs the repository's own shell with kcov's trace descriptor, which
    # subprocess would otherwise close, so kcov can trace it, then each of COMMANDS
    # from the root, as a test runs a build, passing where PASSES holds.
    TRACING_TEST = """
        import os
        import subprocess
        import unittest


        class A(unittest.TestCase):
            def test_a(self):
                trace = int(os.environ["BASH_XTRACEFD"])
                subprocess.run(["bin/hello"], pass_fds=(trace,), check=True)
                for command in COMMANDS:
                    subprocess.run(command, check=True)
                self.assertTrue(PASSES)
    """

    BUILD_MJS = """
        if (process.argv[2] === 'a') {
          console.log('a');
        } else {
          console.log('b');
        }
    """

    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = pathlib.Path(directory.name)

    def write_test(self, *commands, passes=True):
        self.settings("pytest_args() { :; }\n")
        self.write("bin/hello", "#!/usr/bin/env bash\necho hello\n").chmod(0o755)
        self.write(
            "test/test_a.py",
            self.TRACING_TEST.replace("COMMANDS", repr(commands)).replace(
                "PASSES", repr(passes)
            ),
        )

    def run_coverage(self, *args):
        # TMPDIR of its own, so what the run leaves there shows.
        return self.run_bin(
            "coverage",
            *args,
            env={"TMPDIR": str(self.tmp)},
            dropping=(*COVERAGE_ENVIRONMENT, "COVERAGE_FILE"),
            text=False,
        )

    def tree(self):
        """Every path in the tree, with its contents, so any write shows.

        pytest's own cache is left out, since the container it lands in is discarded.
        """
        return sorted(
            (str(p.relative_to(self.root)), p.is_file() and p.read_bytes())
            for p in self.root.rglob("*")
            if ".pytest_cache" not in p.parts
        )

    def report(self, result):
        """The tar's member names, in its order, and the text of each file."""
        with tarfile.open(fileobj=io.BytesIO(result.stdout)) as tar:
            names = tar.getnames()
            files = {
                m.name: tar.extractfile(m).read().decode(errors="replace")
                for m in tar.getmembers()
                if m.isfile()
            }
        return names, files

    def assertLeavesNothing(self, before):
        self.assertEqual(self.tree(), before)
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_writes_each_report_as_a_tar_ending_in_its_index(self):
        self.write_test()
        before = self.tree()
        result = self.run_coverage("test/test_a.py")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        names, files = self.report(result)
        self.assertEqual(names[-1], "./index.html")
        index = files["./index.html"]
        for kind in ("python", "shell"):
            with self.subTest(kind=kind):
                self.assertIn(f'<a href="{kind}/index.html">', index)
                self.assertIn(f"./{kind}/index.html", names)
        self.assertNotIn("js/", index)
        self.assertTrue(any("hello" in name for name in names if "/shell/" in name))
        self.assertIn("test_a_py.html", " ".join(names))
        self.assertIn(b"test/test_a.py", result.stderr)
        self.assertLeavesNothing(before)

    def test_reports_the_javascript_node_ran_under_the_tests(self):
        self.write_test(["node", "build.mjs", "a"])
        self.write("build.mjs", self.BUILD_MJS)
        before = self.tree()
        result = self.run_coverage("test/test_a.py")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        _, files = self.report(result)
        self.assertIn('<a href="js/index.html">', files["./index.html"])
        self.assertIn("build.mjs", files["./js/index.html"])
        self.assertLeavesNothing(before)

    def test_a_repository_s_own_c8_settings_change_nothing(self):
        self.write_test(["node", "build.mjs", "a"])
        self.write("build.mjs", self.BUILD_MJS)
        self.write(".c8rc", '{"reporter": ["text"], "reports-dir": "coverage"}\n')
        before = self.tree()
        result = self.run_coverage("test/test_a.py")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn("./js/index.html", self.report(result)[0])
        self.assertLeavesNothing(before)

    def test_a_failing_test_fails_the_run_and_still_writes_the_report(self):
        self.write_test(passes=False)
        result = self.run_coverage("test/test_a.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"1 failed", result.stdout + result.stderr)
        self.assertEqual(self.report(result)[0][-1], "./index.html")

    def test_without_pytest_args_the_tar_holds_only_the_index(self):
        self.settings("")
        result = self.run_coverage()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        names, files = self.report(result)
        self.assertEqual(names, ["./index.html"])
        self.assertNotIn("<li>", files["./index.html"])

    def test_named_tests_without_pytest_args_fail(self):
        self.settings("")
        self.write("test/test_a.py", PASSING_TEST)
        result = self.run_coverage("test/test_a.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"pytest_args is not set", result.stderr)
        self.assertEqual(result.stdout, b"")


class PreviewTest(Repository):
    def test_refuses_an_argument(self):
        self.settings("")
        result = self.run_bin("preview", "a")
        self.assertEqual((result.returncode, result.stderr), (2, "Usage: preview\n"))

    def test_refuses_without_a_start(self):
        self.settings("")
        self.assertFails(
            self.run_bin("preview"), "preview: bin/settings: preview_start is not set"
        )
        self.settings("# shellcheck disable=SC2034\npreview_start=\n")
        self.assertFails(self.run_bin("preview"), "preview_start is not set")

    def test_refuses_a_start_written_as_a_function(self):
        self.settings("preview_start() { printf '%s\\0' /pages/; }\n")
        self.assertFails(
            self.run_bin("preview"),
            "preview: bin/settings: preview_start must be a variable, not a function",
        )

    def test_serves_every_address_and_redirects_to_the_start(self):
        # urlopen sends a request through the proxy http_proxy names unless no_proxy
        # matches its host, as a string, and no proxy can reach this loopback server.
        self.enterContext(unittest.mock.patch.dict(os.environ, {"no_proxy": "*"}))
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
        # Only a refused connection is retried, as bin/preview not yet listening, so a
        # slow start waits while a wrong answer fails at once.
        deadline = time.monotonic() + WAIT_S
        while True:
            if server.poll() is not None:
                self.fail(f"bin/preview exited {server.returncode}")
            try:
                socket.create_connection(("127.0.0.1", 8080), timeout=10).close()
                break
            except ConnectionRefusedError:
                if time.monotonic() > deadline:
                    self.fail(f"bin/preview did not listen within {WAIT_S}s")
                time.sleep(0.1)
        with urllib.request.urlopen("http://127.0.0.1:8080/", timeout=10) as response:
            self.assertEqual(response.url, "http://127.0.0.1:8080/pages/")
            self.assertIn(b"page", response.read())
        # The test container has no network but loopback, so the wildcard bind is read
        # from the kernel's socket table: 0.0.0.0:8080, listening.
        table = pathlib.Path("/proc/net/tcp").read_text().splitlines()[1:]
        listening = {(line.split()[1], line.split()[3]) for line in table}
        self.assertIn(("00000000:1F90", "0A"), listening)
