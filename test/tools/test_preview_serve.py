"""tools/preview-serve, on a fabricated tree served from a thread."""

from __future__ import annotations

import contextlib
import http.client
import io
import pathlib
import re
import socket
import tempfile
import threading
import unittest
from unittest import mock

import temp_tree
import tools_loader

preview_serve = tools_loader.load("preview-serve")


class PreviewServeTest(temp_tree.TempTreeTestCase):
    def setUp(self) -> None:
        super().setUp()
        root = self.root
        self.write("preview/index.html", "<p>preview</p>")
        self.write("preview/app.webmanifest", "{}")
        self.write("preview/logo.svg", "<svg/>")
        self.write("preview/font.ttf", b"\0\1")
        self.write("preview/data.json", "{}")
        self.write("preview/app.js", "")
        self.write("listed/a.txt", "a")
        self.write(".git/config", "secret")
        self.write(".env", "secret")
        outside = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        (outside / "x.txt").write_text("secret", encoding="utf-8")
        (root / "public-config").symlink_to(".env")
        (root / "listed/git").symlink_to("../.git")
        (root / "outside").symlink_to(outside / "x.txt")
        (root / "preview/alias.html").symlink_to("index.html")
        (root / "linked").symlink_to("listed")
        (root / "public").mkdir()
        (root / "public/index.html").symlink_to("../.env")
        (root / "away").mkdir()
        (root / "away/index.htm").symlink_to(outside / "x.txt")
        quiet = mock.patch.object(preview_serve.PreviewHandler, "log_message")
        quiet.start()
        self.addCleanup(quiet.stop)
        server = preview_serve.make_server(root, "127.0.0.1", 0, "/preview/")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
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

    def assertRefused(self, *paths: str) -> None:
        for path in paths:
            for method in ("GET", "HEAD"):
                with self.subTest(path=path, method=method):
                    self.assertEqual(self.request(path, method).status, 404)

    def test_root_redirects_to_the_start(self) -> None:
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

    def test_head_of_a_file_sends_its_headers_without_a_body(self) -> None:
        response = self.request("/preview/logo.svg", "HEAD")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"")
        self.assertEqual(response.getheader("Content-Length"), "6")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(
            response.getheader("Content-Type"), "image/svg+xml; charset=utf-8"
        )

    def test_errors_are_uncached_too(self) -> None:
        response = self.request("/missing")
        self.assertEqual(response.status, 404)
        self.assertEqual(response.getheader("Cache-Control"), "no-store")

    def test_types(self) -> None:
        cases = {
            "/preview/app.webmanifest": "application/manifest+json; charset=utf-8",
            "/preview/logo.svg": "image/svg+xml; charset=utf-8",
            "/preview/data.json": "application/json; charset=utf-8",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(self.request(path).getheader("Content-Type"), expected)
        # text/javascript or application/javascript, as the host's MIME tables have it;
        # either takes the charset.
        script = self.request("/preview/app.js").getheader("Content-Type")
        self.assertTrue(script.endswith("javascript; charset=utf-8"), script)
        binary = self.request("/preview/font.ttf").getheader("Content-Type")
        self.assertNotIn("charset", binary)

    def test_lists_a_directory_without_an_index(self) -> None:
        response = self.request("/listed/")
        self.assertEqual(response.status, 200)
        self.assertIn(b"a.txt", response.body)
        self.assertEqual(response.getheader("Content-Type"), "text/html; charset=utf-8")
        self.assertEqual(response.getheader("Content-Length"), str(len(response.body)))

    def test_listing_leaves_out_what_is_refused(self) -> None:
        root = self.root
        self.write("listed/.env", "secret")
        self.write("listed/.cache/x", "secret")
        self.write("listed/odd name%.txt", "o")
        self.write("listed/a&b/c.txt", "c")
        (root / "listed/secret").symlink_to("../.env")
        (root / "listed/up").symlink_to("../public")
        (root / "listed/away").symlink_to("../away")
        (root / "listed/preview").symlink_to("../preview")
        for path in ("/listed/", "/linked/"):
            with self.subTest(path=path):
                response = self.request(path)
                self.assertEqual(response.status, 200)
                listing = response.body.decode("utf-8")
                self.assertEqual(
                    re.findall(r'<a href="([^"]*)">([^<]*)</a>', listing),
                    [
                        ("a%26b/", "a&amp;b/"),
                        ("a.txt", "a.txt"),
                        ("odd%20name%25.txt", "odd name%.txt"),
                        ("preview/", "preview/"),
                    ],
                )
                self.assertIn(f"<h1>Directory listing for {path}</h1>", listing)

    def test_listing_title_is_the_unquoted_path_escaped(self) -> None:
        self.write("listed/a&b/c.txt", "c")
        response = self.request("/listed/a%26b/")
        self.assertEqual(response.status, 200)
        self.assertIn(
            "<title>Directory listing for /listed/a&amp;b/</title>",
            response.body.decode("utf-8"),
        )

    def test_listing_an_unreadable_directory_is_not_found(self) -> None:
        with mock.patch.object(
            preview_serve.os, "listdir", side_effect=PermissionError
        ):
            response = self.request("/listed/")
        self.assertEqual(response.status, 404)

    def test_refuses_dotfiles_however_spelt(self) -> None:
        self.assertRefused("/.git/config", "/.env", "/%2Egit/config", "/listed/../.env")

    def test_refuses_symlinks_to_dotfiles_or_out_of_the_tree(self) -> None:
        self.assertRefused(
            "/public-config",
            "/listed/git/config",
            "/outside",
            "/public/",
            "/away/",
        )

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

    def run_usage_error(self, *arguments: str) -> str:
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            preview_serve.parse_arguments(list(arguments))
        self.assertEqual(raised.exception.code, 2)
        output = stderr.getvalue()
        self.assertRegex(
            output, r"\AUsage: preview-serve .*\n(.*\n)*preview-serve: error: .+\n\Z"
        )
        return output

    def test_defaults(self) -> None:
        arguments = preview_serve.parse_arguments(
            ["--root", self.root, "--start", "/p/"]
        )
        self.assertEqual((arguments.address, arguments.port), ("127.0.0.1", 8080))

    def test_usage_errors(self) -> None:
        self.assertIn(
            "is not a directory",
            self.run_usage_error("--root", "/no/such", "--start", "/p/"),
        )
        self.assertIn(
            "is not a /-rooted path",
            self.run_usage_error("--root", self.root, "--start", "p/"),
        )
        self.assertIn(
            "is not a port",
            self.run_usage_error(
                "--root", self.root, "--start", "/p/", "--port", "70000"
            ),
        )
        self.assertIn("required", self.run_usage_error("--start", "/p/"))

    def test_start_on_another_host_is_a_usage_error(self) -> None:
        cases = {
            "//example.com/": "names another host",
            "/\\example.com/": "names another host",
            "\\/example.com/": "is not a /-rooted path",
            "///example.com/": "names another host",
        }
        for start, expected in cases.items():
            with self.subTest(start=start):
                self.assertIn(
                    expected,
                    self.run_usage_error("--root", self.root, "--start", start),
                )

    def test_start_outside_printable_ascii_is_a_usage_error(self) -> None:
        for start in (
            "/\t/example.com/",
            "/p/\r\nSet-Cookie: x=1",
            "/p/\n",
            "/p/\x00",
            "/p/\x7f",
            "/café/",
        ):
            with self.subTest(start=start):
                self.assertIn(
                    "percent-encode it",
                    self.run_usage_error("--root", self.root, "--start", start),
                )

    def test_start_on_root_itself_is_a_usage_error(self) -> None:
        for start in (
            "/",
            "/?x",
            "/#a",
            "/.",
            "/./",
            "/..",
            "/../?x",
            "/a/..",
            "/a/../",
            "/p\\..\\",
            "/%2e/",
            "/%2E",
            "/.%2e/#a",
        ):
            with self.subTest(start=start):
                self.assertIn(
                    "which would redirect to itself",
                    self.run_usage_error("--root", self.root, "--start", start),
                )

    def test_start_on_this_host_is_accepted(self) -> None:
        for start in (
            "/p/",
            "/p//q/",
            "/p/?x=1#a",
            "/caf%C3%A9/",
            "/a/../p/",
            "/%2e/p/",
            "/p?x=/../",
        ):
            with self.subTest(start=start):
                arguments = preview_serve.parse_arguments(
                    ["--root", self.root, "--start", start]
                )
                self.assertEqual(arguments.start, start)

    def test_main_serves_until_interrupted(self) -> None:
        server = mock.Mock(server_address=("0.0.0.0", 1234))
        server.serve_forever.side_effect = KeyboardInterrupt
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(
                preview_serve, "make_server", return_value=server
            ) as made,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = preview_serve.main(
                ["--root", self.root, "--start", "/p/", "--address", "0.0.0.0"]
            )
        self.assertEqual(status, 0)
        made.assert_called_once_with(
            pathlib.Path(self.root).resolve(), "0.0.0.0", 8080, "/p/"
        )
        server.server_close.assert_called_once_with()
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            f"preview-serve: {self.root} at http://0.0.0.0:1234/\n",
        )

    def run_main(self, *arguments: str) -> tuple[int, str]:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = preview_serve.main(
                ["--root", self.root, "--start", "/p/", *arguments]
            )
        return status, stderr.getvalue()

    def test_main_fails_on_a_port_in_use(self) -> None:
        held = self.enterContext(socket.socket())
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        status, stderr = self.run_main("--port", str(port))
        self.assertEqual(status, 1)
        self.assertEqual(
            stderr,
            f"preview-serve: cannot listen on 127.0.0.1:{port}:"
            " Address already in use\n",
        )

    def test_main_fails_on_an_address_it_does_not_hold(self) -> None:
        # 192.0.2.1 is reserved for documentation, so no host holds it, and as a
        # numeric address it needs no lookup.
        status, stderr = self.run_main("--address", "192.0.2.1", "--port", "0")
        self.assertEqual(status, 1)
        self.assertRegex(
            stderr, r"\Apreview-serve: cannot listen on 192\.0\.2\.1:0: \S.*\n\Z"
        )
