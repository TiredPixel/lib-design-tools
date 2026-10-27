# lib-design-tools

Tools for working on a design system, or on a design built on one: generating a palette's CSS and JSON, checking that pages load and render, colour maths for a design's own tests, a preview server, and a container image to run them in. A repository takes the parts it wants as a submodule and calls them; nothing here checks, rewrites or tests a repository that has not asked for it, and nothing holds an opinion about how that repository is written.

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

Each command prints its usage with `--help`.

- `tools/palette-generate` – the JSON and the CSS from a palette YAML, one custom property per colour; the check option compares them with the files instead of writing. A property is named `--`, the given prefix and a hyphen, then the full YAML key path, lowercased, with `_` written as `-` and list items numbered from 1, so `greys: {warm: [a, b]}` with prefix `x` gives `--x-greys-warm-1` and `--x-greys-warm-2`; a key that would put anything but letters, digits, hyphens or non-ASCII characters in a name fails the run.
- `tools/references-check` – every local `href`, `src` and `url()` in HTML and CSS resolves under the root. Options refuse anything external a page loads, or require `integrity` and `crossorigin` on every external stylesheet and script.
- `tools/render-check` – each page loaded in headless Chromium, failing on an uncaught error, an unhandled rejection, a `console.error`, a failed load, or an element matching the error marker.
- `tools/lfs-check` – no file matching a `filter=lfs` pattern in the attributes file is an LFS pointer.
- `tools/preview-serve` – the tree as it is on disk, for previewing while editing: nothing cached, dotfiles refused, directories listed, and `/` redirected to the index path. For development only.

Importable from a repository's own Python tests, with `tools/` on the path, as `bin/test` puts it:

- `tools/design_colour.py` – colour maths: WCAG luminance and contrast, compositing a translucent colour over a ground, CIELAB lightness, chroma and hue, and CIE76 ΔE.
- `tools/design_render.py` – the harness under `render-check`: serving a tree, rendering a page or printing it to PDF in headless Chromium, and reading what it reports.
- `tools/pytest_no_skips.py` – a pytest plugin adding `--no-skips`, which fails a run in which any test skipped.

## Using it from another repository

A repository takes this one at `lib/design-tools`, cloned recursively, since the runners use its own `lib/shell-common`:

```sh
git submodule add https://github.com/TiredPixel/lib-design-tools.git lib/design-tools
```

Each part below is taken on its own. A tool can be called from anywhere, the image needs only its link, and only the parts that run commands in the image depend on others: the services run the runners, and the make targets run the services.

### The image

`dev` is the toolchain the tools need, headless Chromium included; `test` adds a copy of the repository's tree (*Building the images*). A repository builds it through a `Dockerfile` that links to this one:

```sh
ln -s lib/design-tools/Dockerfile Dockerfile
```

### The runners

`bin/format`, `bin/lint`, `bin/test` and `bin/preview` run in the image, from the repository's root, and do only what the repository's own `bin/settings` names (*Settings*). A repository links the ones it uses; each finds this repository by resolving its link:

```sh
mkdir -p bin
for runner in format lint test preview; do ln -s "../lib/design-tools/bin/$runner" "bin/$runner"; done
```

### The services

This repository's `docker-compose.yml` holds the `format`, `test` and `preview` services, which run the runners in the image. A repository links it, and Compose resolves every path in it, and reads `.env`, against the repository holding the link:

```sh
ln -s lib/design-tools/docker-compose.yml docker-compose.yml
```

A repository whose YAML pass would cover `docker-compose.yml` leaves the link out of `yaml_files`, since `bin/format` would rewrite this repository's file through it.

The test stage copies the whole tree, so the repository's `.dockerignore` keeps `.git`, `.env` and anything local out of it, and must not exclude the submodule.

### The make targets

`common.mk` gives `build`, `format`, `lint`, `test` and `upgrade`, run through the services. A repository's `Makefile` includes it and adds its own `upgrade-pre`, which moves the submodule to its latest commit and its own submodules to the commits that one records:

```make
include lib/design-tools/common.mk

upgrade-pre:
	cd lib/design-tools && git checkout latest && git pull && git submodule update --init --recursive
```

`make upgrade` runs `upgrade-pre`, then builds, formats and tests, so a change that breaks the repository shows before the bump is committed.

### Settings

`bin/settings` is the repository's own, sourced by every runner. Each pass runs only where it defines what the pass reads, so a repository with an empty `bin/settings` gets no pass at all. Each is a function printing NUL-separated paths or arguments; a defined one that fails, or that names no files, fails the run.

What `bin/format` rewrites and `bin/lint` checks for drift, each to the tool's own style:

- `palette_args` – `tools/palette-generate`'s arguments.
- `python_files` – ruff, under the repository's own ruff configuration.
- `yaml_files` – keys sorted with yq.
- `json_files` – indented by 2 with keys sorted, by Python's `json.tool`.
- `js_files` – semistandard.
- `css_files` and `html_files` – Prettier, under the repository's own Prettier configuration.

What `bin/lint` checks only:

- `shell_files` – ShellCheck.
- `lfs_args` – `tools/lfs-check`'s arguments.
- `references_args` – `tools/references-check`'s arguments.
- `render_args` – `tools/render-check`'s arguments, such as `--root . pages/*.html`.

What `bin/test` runs, after the lint unless tests are named:

- `pytest_args` – pytest's options, which may be none, for the repository's own tests under its own pytest configuration; `--no-skips` fails a run in which a test skipped.

And `bin/preview`:

- `preview_index`, a variable – the path `/` redirects to.

A `bin/settings` for a design system with a preview page and a palette:

```bash
# shellcheck shell=bash

# shellcheck disable=SC2034
preview_index=/preview/

function palette_args {
    printf '%s\0' src/palette.yml --prefix x --json src/palette.json --css src/palette.css
}

function css_files {
    printf '%s\0' preview/*.css src/*.css
}

function render_args {
    printf '%s\0' --root . preview/index.html
}

function pytest_args {
    printf '%s\0' --no-skips
}
```

### Previewing

`docker compose up` serves the tree read-only with `bin/preview`, on a free host port, or on `DC_PREVIEW_PORT` where `.env` sets one:

```sh
docker compose up
```

With no port pinned, read it back:

```sh
docker compose port preview 8080
```

## Testing

`make test` runs this repository's lint, then its tests, in the `test` container (*Building the images*). The lint fails where anything is unformatted, so format first:

```sh
make format test
```

The tests run under pytest, side by side on one worker for each CPU the container may use; `PYTEST_XDIST_AUTO_NUM_WORKERS` in `.env` runs fewer. A test that skips fails the run: the image carries everything the tests look for, so a skip means something is missing.

Named tests skip the lint and run alone; they are pytest's arguments:

```sh
make test TESTS='test/test_runners.py'
```

`make lint` runs the lint alone:

```sh
make lint
```

## Formatting

`make format` rewrites the tree in place with the passes `bin/format` runs.

```sh
make format
```

## Building the images

The image builds two stages. `dev` is the formatting, linting and test toolchain, headless Chromium included, running as the unprivileged user `x`; it copies nothing in, so a repository builds it through its symlinked `Dockerfile` and runs it on its own tree. `test` adds a copy of the repository's tree to it.

The `format` service runs `bin/format` from `dev` on the tree mounted read-write, since its rewrites must reach it, and the `preview` service `bin/preview` on the tree mounted read-only. The `test` service runs the gates on its own copy, with no mount and no network.

Base images are pinned by digest, and tools from outside Debian's repositories by `ARG`; in this repository, `make upgrade` re-pins each pin the `Dockerfile` names on a `FROMFREEZE` or `ARGFREEZE` line.
