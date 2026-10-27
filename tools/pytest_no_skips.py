"""A pytest plugin adding --no-skips, which fails a run in which any test skipped.

For a gate whose image carries every tool its suites look for, where a skip means
something is missing. bin/test loads it with ``-p``, with this directory on
``PYTHONPATH``; the option does nothing unless passed.
"""

import pytest

_skipped = []


def pytest_addoption(parser):
    parser.addoption(
        "--no-skips",
        action="store_true",
        help="fail the run if any test is skipped",
    )


# Several sessions can run in one process, as pytest.main called twice does.
def pytest_sessionstart(session):
    _skipped.clear()


def pytest_runtest_logreport(report):
    if report.skipped and not hasattr(report, "wasxfail"):
        _skipped.append(report.nodeid)


# A module skipped as it is collected, such as by pytest.importorskip, reports here
# rather than as a test.
def pytest_collectreport(report):
    if report.skipped:
        _skipped.append(report.nodeid)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    # The controller sees every worker's reports; a worker's own status is not the
    # run's.
    if hasattr(session.config, "workerinput"):
        return
    # A run whose every module skipped collects nothing; any other status already
    # fails the run, and says why.
    if (
        session.config.getoption("--no-skips")
        and _skipped
        and exitstatus in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED)
    ):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter, config):
    if config.getoption("--no-skips") and _skipped:
        terminalreporter.section("skipped, which --no-skips fails", red=True)
        for nodeid in _skipped:
            terminalreporter.line(nodeid)
