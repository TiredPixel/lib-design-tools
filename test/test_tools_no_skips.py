"""tools/pytest_no_skips.py, run by a pytest of its own on fabricated suites."""

import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest

import tools_loader

SUITE = """
    import unittest


    class T(unittest.TestCase):
        def test_passes(self):
            pass

        @unittest.expectedFailure
        def test_xfails(self):
            self.fail()
"""

SKIPPING = (
    SUITE
    + """
        def test_skips(self):
            self.skipTest("missing")
"""
)


INTERRUPTING = (
    SKIPPING
    + """
        def test_z_interrupts(self):
            raise KeyboardInterrupt
"""
)

# Fails inside pytest itself once the skip has been reported, which pytest answers
# with an internal error.
FAILING_HOOK = """
    def pytest_runtest_logfinish(nodeid, location):
        if nodeid.endswith("test_xfails"):
            raise RuntimeError("hook fails")
"""

TWO_RUNS = """
    import pytest

    statuses = [
        int(pytest.main(["-p", "pytest_no_skips", "--no-skips", name]))
        for name in ("test_skipping.py", "test_passing.py")
    ]
    print("statuses", *statuses)
"""


class NoSkipsTest(unittest.TestCase):
    def run_python(self, files, *args):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = pathlib.Path(directory.name)
        (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        for name, content in files.items():
            (root / name).write_text(textwrap.dedent(content), encoding="utf-8")
        environment = {**os.environ, "PYTHONPATH": str(tools_loader.TOOLS)}
        return subprocess.run(
            [sys.executable, *args],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def run_pytest(self, suite, *args, conftest=None):
        files = {"test_suite.py": suite}
        if conftest is not None:
            files["conftest.py"] = conftest
        return self.run_python(files, "-m", "pytest", "-p", "pytest_no_skips", *args)

    def test_a_skip_fails_the_run(self):
        for args in ([], ["-n", "2"]):
            with self.subTest(args=args):
                result = self.run_pytest(SKIPPING, "--no-skips", *args)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("skipped, which --no-skips fails", result.stdout)
                self.assertIn("test_suite.py::T::test_skips", result.stdout)

    def test_no_skip_passes_and_an_expected_failure_is_not_a_skip(self):
        for args in ([], ["-n", "2"]):
            with self.subTest(args=args):
                result = self.run_pytest(SUITE, "--no-skips", *args)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertNotIn("--no-skips fails", result.stdout)

    def test_a_module_skipped_at_collection_fails_the_run(self):
        skipping_module = (
            'import pytest\n\npytest.importorskip("no_such_module_here")\n'
            + textwrap.dedent(SUITE)
        )
        for args in ([], ["-n", "2"]):
            with self.subTest(args=args):
                result = self.run_pytest(skipping_module, "--no-skips", *args)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("skipped, which --no-skips fails", result.stdout)
                self.assertIn("test_suite.py", result.stdout)

    def test_a_second_run_in_one_process_does_not_inherit_skips(self):
        result = self.run_python(
            {
                "run_twice.py": TWO_RUNS,
                "test_skipping.py": SKIPPING,
                "test_passing.py": SUITE,
            },
            "run_twice.py",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("statuses 1 0\n", result.stdout)

    def test_an_interrupted_run_keeps_its_status(self):
        result = self.run_pytest(INTERRUPTING, "--no-skips")
        self.assertEqual(result.returncode, 2, result.stdout)

    def test_an_internal_error_keeps_its_status(self):
        result = self.run_pytest(SKIPPING, "--no-skips", conftest=FAILING_HOOK)
        self.assertEqual(result.returncode, 3, result.stdout)
        self.assertIn("hook fails", result.stdout + result.stderr)

    def test_a_skip_passes_without_the_option(self):
        result = self.run_pytest(SKIPPING)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("--no-skips fails", result.stdout)
