# lib-design-tools

Tools for working on a design system, or on a design built on one: generating a palette's CSS and JSON, checking that pages load and render, colour maths for a design's own tests, a preview server, and a container image to run them in. A repository takes the parts it wants as a submodule and calls them; nothing here checks, rewrites or tests a repository that has not asked for it.

## Requirements

Working on this repository, or on one that takes it as a submodule, needs:

- Docker Engine or Docker Desktop with Compose v2
- `git` with submodule support
- GNU `make` 3.82 or later
- `bash` 4.4 or later

Upgrading this repository's pins (*Upgrading the pins*) runs `docker/upgrade` on the host, so it also needs `curl`, `jq`, `sha256sum` and `timeout` there.

## Using the tools

Each command prints its usage with `--help`.

- `tools/palette-generate` – the JSON and the CSS from a palette YAML, one custom property per colour; `--check` compares them with the files instead of writing. A property is named `--`, the `--prefix` given and a hyphen, then the full YAML key path, lowercased, with `_` written as `-` and list items numbered from 1, so `greys: {warm: [a, b]}` with prefix `x` gives `--x-greys-warm-1` and `--x-greys-warm-2`; a key that would put anything but letters, digits, hyphens or non-ASCII characters in a name fails the run, as do two leaves that come out with the same name, a YAML that holds no colour at all or whose top level is not a mapping, a value starting with `#` that is not a `#RGB`, `#RGBA`, `#RRGGBB` or `#RRGGBBAA` colour, and a value left empty, as an unquoted `#` colour is, since YAML reads it as a comment. Aliases and merge keys are expanded in both outputs alike.
- `tools/references-check` – every local reference in HTML and CSS resolves under `--root`: each attribute that loads or links something, such as `href`, `src`, `srcset`, `imagesrcset`, `poster` and `data`, a refresh's target, and each `url()`, `@import` and `image-set()` string, SVG's `href` and `xlink:href` included, but not an `@namespace` rule's URL, which is never fetched. A reference that leaves `--root` fails, as does one to a directory with no `index.html` or `index.htm` or to a file written with a trailing slash, which a server answers with 404, and so does a file that holds a `<base>` element, which changes how every reference resolves, or that is not valid UTF-8. `--no-external` refuses anything external a page loads, and `--require-integrity` requires `integrity` and `crossorigin` on every external stylesheet and script, and fails any external script loaded by `href` or `xlink:href`, as SVG's is, since SVG's `script` takes no `integrity`. `--unresolved PATH`, repeatable, exempts a `/`-rooted path served by something else, such as a sibling site, for a link, an `area`, a refresh or a `link` whose every `rel` loads nothing, matching wherever a reference lands, query and fragment aside; an exempt path that something loads into the page fails, as does an exemption whose path resolves or that nothing references.
- `tools/render-check` – each page loaded in headless Chromium, failing on an uncaught error, an unhandled rejection, a `console.error`, a failed load the page or its stylesheets start, such as an image, script, stylesheet, `@import`, font or background image, or an element matching `--error-marker`, and on a page Chromium fails or times out on, or where the browser cannot report failed loads started from CSS. A request the page's own script makes, such as `fetch`, is left to the script; the page's icon is not checked, and a load from another origin that does not use CORS counts only where its element reports an error.
- `tools/preview-serve` – the tree as it is on disk, for previewing while editing: nothing cached, dot paths and paths outside the tree refused and left out of listings, directories listed, and `/` redirected to `--start`, the page the preview opens on. For development only.
- `tools/kcov-report` – a kcov Cobertura report, such as `bin/coverage` has kcov write, printed as pytest-cov's term-missing report: a header, a line for each file, named relative to `ROOT` where it lies inside it, with its statements, misses, percentage covered and missing lines, and a `TOTAL` line. A file it cannot read, that is not a kcov Cobertura report or that measures no file fails the run.

Importable from a repository's own Python tests, with `tools/` on the path, as `bin/test` puts it:

- `tools/design_colour.py` – colour maths on opaque `#RGB` and `#RRGGBB` colours: WCAG luminance and contrast, compositing a colour at a given opacity over a ground, CIELAB lightness, chroma and hue, which a grey, black or white has none of, so asking for it raises `ValueError`, the gap and spread between hues and whether one lies on the shorter arc between two others that are not opposite, and CIE76 ΔE.
- `tools/design_render.py` – the harness under `render-check`: serving a tree, rendering a page or printing it to PDF in headless Chromium, and reading what it reports.
- `tools/pytest_no_skips.py` – a pytest plugin, which `bin/test` loads, adding `--no-skips`, which fails a run in which any test skipped.

## Adding the tools to a repository

A repository takes this one at `lib/design-tools`, cloned recursively, since the runners use its own `lib/shell-common`:

```sh
git submodule add https://github.com/TiredPixel/lib-design-tools.git lib/design-tools
git submodule update --init --recursive lib/design-tools
```

Each part below is taken on its own. A tool needs nothing here outside `tools/` and can be called directly in the image; the image needs only the links to its `Dockerfile` and `Dockerfile.lock`; and only the parts that run commands in the image depend on others: the services run the runners, and the make targets run the services.

### Laying out the repository

A repository using the services keeps its designs in `src/` and the page the preview opens on in `preview/`, and everything a preview loads in one of the two, since the `preview` service mounts only those (*Previewing pages*). A file a pass generates, such as a palette's CSS and JSON, lives in a directory `make format` mounts whole, such as `src/`, one holding no ignored path and no submodule: it mounts only the paths that exist when it starts, and takes any other directory entry by entry (*Running the make targets*), so a new file written anywhere else stays in the container's copy and is lost without a word.

### Linking the image

The image builds three stages. `wheels` downloads the Python tools' locked wheels. `dev` is the formatting, linting and test toolchain, headless Chromium included, running as the unprivileged user `x`; it installs the Python and npm tools from the locks in `Dockerfile.lock/` through bind mounts and copies nothing in, so a repository linking the `Dockerfile` builds the same toolchain. `test` adds a copy of the repository's tree to it, submodules included, which every service runs on (*Linking the services*).

A repository builds it through a `Dockerfile` and a `Dockerfile.lock` that link to this one's, since the build reads the locks from the repository's own context:

```sh
ln -s lib/design-tools/Dockerfile Dockerfile
ln -s lib/design-tools/Dockerfile.lock Dockerfile.lock
```

A repository leaves the link out of `docker_files`, since the file is not its own: `make format` never mounts the submodule, so a rewrite through the link would reach only the image's copy and be lost.

The `wheels` and `dev` stages bind-mount `Dockerfile.lock` whole from the repository's tree wherever they read a lock, since through a link BuildKit resolves only the first path below it, so a linked lock builds as a copied one does, even with `dev` built alone. The test stage copies the whole tree. So the repository's `.dockerignore` keeps `.git`, `.env`, `tmp/` and anything local out of it, and must not exclude `Dockerfile.lock` or the submodule. Excluding every top-level dot path, `/.*`, and letting the repository's own dot files back in by name, such as `!/.github`, keeps a per-user directory such as `.claude` out without naming it. A submodule is a checkout too, with a `.env`, `tmp/` and Compose override of its own, so the same exclusions are repeated for each depth submodules sit at, as `/lib/*/.*`, `/lib/*/docker-compose.*.yml` and `/lib/*/tmp/`, and under `/lib/*/lib/*/` for this repository's own submodule.

A repository's CI keeps a registry build cache of its own through the scripts in this repository's `lib/shell-common`. It names its own image in `.dockerrepo`, at its root, which the scripts read:

```text
ghcr.io/OWNER/NAME
```

Its `build` job holds `contents: read` and `packages: read`. After checking the repository out with its submodules, recursively, and logging in to the registry, it builds `dev` with `docker/build-dev-latest-nopush`, which pushes nothing, before `make build`, on the same daemon. The script takes layers from the branch's `.dev` image, and from `latest.dev`, which a push to a branch named `latest` writes:

```yaml
build:
  permissions:
    contents: read
    packages: read
  steps:
    - name: Build dev
      run: |
        lib/design-tools/lib/shell-common/docker/build-dev-latest-nopush
        make build
```

Its `cache` job runs only on a person's push to a branch, never an app's, whose tree would otherwise run with the job's write, and holds `packages: write`. After the same checkout and login, it builds `dev` with `docker/build-dev-latest`, which pushes it as the branch's `.dev` image:

```yaml
cache:
  if: |-
    github.event_name == 'push' && github.ref_type == 'branch' &&
    github.event.sender.type == 'User'
  permissions:
    contents: read
    packages: write
  steps:
    - name: Push dev
      run: |
        lib/design-tools/lib/shell-common/docker/build-dev-latest
```

### Linking the runners

`bin/format`, `bin/lint`, `bin/test`, `bin/coverage` and `bin/preview` run in the image, from the repository's root, and do only what the repository's own `bin/settings` names (*Configuring the passes*). A repository links the ones it uses; each finds this repository by resolving its link:

```sh
mkdir -p bin
for runner in format lint test coverage preview; do ln -s "../lib/design-tools/bin/$runner" "bin/$runner"; done
```

### Linking the services

This repository's `docker-compose.yml` holds the `design`, `design_test` and `preview` services, which run the runners in the image. A repository links it, and Compose resolves every path in it, and reads `.env`, against the repository holding the link:

```sh
ln -s lib/design-tools/docker-compose.yml docker-compose.yml
```

The `design` and `design_test` services sit under the `test` profile, which `make build`, `make coverage`, `make format`, `make lint` and `make test` pass, and the `preview` service under the `preview` profile, which `make preview` selects, so a bare `docker compose up` starts no service. Every service runs from `test`, on the image's copy of the tree, and none mounts the whole tree. The `design` service runs `bin/format`, with the paths it rewrites mounted by name, which only `make format` gives it (*Running the make targets*). The `design_test` service runs the gates and `bin/coverage` on its copy, with no mount and no network. The `preview` service runs `bin/preview` with `src/` and `preview/` mounted read-only over its copy (*Previewing pages*).

A repository whose YAML pass would cover `docker-compose.yml` leaves the link out of `yaml_files`, since the file is not its own and a rewrite through the link would reach only the image's copy, as with `docker_files` (*Linking the image*).

### Configuring the passes

`bin/settings` is the repository's own, sourced by every runner. Each pass runs only where it defines what the pass reads, so a repository with an empty `bin/settings` gets no pass at all. Each is a function printing NUL-separated paths or arguments; a defined one that fails, or that names nothing, fails the run, except `pytest_args`, which may name nothing. `preview_start`, which `bin/preview` requires, is the one variable.

What `bin/format` rewrites and `bin/lint` checks for drift:

- `palette_args` – `tools/palette-generate`'s arguments. The files it generates belong to this pass alone, so no other set names them: Prettier, for one, lowercases the hex colours the CSS keeps as the YAML writes them, and the JSON pass would sort the keys it keeps in the YAML's order, so format and lint would never agree.
- `shell_files` – shfmt, indented by 4; `bin/lint` also runs ShellCheck.
- `python_files` – ruff, under the repository's own ruff configuration.
- `yaml_files` – yq, laid out by its `-P` with keys sorted, which `bin/lint` checks with shell-common's `lint/yaml-keys`; `bin/lint` also runs `yamllint --strict`, under the repository's own yamllint configuration.
- `json_files` – indented by 2 with keys sorted, by jq.
- `js_files` – semistandard.
- `css_files` and `html_files` – Prettier, under the repository's own Prettier configuration.
- `svg_files` – Prettier, with its XML plugin, `@prettier/plugin-xml`, under the repository's own Prettier configuration.
- `md_files` – mdformat, with its GFM and front matter plugins, one paragraph per line.
- `toml_files` – taplo, under the repository's own taplo configuration.
- `dotenv_files` – dotenv-linter, whose fixes `bin/format` applies without leaving a backup.
- `docker_files` – dockerfmt, ending each file with a newline; `bin/lint` also runs hadolint, under the repository's own hadolint configuration, and shell-common's `lint/dockerfile-locks`, which fails an install from PyPI or npm that no hashed lock pins. Never the link to this repository's `Dockerfile` (*Linking the image*).

A tool that fails, on a finding it cannot fix or a file it cannot parse, fails `bin/format`, but only once every later pass has run.

What `bin/lint` checks only:

- `compose_files` – Compose files, each validated on its own, with every profile enabled, by `docker-compose --profile '*' config -q`. It checks neither layout nor key order, so each is also named in `yaml_files`, except the link to this repository's `docker-compose.yml` (*Linking the services*).
- `workflow_files` – GitHub Actions workflows, checked by actionlint, under the repository's own `.github/actionlint.yaml` or `.github/actionlint.yml`; like `compose_files`, each is also named in `yaml_files` for its layout and key order.
- `yaml_lint_files` – YAML that `yaml_files` would reorder or lay out anew, such as a file whose key order carries meaning, checked by `yamllint --strict` alone, under the repository's own yamllint configuration. Leave a file named here out of `yaml_files`, whose yq would rewrite it.
- `xml_lint_files` – XML a reader takes byte for byte, such as an OOXML part, checked for well-formedness and namespaces by expat's `xmlwf -n` alone. Leave a file named here out of `svg_files`, whose Prettier would rewrite it.
- `make_files` – Makefiles, checked by checkmake, under the repository's own `checkmake.ini`.
- `references_args` – `tools/references-check`'s arguments.
- `render_args` – `tools/render-check`'s arguments, such as `--root . pages/*.html`.

What `bin/test` runs, after the lint unless tests are named:

- `pytest_args` – pytest's options, which may be none, for the repository's own tests under its own pytest configuration; `--no-skips` fails a run in which a test skipped. Unless tests are named, `bin/test` passes pytest no paths, so a configuration that does not confine collection, as `testpaths` does, also collects this repository's own tests from `lib/design-tools`. Where it is not defined, naming tests fails the run, and `make coverage` prints no Python report.

And `bin/preview`:

- `preview_start`, a variable – the path `/` redirects to, the page the preview opens on.

A `bin/settings` for a design system with a preview page and a palette:

```bash
# shellcheck shell=bash

# Read only by bin/preview, so it looks unused here.
# shellcheck disable=SC2034
preview_start=/preview/

function palette_args {
    printf '%s\0' src/palette/palette.yml --prefix x --json src/palette/palette.json --css src/palette/palette.css
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

### Running the make targets

`common.mk` gives `build`, `coverage`, `format`, `lint`, `preview`, `test`, `upgrade` and `upgrade-post`, run through the services, and `workflows-approve`, run on the host (*Approving workflows*), and runs every recipe, the including `Makefile`'s own among them, in bash with `-Eeuo pipefail`, `inherit_errexit` and `failglob`. A repository's `Makefile` includes it and adds its own `upgrade-pre`, which moves the submodule to its latest commit and its own submodules to the commits that one records. Its first two lines make the submodule a repository of its own before anything runs in it, since git run in an uninitialised submodule's empty directory acts on the parent instead:

```make
include lib/design-tools/common.mk

upgrade-pre:
	git submodule sync lib/design-tools
	git submodule update --init --checkout lib/design-tools
	cd lib/design-tools && git checkout latest && git pull --ff-only && git submodule update --init --recursive
```

`make build` builds the image's three stages (*Linking the image*):

```sh
make build
```

`make test` runs `bin/test`, which *Configuring the passes* describes, in the `design_test` service; named tests are pytest's arguments, paths from the repository's root:

```sh
make test TESTS='test/<file>.py'
```

`make lint` runs the lint alone:

```sh
make lint
```

`make format` rewrites the tree in place with the passes `bin/format` runs. It runs `bin/format-host` on the host, which reads git's list of the tracked paths and the untracked ones not ignored, and mounts each top-level entry by name over the `design` service's copy of the tree, taking a directory that holds a submodule or an ignored path entry by entry instead. A submodule, `tmp/`, anything ignored, an untracked dot path, a symlink and a path missing on disk are never mounted. `bin/format` refuses to run without those mounts, so a bare `docker compose run design format` fails rather than formatting a copy:

```sh
make format
```

`make coverage` runs `bin/test`, taking `TESTS` as `make test` does, in the `design_test` service, measuring the shell under kcov and the Python under pytest-cov, and prints both reports, in pytest-cov's term-missing form: the Python one as the tests finish, then the shell one, through `tools/kcov-report`. It measures only files under the repository's root, and its shell report leaves out the copy of this repository. It writes nothing to the tree, keeping the measurements in a temporary directory in the container:

```sh
make coverage
```

kcov traces a script only where its trace descriptor, the one `BASH_XTRACEFD` names, is open to it. Python's `subprocess` closes it in a child and pytest-xdist's workers start without it, so a script a test starts goes unmeasured, unless the test runs in pytest's own process and passes the descriptor on with `pass_fds`.

`make preview` serves the tree for a look while editing (*Previewing pages*).

`make upgrade` runs `upgrade-pre`, then `upgrade-post`, which builds and tests, so a change that breaks the repository shows before the bump is committed, and then runs the command in `UPGRADE_COMMIT` where the `Makefile` sets one. A repository sets `UPGRADE_COMMIT` to commit the bump its own way; without it, `make upgrade` leaves the bump uncommitted.

Every target works with no `.env`. Where one exists, it sets local options for Compose, read against the repository holding the link; `.env.example` lists each with its default:

- `DC_BIND_USER`, empty by default for the image's own uid 1000; `0:0` where Docker is rootless or the host user's uid is not 1000, so that `make format` can write to the tree.
- `PYTEST_XDIST_AUTO_NUM_WORKERS`, the worker count for pytest-xdist's `-n auto` under `make test` and `make coverage`, where the repository's pytest configuration uses it; empty by default, for one worker for each CPU the container may use.
- `DC_PREVIEW_HOST` and `DC_PREVIEW_PORT`, where `make preview` publishes its port, by default `127.0.0.1` and `0`, a free port (*Previewing pages*).

### Previewing pages

`make preview` serves the repository with `bin/preview`, from the image's copy of its tree with `src/` and `preview/` mounted read-only over it, so an edit in either shows on reload, while an edit anywhere else needs `make preview` again; a repository missing either directory fails to start it (*Laying out the repository*). It serves on a free host port, or on `DC_PREVIEW_PORT` where `.env` sets one. The port is published on `127.0.0.1` only; `DC_PREVIEW_HOST` in `.env` publishes it on another address, such as `0.0.0.0` to reach a VM's preview from its host:

```sh
make preview
```

With no port pinned, read it back from another terminal:

```sh
docker compose port preview 8080
```

## Working on this repository

This repository uses its own runners, services and make targets as a repository that takes it does (*Adding the tools to a repository*); each task below gives only what is particular to it here.

### Installing it

Clone the repository with its submodules:

```sh
git clone --recurse-submodules https://github.com/TiredPixel/lib-design-tools.git
cd lib-design-tools
```

It needs no `.env`; to change a local option (*Running the make targets*), copy `.env.example` to `.env` and uncomment the line:

```sh
cp .env.example .env
```

### Testing it

`make test` runs this repository's lint, then its tests, in the `design_test` container (*Linking the services*). The lint fails where anything is unformatted; `make format` (*Formatting it*) fixes it.

```sh
make test
```

The tests run under pytest, side by side on one worker for each CPU the container may use, unless `.env` sets the worker count (*Running the make targets*). A test that skips fails the run: the image carries everything the tests look for, so a skip means something is missing. *Running the make targets* gives how to name tests and run the lint alone.

### Measuring its coverage

`make coverage` (*Running the make targets*) measures the shell in `bin/` and the Python in `tools/` and `test/`, and prints both reports:

```sh
make coverage
```

The shell report holds the runners in `bin/` as the gate itself runs them, but not `tools/palette-generate` or the runners the tests run in a fabricated repository: the tests start them through Python's `subprocess` on pytest-xdist's workers, where kcov cannot trace them. `lib/shell-common` measures its own coverage, so the report leaves it out.

### Formatting it

`make format` (*Running the make targets*) fixes what the lint finds unformatted:

```sh
make format
```

### Building the image

`make build` (*Running the make targets*):

```sh
make build
```

### Upgrading the pins

Base images are pinned by digest, tools downloaded from releases by `ARG` and checked against a SHA-256 pinned beside each, Python and npm tools by a hashed lock in `Dockerfile.lock/`, and the workflow's actions by commit SHA.

`make upgrade` moves `lib/shell-common` to its latest commit and runs its `docker/upgrade` to re-pin each pin the `Dockerfile` names on a `# TRACK FROM` or `# TRACK ARG` line, rebuild each lock it names on a `# TRACK LOCK` line, and re-pin each action the workflow names on a `# TRACK USES` line, proposing a re-pinned workflow in `.github/workflows.new/` (*Approving workflows*). It then builds, tests and commits the result as `Upgrade pins`, with a line for each move as the body, as *Running the make targets* describes; it never pushes.

`docker/upgrade` refuses to start while the `Dockerfile` or `Dockerfile.lock/` has changes not committed or a workflow proposal is pending. It runs on the host, with the tools *Requirements* lists:

```sh
make upgrade
```

Where the build or tests fail, fix them, then finish the upgrade:

```sh
make upgrade-post
```

### Approving workflows

A change to a workflow is proposed as the whole file in `.github/workflows.new/`, and CI fails while one is pending. `make workflows-approve` shows each proposal as a diff against its live workflow, asks whether to approve it, then moves it into place, commits that move and pushes:

```sh
make workflows-approve
```

### Reviewing pull requests

A pull request into `latest` merges only once Codex's code and security reviews come back clean on its head commit, which `.github/workflows/review.yml` posts as the checks `review/codex/code` and `review/codex/security`. It is lib-shell-common's review gate, copied from `lib/shell-common/github/review.yml` and copied again only when that template's triggers, job settings or token step change; lib-shell-common's README, *Gating merges on reviews*, gives how the checks are read. A comment on the pull request starts the reviews:

```text
@codex review
```

## Licence

Copyright © [Nicolas Williams](https://tiredpixel.com). It is free software, released under the BSD 3-Clause licence, and may be redistributed under the terms specified in `LICENSE`.
