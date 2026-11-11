"""tools/kcov-report, on Cobertura reports in kcov 43's layout and istanbul's."""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
from unittest import mock

import temp_tree
import tools_loader

kcov_report = tools_loader.load("kcov-report")


def cobertura(source: str, classes: dict[str, dict[int, int]]) -> str:
    """A report as kcov writes one: each filename relative to the one source."""
    body = "".join(
        f'<class name="c{index}" filename="{filename}"><lines>'
        + "".join(
            f'<line number="{number}" hits="{hits}"/>' for number, hits in lines.items()
        )
        + "</lines></class>"
        for index, (filename, lines) in enumerate(classes.items())
    )
    return (
        '<?xml version="1.0" ?>\n<coverage line-rate="0.5">'
        f"<sources><source>{source}</source></sources>"
        f'<packages><package name="p"><classes>{body}</classes></package></packages>'
        "</coverage>"
    )


class KcovReportTest(temp_tree.TempTreeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.root / "project"
        self.project.mkdir()

    def report_file(self, classes: dict[str, dict[int, int]], source: str = "") -> str:
        path = self.write(
            "cobertura.xml", cobertura(source or f"{self.project}/", classes)
        )
        return str(path)

    def run_main(self, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = kcov_report.main(list(arguments))
        return status, stdout.getvalue(), stderr.getvalue()

    def run_report(self, classes: dict[str, dict[int, int]]) -> str:
        status, stdout, stderr = self.run_main(
            self.report_file(classes), str(self.project)
        )
        self.assertEqual((status, stderr), (0, ""))
        return stdout

    def run_failure(self, content: str) -> str:
        path = self.write("cobertura.xml", content)
        status, stdout, stderr = self.run_main(str(path), str(self.project))
        self.assertEqual((status, stdout), (1, ""))
        self.assertTrue(stderr.startswith(f"kcov-report: {path}: "), stderr)
        self.assertTrue(stderr.endswith("\n"))
        self.assertEqual(stderr.count("\n"), 1, stderr)
        return stderr

    def run_usage_error(self, *arguments: str) -> str:
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as caught,
        ):
            kcov_report.main(list(arguments))
        self.assertEqual(caught.exception.code, 2)
        output = stderr.getvalue()
        self.assertRegex(
            output, r"\AUsage: kcov-report .*\n(.*\n)*kcov-report: error: .+\n\Z"
        )
        return output

    def test_full_partial_and_zero_coverage_in_term_missing_form(self) -> None:
        output = self.run_report(
            {
                "bin/zero": {2: 0, 3: 0},
                "bin/full": {2: 1, 3: 4},
                "bin/partial": {2: 1, 3: 0, 4: 0, 5: 1, 7: 0},
            }
        )
        self.assertEqual(
            output,
            "Name          Stmts   Miss  Cover   Missing\n"
            "-------------------------------------------\n"
            "bin/full          2      0   100%\n"
            "bin/partial       5      3    40%   3-4, 7\n"
            "bin/zero          2      2     0%   2-3\n"
            "-------------------------------------------\n"
            "TOTAL             9      5    44%\n",
        )

    def test_missing_lines_run_across_lines_that_are_no_statement(self) -> None:
        output = self.run_report({"a.sh": {2: 0, 5: 0, 9: 1, 10: 0, 20: 0, 21: 1}})
        self.assertIn("   2-5, 10-20\n", output)

    def test_lines_out_of_order_still_collapse(self) -> None:
        output = self.run_report({"a.sh": {4: 0, 2: 0, 3: 0, 1: 1}})
        self.assertIn("   2-4\n", output)

    def test_a_line_counts_as_covered_where_any_entry_hits_it(self) -> None:
        # kcov's merged report can carry a file under more than one class.
        path = self.write(
            "cobertura.xml",
            cobertura(f"{self.project}/", {"a.sh": {2: 0, 3: 0}}).replace(
                "</classes>",
                '<class name="again" filename="a.sh"><lines>'
                '<line number="2" hits="3"/><line number="4" hits="0"/>'
                "</lines></class></classes>",
            ),
        )
        status, stdout, _ = self.run_main(str(path), str(self.project))
        self.assertEqual(status, 0)
        self.assertIn("\na.sh        3      2    33%   3-4\n", stdout)

    def test_percentages_round_to_neither_end_unless_exact(self) -> None:
        cases = {
            (0, 0): "100%",
            (1, 0): "100%",
            (3, 3): "0%",
            (3, 1): "67%",
            (1000, 1): "99%",
            (1000, 999): "1%",
        }
        for (statements, misses), expected in cases.items():
            with self.subTest(statements=statements, misses=misses):
                self.assertEqual(kcov_report.percentage(statements, misses), expected)

    def test_a_file_with_no_statements_is_fully_covered(self) -> None:
        output = self.run_report({"empty.sh": {}, "a.sh": {1: 1}})
        self.assertIn("\nempty.sh       0      0   100%\n", output)

    def test_names_join_the_source_relative_to_root(self) -> None:
        source = self.project.parent
        status, stdout, _ = self.run_main(
            self.report_file(
                {"project/bin/a": {1: 1}, "elsewhere/b": {1: 1}}, f"{source}/"
            ),
            str(self.project),
        )
        self.assertEqual(status, 0)
        self.assertIn("\nbin/a ", stdout)
        self.assertIn(f"\n{source}/elsewhere/b ", stdout)

    def test_a_relative_root_is_taken_from_the_working_directory(self) -> None:
        report = self.report_file({"bin/a": {1: 1}})
        previous = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        status, stdout, _ = self.run_main(report, "project")
        self.assertEqual(status, 0)
        self.assertIn("\nbin/a ", stdout)

    def test_the_name_column_widens_for_a_long_name(self) -> None:
        name = "a/" + "x" * 40
        lines = self.run_report({name: {1: 1}}).splitlines()
        self.assertEqual(lines[2], f"{name}       1      0   100%")
        self.assertEqual(lines[1], "-" * len(lines[0]))
        self.assertTrue(
            lines[0].startswith("Name" + " " * (len(name) - 4) + "   Stmts")
        )

    def test_kcov_43_layout(self) -> None:
        # As kcov 43 writes kcov-merged/cobertura.xml in the image, trimmed.
        path = self.write(
            "cobertura.xml",
            '<?xml version="1.0" ?>\n'
            "<!DOCTYPE coverage SYSTEM "
            "'http://cobertura.sourceforge.net/xml/coverage-04.dtd'>\n"
            '<coverage line-rate="0.769" lines-covered="10" lines-valid="13"'
            ' version="1.9" timestamp="1793890073">\n'
            f"\t<sources>\n\t\t<source>{self.project}/</source>\n\t</sources>\n"
            '\t<packages>\n\t\t<package name="child_sh" line-rate="0.769">\n'
            "\t\t\t<classes>\n"
            '\t\t\t\t<class name="main_sh__0" filename="src/main.sh"'
            ' line-rate="0.778">\n'
            "\t\t\t\t\t<lines>\n"
            '\t\t\t\t\t\t<line number="2" hits="1"/>\n'
            '\t\t\t\t\t\t<line number="5" hits="0"/>\n'
            '\t\t\t\t\t\t<line number="6" hits="0"/>\n'
            '\t\t\t\t\t\t<line number="8" hits="2"/>\n'
            "\t\t\t\t\t</lines>\n\t\t\t\t</class>\n"
            "\t\t\t</classes>\n\t\t</package>\n\t</packages>\n</coverage>\n",
        )
        status, stdout, stderr = self.run_main(str(path), str(self.project))
        self.assertEqual((status, stderr), (0, ""))
        self.assertEqual(
            stdout,
            "Name          Stmts   Miss  Cover   Missing\n"
            "-------------------------------------------\n"
            "src/main.sh       4      2    50%   5-6\n"
            "-------------------------------------------\n"
            "TOTAL             4      2    50%\n",
        )

    def test_istanbul_layout(self) -> None:
        # As istanbul-reports writes it for c8, from its test fixture
        # test/fixtures/specs/missing-line-missing-branch.json, trimmed, its <source>
        # the project, with no trailing slash, as c8 gives its working directory. A
        # method's line 8, missed, and 12 are no line of the class's own.
        path = self.write(
            "cobertura.xml",
            '<?xml version="1.0" ?>\n'
            '<!DOCTYPE coverage SYSTEM "http://cobertura.sourceforge.net/xml/'
            'coverage-04.dtd">\n'
            '<coverage lines-valid="47" lines-covered="46" line-rate="0.9787"'
            ' branches-valid="43" branches-covered="41"'
            ' branch-rate="0.9534999999999999" timestamp="123456789"'
            ' complexity="0" version="0.1">\n'
            f"  <sources>\n    <source>{self.project}</source>\n  </sources>\n"
            "  <packages>\n"
            '    <package name="main" line-rate="0.9787"'
            ' branch-rate="0.9534999999999999">\n'
            "      <classes>\n"
            '        <class name="index.js" filename="test-exclude/index.js"'
            ' line-rate="0.9787" branch-rate="0.9534999999999999">\n'
            "          <methods>\n"
            '            <method name="foo" hits="0" signature="()V">\n'
            "              <lines>\n"
            '                <line number="8" hits="0"/>\n'
            "              </lines>\n"
            "            </method>\n"
            '            <method name="TestExclude" hits="21" signature="()V">\n'
            "              <lines>\n"
            '                <line number="12" hits="21"/>\n'
            "              </lines>\n"
            "            </method>\n"
            "          </methods>\n"
            "          <lines>\n"
            '            <line number="1" hits="1" branch="false"/>\n'
            '            <line number="2" hits="1" branch="false"/>\n'
            '            <line number="3" hits="1" branch="false"/>\n'
            '            <line number="4" hits="1" branch="false"/>\n'
            '            <line number="5" hits="1" branch="false"/>\n'
            '            <line number="9" hits="0" branch="false"/>\n'
            '            <line number="13" hits="21" branch="false"/>\n'
            '            <line number="21" hits="21" branch="true"'
            ' condition-coverage="75% (3/4)"/>\n'
            '            <line number="22" hits="21" branch="true"'
            ' condition-coverage="100% (2/2)"/>\n'
            "          </lines>\n"
            "        </class>\n"
            "      </classes>\n"
            "    </package>\n"
            "  </packages>\n"
            "</coverage>\n",
        )
        status, stdout, stderr = self.run_main(str(path), str(self.project))
        self.assertEqual((status, stderr), (0, ""))
        self.assertEqual(
            stdout,
            "Name                    Stmts   Miss  Cover   Missing\n"
            "-----------------------------------------------------\n"
            "test-exclude/index.js       9      1    89%   9\n"
            "-----------------------------------------------------\n"
            "TOTAL                       9      1    89%\n",
        )

    def test_malformed_reports_fail(self) -> None:
        source = f"<sources><source>{self.project}/</source></sources>"
        cases = {
            "not XML": "<coverage>",
            "root element is <report>, not <coverage>": "<report/>",
            "holds 0 <source> elements, not one": "<coverage/>",
            "<source> is empty": (
                "<coverage><sources><source> </source></sources></coverage>"
            ),
            "<source> is empty ": "<coverage><sources><source/></sources></coverage>",
            "holds 2 <source> elements, not one": (
                "<coverage><sources><source>/a</source><source>/b</source>"
                "</sources></coverage>"
            ),
            "<class name='c'> has no filename": (
                f'<coverage>{source}<class name="c"/></coverage>'
            ),
            "a.sh: <line> without integer number and hits": (
                f'<coverage>{source}<class filename="a.sh">'
                '<lines><line number="x" hits="1"/></lines></class></coverage>'
            ),
            "a.sh: <line> without integer number and hits ": (
                f'<coverage>{source}<class filename="a.sh">'
                '<lines><line number="1"/></lines></class></coverage>'
            ),
            "measures no file": f"<coverage>{source}</coverage>",
            # As c8 writes one where Node ran none of the files it reports.
            "measures no file ": (
                f"<coverage>{source}<packages></packages></coverage>"
            ),
        }
        for message, content in cases.items():
            with self.subTest(message=message):
                self.assertIn(message.strip(), self.run_failure(content))

    def test_an_unreadable_report_fails(self) -> None:
        # Raised through parse, since root reads a file whatever its mode.
        path = self.report_file({"a": {1: 1}})
        cases = {
            "Permission denied": PermissionError(13, "Permission denied"),
            "no strerror": OSError("no strerror"),
        }
        for message, error in cases.items():
            with (
                self.subTest(message=message),
                mock.patch.object(kcov_report.ElementTree, "parse", side_effect=error),
            ):
                status, stdout, stderr = self.run_main(path, str(self.project))
                self.assertEqual((status, stdout), (1, ""))
                self.assertEqual(
                    stderr, f"kcov-report: {path}: cannot read: {message}\n"
                )

    def test_usage_errors(self) -> None:
        report = self.report_file({"a": {1: 1}})
        project = str(self.project)
        cases = {
            "is not a file": [str(self.root / "missing.xml"), project],
            "is not a directory": [report, report],
            "ROOT": [report],
            "COBERTURA_XML": [],
            "unrecognized arguments": [report, project, project],
        }
        for message, arguments in cases.items():
            with self.subTest(message=message):
                self.assertIn(message, self.run_usage_error(*arguments))

    def test_a_missing_report_is_a_usage_error(self) -> None:
        missing = self.root / "missing.xml"
        output = self.run_usage_error(str(missing), str(self.project))
        self.assertTrue(output.endswith(f"error: {missing} is not a file\n"))

    def test_help_opens_with_usage(self) -> None:
        stdout = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            self.assertRaises(SystemExit) as caught,
        ):
            kcov_report.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertTrue(
            stdout.getvalue().startswith("Usage: kcov-report [-h] COBERTURA_XML ROOT\n")
        )

    def test_runs_as_a_command(self) -> None:
        result = subprocess.run(
            [
                str(tools_loader.TOOLS / "kcov-report"),
                self.report_file({"a.sh": {1: 1, 2: 0}}),
                str(self.project),
            ],
            capture_output=True,
            text=True,
            check=False,
            # Generous, so a hung child fails the test rather than stalling a worker;
            # not a measure of how long it should take.
            timeout=300,
        )
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertIn("\na.sh        2      1    50%   2\n", result.stdout)
