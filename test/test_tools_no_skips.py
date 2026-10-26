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


class NoSkipsTest(unittest.TestCase):
    def run_pytest(self, suite, *args):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = pathlib.Path(directory.name)
        (root / "pytest.ini").write_text("[pytest]\n")
        (root / "test_suite.py").write_text(textwrap.dedent(suite))
        environment = {**os.environ, "PYTHONPATH": str(tools_loader.TOOLS)}
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "pytest_no_skips", *args],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

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

    def test_a_skip_passes_without_the_option(self):
        result = self.run_pytest(SKIPPING)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("--no-skips fails", result.stdout)
