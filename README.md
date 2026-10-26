# lib-design-tools

The tooling a design system and the projects built on it share: programs that generate and check palettes, stylesheets, pages and the prose about them, the container image they run in, and the Compose services, make targets and `bin/` runners that wire them into a repository's gate. None of it knows any one design system: a repository says which of its files each pass reads, and nothing else.

A design system takes this repository as a submodule, and so does each design project built on it, whose product is the design itself, such as a set of wireframes, beside its submodule of the design system. A design project links the design system's files where they sit in that submodule, so the preview serves them as they are. An application taking the design system into its own asset pipeline needs none of this repository.

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

Each prints its usage with `--help`.

- `tools/palette-generate` – the JSON and the CSS from a palette YAML, one custom property per colour; the check option compares them with the files instead of writing. A property is named `--`, the given prefix and a hyphen, then the full YAML key path, lowercased, with `_` written as `-` and list items numbered from 1, so `greys: {warm: [a, b]}` with prefix `x` gives `--x-greys-warm-1` and `--x-greys-warm-2`; a key that would put anything but letters, digits, hyphens or non-ASCII characters in a name fails the run.
- `tools/html-embedded` – each inline `<script>` and `<style>` body through the given formatter command, spliced back, or in `check` mode compared.
- `tools/references-check` – every local `href`, `src` and `url()` in HTML and CSS resolves under the root, and every external stylesheet and script carries `integrity` and `crossorigin`; the no-external option refuses anything external the page loads.
- `tools/render-check` – each page loaded in headless Chromium, failing on an uncaught error, an unhandled rejection, a `console.error`, a failed load, or an element matching the error marker. Its harness, `tools/design_render.py`, is importable from a repository's tests, since `bin/test` puts `tools/` on the path.
- `tools/preview-serve` – the tree as it is on disk, for previewing while editing: nothing cached, dotfiles refused, directories listed, and `/` redirected to the index path. For development only.
- `tools/markdown-lint` – one paragraph per line, local links and anchors resolve, backticked paths exist, a backticked custom property names one the given stylesheets define, and prose has no `--` or `—`.
- `tools/comment-check` – comments, docstrings and usage text in Python, CSS, JavaScript, YAML and shell use typewriter punctuation, while letters keep their diacritics and scripts.
- `tools/lfs-check` – no file matching a `filter=lfs` pattern in the attributes file is an LFS pointer.
- `tools/charset-check` – every stylesheet opens with `@charset "UTF-8";`.
- `tools/pytest_no_skips.py` – a pytest plugin adding `--no-skips`, which fails a run in which any test skipped; `bin/test` loads it.

## Setting up a repository

A design system takes this repository at `lib/design-tools`:

```sh
git submodule add https://github.com/TiredPixel/lib-design-tools.git lib/design-tools
```

A design project adds it the same way, so everything below holds for both. The submodule is cloned recursively, since the runners use this repository's own `lib/shell-common`.

The repository then links the image and the runners, and holds its own few files. `example/` is a minimal design system with every one of them, which `make example` boots as a repository taking this one would be (*Booting the example*), so copy its files and adapt them.

`Dockerfile`, a symlink:

```sh
ln -s lib/design-tools/Dockerfile Dockerfile
```

`bin/format`, `bin/lint`, `bin/test` and `bin/preview`, symlinks; each finds this repository by resolving its link and runs from the repository's root:

```sh
mkdir -p bin
for runner in format lint test preview; do ln -s "../lib/design-tools/bin/$runner" "bin/$runner"; done
```

The repository's own files, as `example/` has them:

- [`example/docker-compose.yml`](example/docker-compose.yml) includes the services, with `project_directory` resolving every path in them against the repository; a repository with no pages to preview leaves out `compose.preview.yml`.
- [`example/Makefile`](example/Makefile) includes `design.mk`, and its `upgrade-pre` moves the submodule to its latest commit and its own submodules to the commits that one records; a design project moves its design system there too.
- [`example/bin/format-paths`](example/bin/format-paths), sourced by every runner, names what each pass reads (*Configuring the passes*) and the preview's index.
- [`example/.dockerignore`](example/.dockerignore) keeps `.git`, `.env` and anything local out of the copy the `test` stage takes, and must not exclude the submodule.
- [`example/.env.example`](example/.env.example) holds the settings `.env` may take.
- [`example/test/test_preview.py`](example/test/test_preview.py) is a test in `test/`, a `test_*.py` unittest suite, which `bin/test` runs under this repository's `pytest.ini`; importing `design_render`, it renders the repository's page in headless Chromium.

### Configuring the passes

Every pass in `bin/format` and `bin/lint` runs where `bin/format-paths` defines what it reads, and is skipped where it does not, so a repository turns on only what it has. Each is a function printing NUL-separated paths or arguments; a defined one that fails or prints nothing fails the run.

- `palette_args` – `tools/palette-generate`'s arguments; format writes the files, lint checks them.
- `python_files` – formatted and checked with ruff.
- `yaml_files` – keys sorted with yq.
- `json_files` – indented by 2 with keys sorted, by Python's `json.tool`.
- `js_files` – formatted and checked with semistandard.
- `css_files` and `html_files` – formatted and checked with Prettier.
- `shell_files` – ShellCheck, beyond the scripts it finds at the top level and in `bin/`, and `bin/format-paths`, which is always checked.
- `charset_files` – `tools/charset-check`.
- `lfs_args` – `tools/lfs-check`'s arguments.
- `references_args` – `tools/references-check`'s arguments.
- `markdown_args` – `tools/markdown-lint`'s arguments.
- `comment_files` – `tools/comment-check`, with the options `comment_args` prints where it is defined, such as `--forbid` for a character the repository's own conventions rule out.

`preview_index`, a variable, is the path `/` redirects to in the preview. Lint also checks that `.env.example`, where there is one, is sorted.

### Previewing

`docker compose up` serves the tree read-only with `bin/preview` on a free host port, or on `DC_PREVIEW_PORT` where `.env` sets one:

```sh
docker compose up
```

With no port pinned, read it back:

```sh
docker compose port preview 8080
```

### Taking a change

In the repository taking the submodule:

```sh
make upgrade
```

`make upgrade` runs the repository's `upgrade-pre`, then builds, formats and tests, so a change that breaks the repository shows before the bump is committed.

### Migrating from Claude Design

A project exported from Claude Design is brought onto a design system and these tools as [`docs/migrating-from-claude-design.md`](docs/migrating-from-claude-design.md) describes.

## Testing

`make test` runs the lint, then the tests, in the `test` container (*Building the images*). The lint fails where anything is unformatted, so format first:

```sh
make format test
```

The tests run under pytest, side by side on one worker for each CPU the container may use; `PYTEST_XDIST_AUTO_NUM_WORKERS` in `.env` runs fewer. A test that skips fails the run: the image carries everything the tests look for, so a skip means something is missing, never something to pass over.

Named tests skip the lint and run alone; they are pytest's arguments:

```sh
make test TESTS='test/test_runners.py'
```

`make lint` runs the lint alone:

```sh
make lint
```

### Booting the example

`example/` holds a minimal design system's own files: a palette, a preview page that renders it, a render test, and its `bin/format-paths`, `Makefile`, `docker-compose.yml`, `.dockerignore` and `.env.example`. `make example` runs on the host and assembles it under `tmp/` with a copy of this tree as its `lib/design-tools`, makes the links *Setting up a repository* gives, runs its `make build` and `make test`, and fetches its page from the preview, removing everything it assembled afterwards:

```sh
make example
```

At this repository's root, `docker compose up` serves the tree as *Previewing* describes, with `/` redirecting to the example's page, so an edit to `example/` shows on reload:

```sh
docker compose up
```

## Formatting

`make format` rewrites the tree in place with the passes `bin/format` runs. Markdown is linted and never rewritten, since a formatter would reflow it.

```sh
make format
```

## Building the images

The image builds two stages. `dev` is the formatting, linting and test toolchain, headless Chromium included, running as the unprivileged user `x`; it copies nothing in, so a repository builds it through its symlinked `Dockerfile` and runs it on its own tree. `test` adds a copy of the repository's tree to it.

The `format` service runs `bin/format` from `dev` on the tree mounted read-write, since its rewrites must reach it, and the `preview` service `bin/preview` on the tree mounted read-only. The `test` service runs the gates on its own copy, with no mount and no network.

Base images are pinned by digest, and tools from outside Debian's repositories by `ARG`; in this repository, `make upgrade` re-pins each pin the `Dockerfile` names on a `FROMFREEZE` or `ARGFREEZE` line.
