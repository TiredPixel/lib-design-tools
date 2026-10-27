"""A test case working in a fresh temporary directory tree, for testing."""

from __future__ import annotations

import pathlib
import tempfile
import unittest


class TempTreeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = pathlib.Path(directory.name)

    def write(self, name: str, content: str | bytes) -> pathlib.Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path
