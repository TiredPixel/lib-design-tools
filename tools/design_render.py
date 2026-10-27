"""Render static pages in headless Chromium and collect what went wrong.

A page is served from a local root over HTTP, so that root-relative references, module
scripts and ``fetch`` behave as they do when deployed, rather than under ``file://``.
Every page requested by an ``.html`` or ``.htm`` path gets a capture script injected as
the first child of ``<head>``, ahead of anything the page loads, which records failed
resource loads, uncaught errors, unhandled rejections and ``console.error`` calls into a
``data-render-errors`` attribute on ``<html>``. Chromium's ``--dump-dom`` then hands
back the DOM after scripts have run, attribute included.

Everything except the Chromium call itself is plain text processing, so it can be tested
without a browser.
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import html.parser
import http.server
import json
import pathlib
import re
import subprocess
import tempfile
import threading
import urllib.parse
from collections.abc import Iterator

ERRORS_ATTRIBUTE = "data-render-errors"

DEFAULT_CHROMIUM = "chromium"
DEFAULT_BUDGET_MS = 5000
DEFAULT_TIMEOUT_S = 60

# Installed before anything else on the page runs. The `error` listener is in the
# capture phase because a failed <link>, <script> or <img> load fires a non-bubbling
# `error` event at the element, which only a capturing listener on window sees. The
# attribute is written as `[]` up front so that its absence from the dumped DOM proves
# the script never ran.
CAPTURE_SCRIPT = """<script>(function () {
  var root = document.documentElement;
  var errors = [];
  function record(entry) {
    errors.push(entry);
    root.setAttribute('ERRORS_ATTRIBUTE', JSON.stringify(errors));
  }
  function text(value) {
    if (value instanceof Error) { return value.stack || String(value); }
    if (typeof value === 'string') { return value; }
    try { return JSON.stringify(value); } catch (e) { return String(value); }
  }
  root.setAttribute('ERRORS_ATTRIBUTE', '[]');
  window.addEventListener('error', function (event) {
    var target = event.target;
    if (target && target !== window && target.tagName) {
      record({kind: 'resource', tag: target.tagName.toLowerCase(),
              url: target.currentSrc || target.src || target.href || ''});
    } else {
      record({kind: 'error', message: text(event.error || event.message),
              source: event.filename || '', line: event.lineno || 0});
    }
  }, true);
  window.addEventListener('unhandledrejection', function (event) {
    record({kind: 'unhandledrejection', message: text(event.reason)});
  });
  var consoleError = console.error;
  console.error = function () {
    record({kind: 'console.error',
            message: Array.prototype.map.call(arguments, text).join(' ')});
    return consoleError.apply(console, arguments);
  };
})();</script>""".replace("ERRORS_ATTRIBUTE", ERRORS_ATTRIBUTE)

_CLASS_SELECTOR = re.compile(r"\.(-?[A-Za-z_][A-Za-z0-9_-]*)")

HTML_SUFFIXES = frozenset({".html", ".htm"})


class RenderError(Exception):
    """A page could not be rendered, or its render could not be read."""


@dataclasses.dataclass(frozen=True)
class Render:
    """The DOM Chromium dumped for a page, and the errors captured in it."""

    url: str
    dom: str
    errors: list[dict]


def _offset(starts: list[int], position: tuple[int, int]) -> int:
    line, column = position
    return starts[line - 1] + column


def _line_starts(text: str) -> list[int]:
    """Return the offset of the first character of each line of ``text``."""
    return [0] + [index + 1 for index, char in enumerate(text) if char == "\n"]


class _InsertionPoint(html.parser.HTMLParser):
    """Find where the first child of ``<head>`` goes, by real markup only.

    A parser rather than a pattern, so that ``<header>``, or ``<head>`` inside a comment
    or a script, is never mistaken for the element.
    """

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self._starts = _line_starts(text)
        self.after_head: int | None = None
        self.after_html: int | None = None
        self.after_doctype: int | None = None

    def _end_of_tag(self) -> int:
        return _offset(self._starts, self.getpos()) + len(self.get_starttag_text())

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "head" and self.after_head is None:
            self.after_head = self._end_of_tag()
        elif tag == "html" and self.after_html is None:
            self.after_html = self._end_of_tag()

    def handle_decl(self, decl: str) -> None:
        if self.after_doctype is None and decl.lower().startswith("doctype"):
            self.after_doctype = _offset(self._starts, self.getpos()) + len(decl) + 3


def inject_capture(text: str) -> str:
    """Return ``text`` with the capture script as the first child of ``<head>``.

    Where the page omits ``<head>``, the script goes where the parser would open the
    implied head: after ``<html>``, else after the doctype, else first.
    """
    finder = _InsertionPoint(text)
    finder.feed(text)
    finder.close()
    for point in (finder.after_head, finder.after_html, finder.after_doctype):
        if point is not None:
            return text[:point] + CAPTURE_SCRIPT + text[point:]
    return CAPTURE_SCRIPT + text


class _HtmlAttributes(html.parser.HTMLParser):
    """Collect the attributes of the first ``<html>`` element."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.attributes: dict[str, str | None] | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "html" and self.attributes is None:
            self.attributes = dict(attrs)


def parse_render_errors(dom: str) -> list[dict]:
    """Return the errors the capture script recorded in a dumped DOM.

    Raises RenderError when the attribute is missing, since that means the capture
    script never ran and the page's silence proves nothing.
    """
    parser = _HtmlAttributes()
    parser.feed(dom)
    parser.close()
    value = (parser.attributes or {}).get(ERRORS_ATTRIBUTE)
    if value is None:
        raise RenderError(
            f"no {ERRORS_ATTRIBUTE} attribute on <html>: the capture script "
            "did not run, so the page was not checked"
        )
    try:
        errors = json.loads(value)
    except json.JSONDecodeError as error:
        raise RenderError(f"{ERRORS_ATTRIBUTE} is not valid JSON: {error}") from error
    if not isinstance(errors, list):
        raise RenderError(f"{ERRORS_ATTRIBUTE} is not a JSON array: {value}")
    return errors


def describe_error(entry: object) -> str:
    """Return a one-line description of a captured error entry."""
    kind = entry.get("kind") if isinstance(entry, dict) else None
    if kind == "resource":
        return f"failed to load <{entry.get('tag')}> {entry.get('url')}"
    if kind == "error":
        where = entry.get("source") or ""
        if where:
            where = f" (at {where}:{entry.get('line')})"
        return f"uncaught error: {entry.get('message')}{where}"
    if kind in ("unhandledrejection", "console.error"):
        return f"{kind}: {entry.get('message')}"
    return f"unrecognised entry: {json.dumps(entry)}"


def parse_class_selector(selector: str) -> str:
    """Return the class name in a simple ``.name`` selector.

    Raises ValueError for anything richer, rather than matching it wrongly.
    """
    match = _CLASS_SELECTOR.fullmatch(selector)
    if match is None:
        raise ValueError(
            f"unsupported selector {selector!r}: only a single class, "
            "such as '.load-error', is supported"
        )
    return match.group(1)


class _ClassFinder(html.parser.HTMLParser):
    def __init__(self, class_name: str) -> None:
        super().__init__(convert_charrefs=True)
        self._class_name = class_name
        self.matches: list[tuple[str, int]] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        for name, value in attrs:
            if name == "class" and value and self._class_name in value.split():
                self.matches.append((tag, self.getpos()[0]))
                return


def find_marked(dom: str, class_name: str) -> list[tuple[str, int]]:
    """Return the tag name and DOM line of each element with ``class_name``."""
    finder = _ClassFinder(class_name)
    finder.feed(dom)
    finder.close()
    return finder.matches


class _InjectingHandler(http.server.SimpleHTTPRequestHandler):
    """Serve a directory, injecting the capture script into HTML pages.

    Pages are served as UTF-8. Bytes that are not valid UTF-8 pass through unchanged
    rather than failing the request.
    """

    def do_GET(self) -> None:
        path = pathlib.Path(self.translate_path(self.path))
        if path.suffix.lower() not in HTML_SUFFIXES or not path.is_file():
            super().do_GET()
            return
        text = path.read_bytes().decode("utf-8", errors="surrogateescape")
        body = inject_capture(text).encode("utf-8", errors="surrogateescape")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Keep request logs off stderr, where findings go."""


@contextlib.contextmanager
def serve(root: pathlib.Path) -> Iterator[str]:
    """Serve ``root`` on an ephemeral loopback port; yield its base URL."""
    handler = functools.partial(_InjectingHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def page_url(base_url: str, root: pathlib.Path, page: pathlib.Path) -> str:
    """Return the URL ``page`` has when ``root`` is served at ``base_url``."""
    resolved_root = root.resolve()
    resolved_page = page.resolve()
    if not resolved_page.is_relative_to(resolved_root):
        raise ValueError(f"{page} is not inside the served root {root}")
    relative = resolved_page.relative_to(resolved_root).as_posix()
    return base_url + urllib.parse.quote(relative)


def _chromium(
    url: str,
    action: list[str],
    *,
    profile: str,
    chromium: str,
    budget_ms: int,
    timeout_s: float,
) -> str:
    """Run headless Chromium on ``url`` with ``action``; return its stdout."""
    command = [
        chromium,
        "--headless",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        f"--user-data-dir={profile}",
        f"--virtual-time-budget={budget_ms}",
        *action,
        url,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError as error:
        raise RenderError(f"cannot run {chromium!r}: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise RenderError(
            f"{chromium} did not finish {url} within {timeout_s}s"
        ) from error
    if result.returncode != 0:
        detail = result.stderr.strip() or "no output on stderr"
        raise RenderError(f"{chromium} exited {result.returncode} on {url}: {detail}")
    return result.stdout


def dump_dom(
    url: str,
    *,
    chromium: str = DEFAULT_CHROMIUM,
    budget_ms: int = DEFAULT_BUDGET_MS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> str:
    """Load ``url`` in headless Chromium and return the DOM it settles on."""
    with tempfile.TemporaryDirectory(prefix="design-render-") as profile:
        return _chromium(
            url,
            ["--dump-dom"],
            profile=profile,
            chromium=chromium,
            budget_ms=budget_ms,
            timeout_s=timeout_s,
        )


def print_pdf(
    url: str,
    *,
    chromium: str = DEFAULT_CHROMIUM,
    budget_ms: int = DEFAULT_BUDGET_MS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> bytes:
    """Print ``url`` as headless Chromium's print does, without its header and footer,
    and return the PDF."""
    with tempfile.TemporaryDirectory(prefix="design-render-") as profile:
        out = pathlib.Path(profile) / "print.pdf"
        _chromium(
            url,
            ["--no-pdf-header-footer", f"--print-to-pdf={out}"],
            profile=profile,
            chromium=chromium,
            budget_ms=budget_ms,
            timeout_s=timeout_s,
        )
        if not out.is_file():
            raise RenderError(f"{chromium} printed no PDF for {url}")
        return out.read_bytes()


_PDF_PAGE = re.compile(rb"/Type\s*/Page(?![s\w])")


def sheet_count(pdf: bytes) -> int:
    """The number of pages in a PDF Chromium printed, read from its page objects;
    Chromium writes them uncompressed."""
    return len(_PDF_PAGE.findall(pdf))


def render_url(url: str, **chromium_options: object) -> Render:
    """Render ``url`` and return its DOM with the captured errors."""
    dom = dump_dom(url, **chromium_options)
    return Render(url=url, dom=dom, errors=parse_render_errors(dom))


def render(
    root: pathlib.Path, page: pathlib.Path, **chromium_options: object
) -> Render:
    """Serve ``root``, render ``page`` from it, and return the result.

    For tests that assert on a single page's DOM; checking many pages is cheaper with
    one ``serve`` around repeated ``render_url`` calls.
    """
    with serve(root) as base_url:
        return render_url(page_url(base_url, root, page), **chromium_options)
