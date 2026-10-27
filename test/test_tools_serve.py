"""tools/preview-serve, on a fabricated tree served from a thread."""

from __future__ import annotations

import contextlib
import http.client
import io
import pathlib
import tempfile
import threading
import unittest
from unittest import mock

import tools_loader

preview_serve = tools_loader.load("preview-serve")


class ServeTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = pathlib.Path(directory.name)
        (root / "preview").mkdir()
        (root / "preview/index.html").write_text("<p>preview</p>", encoding="utf-8")
        (root / "preview/app.webmanifest").write_text("{}", encoding="utf-8")
        (root / "preview/logo.svg").write_text("<svg/>", encoding="utf-8")
        (root / "preview/font.ttf").write_bytes(b"\0\1")
        (root / "listed").mkdir()
        (root / "listed/a.txt").write_text("a", encoding="utf-8")
        (root / ".git").mkdir()
        (root / ".git/config").write_text("secret", encoding="utf-8")
        (root / ".env").write_text("secret", encoding="utf-8")
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (pathlib.Path(outside.name) / "x.txt").write_text("secret", encoding="utf-8")
        (root / "public-config").symlink_to(".env")
        (root / "listed/git").symlink_to("../.git")
        (root / "outside").symlink_to(pathlib.Path(outside.name) / "x.txt")
        (root / "preview/alias.html").symlink_to("index.html")
        (root / "linked").symlink_to("listed")
        (root / "public").mkdir()
        (root / "public/index.html").symlink_to("../.env")
        (root / "away").mkdir()
        (root / "away/index.htm").symlink_to(pathlib.Path(outside.name) / "x.txt")
        quiet = mock.patch.object(preview_serve.PreviewHandler, "log_message")
        quiet.start()
        self.addCleanup(quiet.stop)
        server = preview_serve.make_server(root, "127.0.0.1", 0, "/preview/")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def request(self, path: str, method: str = "GET") -> http.client.HTTPResponse:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(connection.close)
        connection.request(method, path)
        response = connection.getresponse()
        response.body = response.read()
        return response

    def test_root_redirects_to_the_index(self) -> None:
        for method in ("GET", "HEAD"):
            with self.subTest(method=method):
                response = self.request("/", method)
                self.assertEqual(response.status, 302)
                self.assertEqual(response.getheader("Location"), "/preview/")

    def test_query_on_root_still_redirects(self) -> None:
        self.assertEqual(self.request("/?x=1").status, 302)

    def test_serves_the_page_uncached(self) -> None:
        response = self.request("/preview/")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"<p>preview</p>")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(response.getheader("Content-Type"), "text/html; charset=utf-8")

    def test_errors_are_uncached_too(self) -> None:
        response = self.request("/missing")
        self.assertEqual(response.status, 404)
        self.assertEqual(response.getheader("Cache-Control"), "no-store")

    def test_types(self) -> None:
        cases = {
            "/preview/app.webmanifest": "application/manifest+json; charset=utf-8",
            "/preview/logo.svg": "image/svg+xml; charset=utf-8",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(self.request(path).getheader("Content-Type"), expected)
        binary = self.request("/preview/font.ttf").getheader("Content-Type")
        self.assertNotIn("charset", binary)

    def test_lists_a_directory_without_an_index(self) -> None:
        response = self.request("/listed/")
        self.assertEqual(response.status, 200)
        self.assertIn(b"a.txt", response.body)

    def test_refuses_dotfiles_however_spelt(self) -> None:
        for path in ("/.git/config", "/.env", "/%2Egit/config", "/listed/../.env"):
            for method in ("GET", "HEAD"):
                with self.subTest(path=path, method=method):
                    self.assertEqual(self.request(path, method).status, 404)

    def test_refuses_symlinks_to_dotfiles_or_out_of_the_tree(self) -> None:
        for path in (
            "/public-config",
            "/listed/git/config",
            "/outside",
            "/public/",
            "/away/",
        ):
            for method in ("GET", "HEAD"):
                with self.subTest(path=path, method=method):
                    self.assertEqual(self.request(path, method).status, 404)

    def test_serves_symlinks_within_the_tree(self) -> None:
        for path, body in (
            ("/preview/alias.html", b"<p>preview</p>"),
            ("/linked/a.txt", b"a"),
        ):
            with self.subTest(path=path):
                response = self.request(path)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.body, body)


class ArgumentsTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name

    def usage_error(self, *arguments: str) -> str:
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            preview_serve.parse_arguments(list(arguments))
        self.assertEqual(raised.exception.code, 2)
        return stderr.getvalue()

    def test_defaults(self) -> None:
        arguments = preview_serve.parse_arguments(
            ["--root", self.root, "--index", "/p/"]
        )
        self.assertEqual((arguments.address, arguments.port), ("127.0.0.1", 8080))

    def test_usage_errors(self) -> None:
        self.assertIn(
            "is not a directory", self.usage_error("--root", "/no/such", "--index", "/")
        )
        self.assertIn(
            "is not a /-rooted path",
            self.usage_error("--root", self.root, "--index", "p/"),
        )
        self.assertIn(
            "is not a port",
            self.usage_error("--root", self.root, "--index", "/", "--port", "70000"),
        )
        self.assertIn("required", self.usage_error("--index", "/"))

    def test_main_serves_until_interrupted(self) -> None:
        server = mock.Mock(server_address=("0.0.0.0", 1234))
        server.serve_forever.side_effect = KeyboardInterrupt
        stdout = io.StringIO()
        with (
            mock.patch.object(
                preview_serve, "make_server", return_value=server
            ) as made,
            contextlib.redirect_stdout(stdout),
        ):
            status = preview_serve.main(
                ["--root", self.root, "--index", "/p/", "--address", "0.0.0.0"]
            )
        self.assertEqual(status, 0)
        made.assert_called_once_with(
            pathlib.Path(self.root).resolve(), "0.0.0.0", 8080, "/p/"
        )
        server.server_close.assert_called_once_with()
        self.assertIn("http://0.0.0.0:1234/", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
