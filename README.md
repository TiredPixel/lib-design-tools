# lib-design-tools

Tools for working on a design system, or on a design built on one: generating a palette's CSS and JSON, checking that pages load and render, colour maths for a design's own tests, a preview server, and a container image to run them in. A repository takes the parts it wants as a submodule and calls them; nothing here checks, rewrites or tests a repository that has not asked for it, and nothing holds an opinion about how that repository is written.

## Requirements

Working on this repository needs:

- Docker Engine or Docker Desktop with Compose v2
- `git` with submodule support
- `make`
- `bash` 4.4 or later

## Installation

Clone the repository with its submodules, and copy `.env.example` to `.env`, which sets local options for Compose:

```sh
git clone --recurse-submodules https://github.com/TiredPixel/lib-design-tools.git
cd lib-design-tools
cp .env.example .env
```

Set `DC_BIND_USER=0:0` in `.env` where Docker is rootless or the host user's uid is not 1000, so that `make format` can write to the tree.

## Tools

Each command prints its usage with `--help`.

- `tools/palette-generate` – the JSON and the CSS from a palette YAML, one custom property per colour; `--check` compares them with the files instead of writing. A property is named `--`, the `--prefix` given and a hyphen, then the full YAML key path, lowercased, with `_` written as `-` and list items numbered from 1, so `greys: {warm: [a, b]}` with prefix `x` gives `--x-greys-warm-1` and `--x-greys-warm-2`; a key that would put anything but letters, digits, hyphens or non-ASCII characters in a name fails the run, as does a value starting with `#` that is not a `#RGB`, `#RGBA`, `#RRGGBB` or `#RRGGBBAA` colour, or a value left empty, as an unquoted `#` colour is, since YAML reads it as a comment, as do two leaves that come out with the same name and a YAML that holds no colour at all. Aliases and merge keys are expanded in both outputs alike.
- `tools/references-check` – every local reference in HTML and CSS resolves under `--root`: each attribute that loads or links something, such as `href`, `src`, `srcset`, `imagesrcset`, `poster` and `data`, a refresh's target, and each `url()`, `@import` and `image-set()` string. A reference that leaves `--root` fails, as does one to a directory with no `index.html` or `index.htm`, and so does a file that holds a `<base>` element, which changes how every reference resolves, or that is not valid UTF-8. `--no-external` refuses anything external a page loads, and `--require-integrity` requires `integrity` and `crossorigin` on every external stylesheet and script. `--unresolved PATH`, repeatable, exempts a `/`-rooted path served by something else, such as a sibling site, for a link, an `area`, a refresh or a `link` whose every `rel` loads nothing, matching wherever a reference lands, query and fragment aside; an exempt path that something loads into the page fails, as does an exemption whose path resolves or that nothing references.
- `tools/render-check` – each page loaded in headless Chromium, failing on an uncaught error, an unhandled rejection, a `console.error`, a failed load the page or its stylesheets start, such as an image, script, stylesheet, `@import`, font or background image, or an element matching `--error-marker`. A request the page's own script makes, such as `fetch`, is left to the script; the page's icon is not checked, and a load from another origin that does not use CORS counts only where its element reports an error.
- `tools/preview-serve` – the tree as it is on disk, for previewing while editing: nothing cached, dot paths and paths outside the tree refused, directories listed, and `/` redirected to `--start`, the page the preview opens on. For development only.

Importable from a repository's own Python tests, with `tools/` on the path, as `bin/test` puts it:

- `tools/design_colour.py` – colour maths: WCAG luminance and contrast, compositing a translucent colour over a ground, CIELAB lightness, chroma and hue, the gap and spread between hues and whether one lies on the arc between two others, and CIE76 ΔE.
- `tools/design_render.py` – the harness under `render-check`: serving a tree, rendering a page or printing it to PDF in headless Chromium, and reading what it reports.
- `tools/pytest_no_skips.py` – a pytest plugin, which `bin/test` loads, adding `--no-skips`, which fails a run in which any test skipped.

## Usage

A repository takes this one at `lib/design-tools`, cloned recursively, since the runners use its own `lib/shell-common`:

```sh
git submodule add https://github.com/TiredPixel/lib-design-tools.git lib/design-tools
git submodule update --init --recursive lib/design-tools
```

Each part below is taken on its own. A tool needs nothing here outside `tools/` and can be called directly in the image; the image needs only its link; and only the parts that run commands in the image depend on others: the services run the runners, and the make targets run the services.

### The image

The image builds three stages. `wheels` downloads the Python tools' locked wheels. `dev` is the formatting, linting and test toolchain, headless Chromium included, running as the unprivileged user `x`; it installs the Python and npm tools from the locks in `Dockerfile.lock/` through bind mounts and copies nothing in, so a repository builds it through its symlinked `Dockerfile` and runs it on its own tree. `test` adds a copy of the repository's tree to it.

A repository builds it through a `Dockerfile` and a `Dockerfile.lock` that link to this one's, since the build reads the locks from the repository's own context:

```sh
ln -s lib/design-tools/Dockerfile Dockerfile
ln -s lib/design-tools/Dockerfile.lock Dockerfile.lock
```

A repository leaves the link out of `docker_files`, since `bin/format` would rewrite this repository's `Dockerfile` through it.

### The runners

`bin/format`, `bin/lint`, `bin/test` and `bin/preview` run in the image, from the repository's root, and do only what the repository's own `bin/settings` names (*Settings*). A repository links the ones it uses; each finds this repository by resolving its link:

```sh
mkdir -p bin
for runner in format lint test preview; do ln -s "../lib/design-tools/bin/$runner" "bin/$runner"; done
```

### The services

This repository's `docker-compose.yml` holds the `design`, `design_test` and `preview` services, which run the runners in the image. A repository links it, and Compose resolves every path in it, and reads `.env`, against the repository holding the link:

```sh
ln -s lib/design-tools/docker-compose.yml docker-compose.yml
```

The `design` and `design_test` services sit under the `test` profile, which `make build`, `make format`, `make lint` and `make test` pass, and the `preview` service under the `preview` profile, which `make preview` selects, so a bare `docker compose up` starts no service. The `design` service runs `bin/format` from `dev` on the tree mounted read-write, since its rewrites must reach it, and the `preview` service `bin/preview` on the tree mounted read-only. The `design_test` service runs the gates from `test` on its own copy, with no mount and no network.

A repository whose YAML pass would cover `docker-compose.yml` leaves the link out of `yaml_files`, since `bin/format` would rewrite this repository's file through it.

The test stage copies the whole tree, so the repository's `.dockerignore` keeps `.git`, `.env` and anything local out of it, and must not exclude the submodule. Excluding every top-level dot path, `/.*`, and letting the repository's own dot files back in by name, such as `!/.github`, keeps a per-user directory such as `.claude` out without naming it.

### The make targets

`common.mk` gives `build`, `format`, `lint`, `preview`, `test`, `upgrade` and `upgrade-post`, run through the services. A repository's `Makefile` includes it and adds its own `upgrade-pre`, which moves the submodule to its latest commit and its own submodules to the commits that one records. Its first two lines make the submodule a repository of its own before anything runs in it, since git run in an uninitialised submodule's empty directory acts on the parent instead:

```make
include lib/design-tools/common.mk

upgrade-pre:
	git submodule sync lib/design-tools
	git submodule update --init --checkout lib/design-tools
	cd lib/design-tools && git checkout latest && git pull --ff-only && git submodule update --init --recursive
```

`make upgrade` runs `upgrade-pre`, then `upgrade-post`, which builds and tests, so a change that breaks the repository shows before the bump is committed, and then runs the command in `UPGRADE_COMMIT` where the `Makefile` sets one.

`make test` runs `bin/test`, which *Settings* describes, in the `design_test` service; named tests are pytest's arguments, paths from the repository's root:

```sh
make test TESTS='test/<file>.py'
```

`PYTEST_XDIST_AUTO_NUM_WORKERS` in `.env` sets the worker count for pytest-xdist's `-n auto`, where the repository's pytest configuration uses it.

`make lint` runs the lint alone:

```sh
make lint
```

*Formatting* gives `make format`, and *Previewing* gives `make preview`.

### Settings

`bin/settings` is the repository's own, sourced by every runner. Each pass runs only where it defines what the pass reads, so a repository with an empty `bin/settings` gets no pass at all. Each is a function printing NUL-separated paths or arguments; a defined one that fails, or that names nothing, fails the run, except `pytest_args`, which may name nothing. `preview_start`, which `bin/preview` requires, is the one variable.

What `bin/format` rewrites and `bin/lint` checks for drift, each to the tool's own style:

- `palette_args` – `tools/palette-generate`'s arguments. The files it generates belong to this pass alone, so no other set names them: Prettier, for one, lowercases the hex colours the CSS keeps as the YAML writes them, and the JSON pass would sort the keys it keeps in the YAML's order, so format and lint would never agree.
- `shell_files` – shfmt, indented by 4; `bin/lint` also runs ShellCheck.
- `python_files` – ruff, under the repository's own ruff configuration.
- `yaml_files` – keys sorted with yq; `bin/lint` also runs `yamllint --strict`, under the repository's own yamllint configuration.
- `json_files` – indented by 2 with keys sorted, by jq.
- `js_files` – semistandard.
- `css_files` and `html_files` – Prettier, under the repository's own Prettier configuration.
- `md_files` – mdformat, with its GFM and front matter plugins, one paragraph per line.
- `toml_files` – taplo, under the repository's own taplo configuration.
- `docker_files` – dockerfmt, ending each file with a newline; `bin/lint` also runs hadolint, under the repository's own hadolint configuration, and shell-common's `lint/dockerfile-locks`, which fails an install from PyPI or npm that no hashed lock pins. It never names the link to this repository's `Dockerfile` (*The image*).

A tool that fails, on a finding it cannot fix or a file it cannot parse, fails `bin/format`, but only once every later pass has run.

What `bin/lint` checks only:

- `compose_files` – Compose files, each validated on its own, with every profile enabled, by `docker-compose --profile '*' config -q`. It checks neither layout nor key order, so each is also named in `yaml_files`, except the link to this repository's `docker-compose.yml` (*The services*).
- `workflow_files` – GitHub Actions workflows, checked by actionlint; like `compose_files`, each is also named in `yaml_files` for its layout and key order.
- `make_files` – Makefiles, checked by checkmake, under the repository's own `checkmake.ini`.
- `references_args` – `tools/references-check`'s arguments.
- `render_args` – `tools/render-check`'s arguments, such as `--root . pages/*.html`.

What `bin/test` runs, after the lint unless tests are named:

- `pytest_args` – pytest's options, which may be none, for the repository's own tests under its own pytest configuration; `--no-skips` fails a run in which a test skipped. Unless tests are named, `bin/test` passes pytest no paths, so a configuration that does not confine collection, as `testpaths` does, also collects this repository's own tests from `lib/design-tools`.

And `bin/preview`:

- `preview_start`, a variable – the path `/` redirects to, the page the preview opens on.

A `bin/settings` for a design system with a preview page and a palette:

```bash
# shellcheck shell=bash

# Read only by bin/preview, so it looks unused here.
# shellcheck disable=SC2034
preview_start=/preview/

function palette_args {
    printf '%s\0' palette/palette.yml --prefix x --json palette/palette.json --css palette/palette.css
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

`make preview` serves the tree read-only with `bin/preview`, on a free host port, or on `DC_PREVIEW_PORT` where `.env` sets one. The port is published on `127.0.0.1` only; `DC_PREVIEW_HOST` in `.env` publishes it on another address, such as `0.0.0.0` to reach a VM's preview from its host:

```sh
make preview
```

With no port pinned, read it back from another terminal:

```sh
docker compose port preview 8080
```

## Testing

`make test` runs this repository's lint, then its tests, in the `design_test` container (*The services*). The lint fails where anything is unformatted; `make format` (*Formatting*) fixes it.

```sh
make test
```

The tests run under pytest, side by side on one worker for each CPU the container may use, unless `.env` sets the worker count (*The make targets*). A test that skips fails the run: the image carries everything the tests look for, so a skip means something is missing. *The make targets* gives how to name tests and run the lint alone.

## Formatting

`make format` rewrites the tree in place with the passes `bin/format` runs.

```sh
make format
```

## Building

`make build` builds the image's three stages (*The image*):

```sh
make build
```

## Upgrading

Base images are pinned by digest, tools downloaded from releases by `ARG` and checked against a SHA-256 pinned beside each, Python and npm tools by a hashed lock in `Dockerfile.lock/`, and the workflow's actions by commit SHA. `make upgrade` moves `lib/shell-common` to its latest commit and runs its `docker/upgrade` to re-pin each pin the `Dockerfile` names on a `# TRACK FROM` or `# TRACK ARG` line, rebuild each lock it names on a `# TRACK LOCK` line, and re-pin each action the workflow names on a `# TRACK USES` line, proposing a re-pinned workflow in `.github/workflows.new/`. It then builds, tests and commits the result as `Upgrade pins`, with a line for each move as the body, as *The make targets* describes; it never pushes. `docker/upgrade` refuses to start while the `Dockerfile` or `Dockerfile.lock/` has changes not committed or a workflow proposal is pending. It runs on the host, so `make upgrade` needs `curl`, `jq`, `sha256sum` and `timeout` there:

```sh
make upgrade
```

Where the build or tests fail, fix them, then finish the upgrade:

```sh
make upgrade-post
```

## Approving workflows

A change to a workflow is proposed as the whole file in `.github/workflows.new/`, since GitHub refuses an agent's push touching `.github/workflows/`, and CI fails while one is pending. `make workflows-approve` shows each proposal as a diff against its live workflow, asks whether to approve it, then moves it into place, commits that move and pushes:

```sh
make workflows-approve
```

## Licence

Copyright © [Nicolas Williams](https://tiredpixel.com). It is free software, released under the BSD 3-Clause licence, and may be redistributed under the terms specified in `LICENSE`.
