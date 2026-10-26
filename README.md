# lib-design-tools

Programs that generate and check a design system's files: palettes, stylesheets, pages and the prose about them. None of them knows any one design system: each takes its paths and options as arguments, reads nothing else, and prints its usage with `--help`.

## Prerequisites

- Docker Engine or Docker Desktop with Compose v2
- `git` with submodule support
- `make`

## Installation

The repository uses git submodules. Clone it with them, then build the images:

```sh
git clone --recurse-submodules https://github.com/TiredPixel/lib-design-tools.git
cd lib-design-tools
cp .env.example .env
make build
```

## The tools

- `tools/palette-generate` – the JSON and the CSS from a palette YAML, one custom property per colour; the check option compares them with the files instead of writing. A property is named `--`, the given prefix and a hyphen, then the full YAML key path, lowercased, with `_` written as `-` and list items numbered from 1, so `greys: {warm: [a, b]}` with prefix `x` gives `--x-greys-warm-1` and `--x-greys-warm-2`.
- `tools/html-embedded` – each inline `<script>` and `<style>` body through the given formatter command, spliced back, or in `check` mode compared.
- `tools/references-check` – every local `href`, `src` and `url()` in HTML and CSS resolves under the root, and every external stylesheet and script carries `integrity` and `crossorigin`; the no-external option refuses anything external the page loads.
- `tools/render-check` – each page loaded in headless Chromium, failing on an uncaught error, an unhandled rejection, a `console.error`, a failed load, or an element matching the error marker. Its harness, `tools/design_render.py`, is importable from tests with `PYTHONPATH=lib/design-tools/tools`.
- `tools/preview-serve` – the tree as it is on disk, for previewing while editing: nothing cached, dotfiles refused, directories listed, and `/` redirected to the index path. For development only.
- `tools/markdown-lint` – one paragraph per line, local links and anchors resolve, backticked paths exist, a backticked custom property names one the given stylesheets define, and prose has no `--` or `—`.
- `tools/comment-check` – comments, docstrings and usage text in Python, CSS, JavaScript, YAML and shell use typewriter punctuation, while letters keep their diacritics and scripts.
- `tools/lfs-check` – no file matching a `filter=lfs` pattern in the attributes file is an LFS pointer.
- `tools/charset-check` – every stylesheet opens with `@charset "UTF-8";`.

## Using the tools from another repository

A consumer takes this repository as a git submodule at `lib/design-tools` and runs the tools from it; nothing is copied.

In the consumer's repository:

```sh
git submodule add https://github.com/TiredPixel/lib-design-tools.git lib/design-tools
```

The consumer's own `bin/` scripts run the tools in the `dev` image built from `lib/design-tools/Dockerfile`, with the consumer's tree at `/home/x/r`: copied in for its gates, and mounted only for a pass that must write back, as this repository's `test` and `format` services have it.

`tools/pytest_no_skips.py` is a pytest plugin adding `--no-skips`, which fails a run in which any test skipped. With `lib/design-tools/tools` on pytest's `pythonpath`, a consumer's `conftest.py` loads it:

```python
pytest_plugins = ("pytest_no_skips",)
```

### Taking a change

In the consumer's repository:

```sh
git submodule update --remote lib/design-tools
```

Then run the consumer's own checks before committing the bump.

## Testing

`make test` runs the lint, then the tests, in the `test` container (*Building the images*). The lint fails where anything is unformatted, so format first:

```sh
make format test
```

The tests run under pytest, side by side on one worker for each CPU the container may use; `PYTEST_XDIST_AUTO_NUM_WORKERS` in `.env` runs fewer. A test that skips fails the run: the image carries everything the tests look for, so a skip means something is missing, never something to pass over.

Named tests skip the lint and run alone; they are pytest's arguments:

```sh
make test TESTS='test/test_tools_render.py'
```

`make lint` runs the lint alone:

```sh
make lint
```

## Formatting

`make format` rewrites the tree in place with the passes `bin/format` runs. Markdown is linted and never rewritten, since a formatter would reflow it.

```sh
make format
```

## Building the images

The image builds two stages. `dev` is the formatting, linting and test toolchain, headless Chromium included, running as the unprivileged user `x`; it copies nothing in, so a consumer builds it from the submodule. `test` adds a copy of this tree to it.

The `format` service runs `bin/format` from `dev` on the tree mounted read-write, since its rewrites must reach it. The `test` service runs the gates on its own copy, with no mount and no network.

Base images are pinned by digest, and tools from outside Debian's repositories by `ARG`; `make upgrade` re-pins each pin the `Dockerfile` names on a `FROMFREEZE` or `ARGFREEZE` line.
