# lib-design-tools

Tools for working on a design system, or on a design built on one: generating a palette's CSS and JSON, checking that pages load and render, colour maths and font checks for a design's own tests, a preview server, moving fonts to their latest releases or to the newest commit touching their folder, building and checking Word templates, and a container image to run them in. A repository takes the parts it wants as a submodule and calls them; nothing here checks, rewrites or tests a repository that has not asked for it.

## Using the tools

Each command prints its usage with `--help`.

- `tools/palette-generate` – the JSON and the CSS from a palette YAML, one custom property per colour; `--check` compares them with the files instead of writing. A property is named `--`, the `--prefix` given and a hyphen, then the full YAML key path, lowercased, with `_` written as `-` and list items numbered from 1, so `greys: {warm: [a, b]}` with prefix `x` gives `--x-greys-warm-1` and `--x-greys-warm-2`; a key that would put anything but letters, digits, hyphens or non-ASCII characters in a name fails the run, as do two leaves that come out with the same name, a YAML that holds no colour at all or whose top level is not a mapping, a value starting with `#` that is not a `#RGB`, `#RGBA`, `#RRGGBB` or `#RRGGBBAA` colour, and a value left empty, as an unquoted `#` colour is, since YAML reads it as a comment. Aliases and merge keys are expanded in both outputs alike.
- `tools/references-check` – every local reference in HTML and CSS resolves under `--root`: each attribute that loads or links something, such as `href`, `src`, `srcset`, `imagesrcset`, `poster` and `data`, a refresh's target, and each `url()`, `@import` and `image-set()` string, SVG's `href` and `xlink:href` included, but not an `@namespace` rule's URL, which is never fetched. A reference that leaves `--root` fails, as does one to a directory with no `index.html` or `index.htm` or to a file written with a trailing slash, which a server answers with 404, and so does a file that holds a `<base>` element, which changes how every reference resolves, or that is not valid UTF-8. `--no-external` refuses anything external a page loads, and `--require-integrity` requires `integrity` and `crossorigin` on every external stylesheet and script, and fails any external script loaded by `href` or `xlink:href`, as SVG's is, since SVG's `script` takes no `integrity`. `--unresolved PATH`, repeatable, exempts a `/`-rooted path served by something else, such as a sibling site, for a link, an `area`, a refresh or a `link` whose every `rel` loads nothing, matching wherever a reference lands, query and fragment aside; an exempt path that something loads into the page fails.
- `tools/render-check` – each page loaded in headless Chromium, failing on an uncaught error, an unhandled rejection, a `console.error`, a failed load the page or its stylesheets start, such as an image, script, stylesheet, `@import`, font or background image, or an element matching `--error-marker`, and on a page Chromium fails or times out on, or where the browser cannot report failed loads started from CSS. A request the page's own script makes, such as `fetch`, is left to the script; the page's icon is not checked, and a load from another origin that does not use CORS counts only where its element reports an error.
- `tools/preview-serve` – the tree as it is on disk, for previewing while editing: nothing cached, dot paths and paths outside the tree refused and left out of listings, directories listed, and `/` redirected to `--start`, the page the preview opens on. For development only.
- `tools/kcov-report` – a Cobertura report, such as kcov or c8 writes, printed as pytest-cov's term-missing report: a header, a line for each file, named relative to `ROOT` where it lies inside it, with its statements, misses, percentage covered and missing lines, and a `TOTAL` line. A statement is a line of a class's own, not the line a method is declared on, which c8's report also lists. A file it cannot read, that is not a Cobertura report it can read or that measures no file fails the run.
- `tools/fonts-upgrade` – the font and licence files a fonts directory's `SHA256SUMS` lists, moved to each family's latest GitHub release, or to the newest commit touching its folder in a GitHub repository, as its `families.toml` describes the families, with `SHA256SUMS` and `RELEASES` rewritten once every file is downloaded and checked; it prints a line `OWNER/REPO OLD -> NEW` or `OWNER/REPO/PATH OLD -> NEW` for each release or commit that moved (*Laying out the repository*).
- `tools/ooxml-check` – each Word template's parts checked against each other: content types, relationships, and the styles, lists and fonts each part names; `--pandoc` also checks the style ids pandoc's docx writer reads, and `--fonts DIR` that each style's face, in the template's styles and the glossary's, is one a TrueType file in `DIR` names, an East Asian face only where a style names one.

Importable from a repository's own Python tests, with `tools/` on the path, as `bin/test` puts it:

- `tools/design_colour.py` – colour maths on opaque `#RGB` and `#RRGGBB` colours: WCAG luminance and contrast, compositing a colour at a given opacity over a ground, CIELAB lightness, chroma and hue, which a grey, black or white has none of, so asking for it raises `ValueError`, the gap and spread between hues and whether one lies on the shorter arc between two others that are not opposite, CIE76 ΔE, and `flatten`, which takes the alpha forms `#RGBA` and `#RRGGBBAA` too and gives the opaque colour one shows over a ground.
- `tools/design_render.py` – the harness under `render-check`: serving a tree, rendering a page, printing it to PDF or taking a screenshot of it in headless Chromium, and reading what it reports.
- `tools/pytest_no_skips.py` – a pytest plugin, which `bin/test` and `bin/coverage` load, adding `--no-skips`, which fails a run in which any test skipped.
- `tools/design_ttf.py` – a TrueType font's tables, and its Windows Unicode BMP US English names, the ones Word reads.
- `tools/design_fonts.py` – a fonts directory checked against its `SHA256SUMS`, and the `@font-face` rules of a stylesheet against the font files they name, each `url()` read against the stylesheet, taking the formats each face must carry, such as `("woff2", "truetype")`.

## Adding the tools to a repository

A repository taking this one needs:

- Docker Engine or Docker Desktop with Compose v2
- `git` with submodule support
- GNU `make` 3.82 or later
- `bash` 4.4 or later
- for `make coverage`, `tar`, which unpacks its report on the host

A repository takes this one at `lib/design-tools`, cloned recursively, since the runners use its own `lib/shell-common`:

```sh
git submodule add https://github.com/TiredPixel/lib-design-tools.git lib/design-tools
git submodule update --init --recursive lib/design-tools
```

Each part below is taken on its own. A tool needs nothing here outside `tools/` and can be called directly in the image; the image needs only the links to its `Dockerfile` and `Dockerfile.lock`; and only the parts that run commands in the image depend on others: the services run the runners, and the make targets run the services.

### Laying out the repository

A repository using the services keeps its designs in `src/` and the page the preview opens on in `preview/`, and everything a preview loads in one of the two, since the `preview` service mounts only those (*Previewing pages*). A file a pass generates, such as a palette's CSS and JSON, lives in a directory `make format` binds whole, such as `src/`, one holding no ignored path and no submodule: it binds only the paths that exist when it starts, and takes any other directory entry by entry (lib-shell-common's `runner/format-host`), so a new file written anywhere else stays in the container's copy and is lost without a word.

A repository whose fonts `make upgrade-fonts` moves keeps them in `src/fonts`, with `SHA256SUMS`, `RELEASES` and `families.toml`, which `tools/fonts-upgrade` reads. Each file `SHA256SUMS` lists starts with its family's prefix and a hyphen: a font keeps the publisher's own name for it, such as `Spectral-Regular.ttf`, and a licence takes the publisher's own name for it after the hyphen, so the publisher's `OFL.txt` is `Spectral-OFL.txt`, sorting beside the family's fonts.

`families.toml` holds a `[[family]]` table for each family, with its `prefix` and GitHub `repository`, and either `[family.directories]`, to follow the repository's latest release, or `path`, to follow a folder in it. A release's family maps each font suffix to the directory in the release's `.zip` assets holding that kind of font, and sets `licences-from-repository = true` where its licence is taken from the repository's root at the release's tag rather than from the assets. A folder's family names the folder's path from the repository's root, such as Google Fonts' build of a family in `google/fonts`, and follows the newest commit on the default branch that touches the folder, taking each file from the folder itself at that full commit: a font, a `.ttf` or `.woff2`, by its own name, and any other file, a licence, by the publisher's name after the prefix:

```toml
[[family]]
prefix = "Spectral"
repository = "google/fonts"
path = "ofl/spectral"

[[family]]
prefix = "ExampleSans"
repository = "example-type/sans-fonts"

[family.directories]
".ttf" = "ttf"
".woff2" = "webfont"
```

`RELEASES` holds a line, sorted, for each repository families follow by release, `OWNER/REPO TAG`, and for each folder families follow by commit, `OWNER/REPO/PATH COMMIT` with the full commit, so two folders of one repository are pinned apart. Each needs its line before the first run. A folder's line moves whenever the newest commit touching the folder does, even where no file `SHA256SUMS` lists changed, as where that commit touched only the folder's metadata or the line was first written with a commit from elsewhere in the repository.

### Linking the image

The image builds three stages. `wheels` downloads the Python tools' locked wheels. `dev` is the formatting, linting and test toolchain, headless Chromium included, running as the unprivileged user `x`; it installs the Python and npm tools from the locks in `Dockerfile.lock/` through bind mounts and copies nothing in, so a repository linking the `Dockerfile` builds the same toolchain. `test` adds a copy of the repository's tree to it, submodules included, which every service runs on (*Linking the services*).

A repository builds it through a `Dockerfile` and a `Dockerfile.lock` that link to this one's, since the build reads the locks from the repository's own context:

```sh
ln -s lib/design-tools/Dockerfile Dockerfile
ln -s lib/design-tools/Dockerfile.lock Dockerfile.lock
```

A repository leaves the link out of `docker_files`, since the file is not its own: `make format` never mounts the submodule, so a rewrite through the link would reach only the image's copy and be lost.

The image's `org.opencontainers.image.source` label takes the build argument `IMAGE_SOURCE`, defaulting to this repository's URL, so a repository that pushes the image passes its own, as in `--build-arg IMAGE_SOURCE=https://github.com/OWNER/REPO`.

The `wheels` and `dev` stages bind-mount `Dockerfile.lock` whole from the repository's tree wherever they read a lock, since through a link BuildKit resolves only the first path below it, so a linked lock builds as a copied one does, even with `dev` built alone. The test stage copies the whole tree. So the repository's `.dockerignore` keeps `.git`, `.env`, `tmp/` and anything local out of it, and must not exclude `Dockerfile.lock` or the submodule. Excluding every top-level dot path, `/.*`, and letting the repository's own dot files back in by name, such as `!/.github`, keeps a per-user directory out without naming it. A submodule is a checkout too, with a `.env` and `tmp/` of its own, so the same exclusions are repeated for each depth submodules sit at, as `/lib/*/.*` and `/lib/*/tmp/`, and under `/lib/*/lib/*/` for this repository's own submodule. A repository that links or extends a dot file of this one, such as `.yamllint.yaml`, lets its target back in by name, as `!/lib/design-tools/.yamllint.yaml`.

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

`bin/format`, `bin/lint`, `bin/test`, `bin/coverage` and `bin/preview` run in the image, from the repository's root, and do only what the repository's own `bin/settings` names (*Configuring the passes*). `bin/lint` and `bin/format` are links to lib-shell-common's runners in `lib/shell-common/runner/`. A repository links the ones it uses; each finds this repository by resolving its link:

```sh
mkdir -p bin
for runner in format lint test coverage preview; do ln -s "../lib/design-tools/bin/$runner" "bin/$runner"; done
```

### Linking the services

This repository's `docker-compose.yml` holds the `preview`, `tooling`, `tooling_test` and `tooling_upgrade` services, which run the runners in the image. A repository links it, and Compose resolves every path in it, and reads `.env`, against the repository holding the link:

```sh
ln -s lib/design-tools/docker-compose.yml docker-compose.yml
```

The `tooling`, `tooling_test` and `tooling_upgrade` services sit under the `test` profile, which `make build`, `make coverage`, `make format`, `make lint`, `make test` and `make upgrade-fonts` pass, and the `preview` service under the `preview` profile, which `make preview` passes, so a bare `docker compose up` starts no service. Every service runs from `test`, on the image's copy of the tree, and none mounts the whole tree. `tooling` runs `bin/format`, with the tree's own paths bound over its copy by `make format` (*Running the make targets*). `tooling_test` runs the gates and `bin/coverage` on its copy, with no mount and no network. `tooling_upgrade` has a network, for `make upgrade-fonts`. `preview` runs `bin/preview` with `src/` and `preview/` mounted read-only over its copy (*Previewing pages*).

A repository whose YAML pass would cover `docker-compose.yml` leaves the link out of `yaml_files`, since the file is not its own and a rewrite through the link would reach only the image's copy, as with `docker_files` (*Linking the image*).

A repository links `.env.example` too, which lists the settings the services read, each commented out with its default:

```sh
ln -s lib/design-tools/.env.example .env.example
```

It leaves the link out of `dotenv_files`, since the file is not its own, as with `docker_files` (*Linking the image*).

### Configuring the passes

`bin/settings` is the repository's own, sourced by every runner. lib-shell-common's README, *Linting and formatting a repository*, gives the sets of the generic passes, such as `shell_files`, `yaml_files` and `md_files`, and their tools. A repository's `bin/settings` sources `lib/design-tools/bin/layer`, which adds the design passes below. Each runs only where `bin/settings` defines what it reads, as a function printing NUL-separated paths or arguments; one that fails, or that names nothing, fails the run, except `pytest_args`, which may name nothing.

What `bin/format` rewrites and `bin/lint` checks:

- `svg_files` – Prettier, with its XML plugin, `@prettier/plugin-xml`, under the repository's own Prettier configuration.
- `palette_args` – `tools/palette-generate`'s arguments, run after every other pass. The files it generates belong to this pass alone, so no other set names them: Prettier, for one, lowercases the hex colours the CSS keeps as the YAML writes them, and the JSON pass would sort the keys it keeps in the YAML's order, so format and lint would never agree.

What `bin/lint` checks only:

- `xml_lint_files` – XML a reader takes byte for byte, such as an OOXML part, checked by expat's `xmlwf -n` alone. Leave a file named here out of `svg_files`, whose Prettier would rewrite it.
- `references_args` – `tools/references-check`'s arguments.
- `render_args` – `tools/render-check`'s arguments, such as `--root . pages/*.html`.
- `ooxml_args` – `tools/ooxml-check`'s arguments, such as `--pandoc Template.dotx`.

What `bin/test` runs, after the lint unless tests are named:

- `pytest_args` – pytest's options, which may be none, for the repository's own tests under its own pytest configuration; `--no-skips` fails a run in which a test skipped. Unless tests are named, `bin/test` passes pytest no paths, so a configuration that does not confine collection, as `testpaths` does, also collects this repository's own tests from `lib/design-tools`. Where it is not defined, naming tests fails the run.

What `bin/build-dotx` reads:

- `dotx_args` – `tools/dotx.py`'s arguments (*Building a Word template*).

And `bin/preview`:

- `preview_start`, a variable – the path `/` redirects to, the page the preview opens on.

A `bin/settings` for a design system with a preview page and a palette:

```bash
# shellcheck shell=bash
# shellcheck source=lib/design-tools/bin/layer
source lib/design-tools/bin/layer

palette_args() {
    printf '%s\0' src/palette/palette.yml --prefix x --json src/palette/palette.json --css src/palette/palette.css
}

css_files() {
    files_sorted preview src -type f -name '*.css'
}

render_args() {
    printf '%s\0' --root . preview/index.html
}

pytest_args() {
    printf '%s\0' --no-skips
}

# shellcheck disable=SC2034 # Read by bin/preview, which sources this file.
preview_start=/preview/
```

### Running the make targets

`common.mk` gives `build`, `coverage`, `format`, `lint`, `preview`, `test`, `upgrade`, `upgrade-pre`, `upgrade-post`, `upgrade-discard`, `upgrade-fonts` and, where a repository sets `DOTX` (*Building a Word template*), `dotx`, run through the services, and `workflows-approve`, run on the host (*Approving workflows*), and runs every recipe, the including `Makefile`'s own among them, in bash with `-Eeuo pipefail`, `inherit_errexit` and `failglob`. A repository's `Makefile` includes it, and sets after the include those of its upgrade variables that differ from their defaults: `UPGRADE_SUBMODULES`, the submodules `make upgrade` moves, `lib/design-tools` by default; `UPGRADE_STEPS`, the targets it runs between moving the pins and building, such as `upgrade-fonts`, none by default; and `UPGRADE_PIN_ARGS`, what `docker/upgrade` re-pins, the workflows by default:

```make
include lib/design-tools/common.mk

UPGRADE_STEPS = upgrade-fonts
```

`make build` builds the image's three stages (*Linking the image*):

```sh
make build
```

`make test` runs `bin/test`, which *Configuring the passes* describes, in the `tooling_test` service; named tests are pytest's arguments, paths from the repository's root:

```sh
make test TESTS='test/<file>.py'
```

`make lint` runs the lint alone:

```sh
make lint
```

`make format` rewrites the tree in place with the passes `bin/format` runs. It runs lib-shell-common's `runner/format-host` on the host, which binds the tree's own paths over the `tooling` service's copy, as that script's header and lib-shell-common's README give, and sets `FORMAT_MOUNTS`, without which `bin/format` refuses to run, so a bare `docker compose run tooling bin/format` fails rather than formatting a copy:

```sh
make format
```

`make coverage` runs the tests, taking `TESTS` as `make test` does, in the `tooling_test` service, measuring the shell under kcov, the Python under pytest-cov and the JavaScript Node runs under the tests through `NODE_V8_COVERAGE`, and writes the report to `tmp/coverage/index.html`. It measures only files under the repository's root, and its shell report leaves out the copy of this repository:

```sh
make coverage
```

kcov traces a script only where its trace descriptor, the one `BASH_XTRACEFD` names, is open to it. Python's `subprocess` closes it in a child and pytest-xdist's workers start without it, so a script a test starts goes unmeasured, unless the test runs in pytest's own process and passes the descriptor on with `pass_fds`.

`bin/coverage` sets `NODE_V8_COVERAGE` for the pytest run alone, so `make test` and `make lint` set nothing and no lint tool is measured. Every Node process under the tests is measured, such as a script a test starts with `node build.mjs` or a command in `tools/` with no extension, unless a test gives it an environment without the variable. The JavaScript report holds each file under the root that one ran, test files included, and none under `node_modules`. `bin/coverage` gives c8 every setting its report reads, so no `.c8rc`, `.nycrc` or `"c8"` key in `package.json` changes it.

Node writes a process's data as it exits, so one ended by a signal it does not handle, other than one it sends itself, writes none, and a test that stops a Node process, as with `terminate()`, leaves that run out (Node's `src/node.cc`, read 2026-11-20). c8 counts every line of a file as a statement, blank and comment lines included (v8-to-istanbul's `lib/source.js`, read 2026-11-20), so the JavaScript report's statements are lines.

`make preview` serves the tree for a look while editing (*Previewing pages*).

`make upgrade-fonts` runs `tools/fonts-upgrade src/fonts` in the `tooling_upgrade` service, with `GH_TOKEN` and `GITHUB_TOKEN` passed through, and records each release or commit that moved for the `Upgrade pins` commit; `make upgrade` runs it where `UPGRADE_STEPS` names it.

`make upgrade` runs `upgrade-pre`, which moves the submodules and re-pins, then each of `UPGRADE_STEPS`, then `upgrade-post`, which builds, formats and tests, so a change that breaks the repository shows before the bump is committed, and commits the whole tree as `Upgrade pins`. It refuses to start on a tree with changes or untracked files, or while a record from an unfinished upgrade is left. Where the build or tests fail, fix them and run `make upgrade-post`; `make upgrade-discard` instead puts back what the upgrade moved.

Every target works with no `.env`. Where one exists, it sets local options for Compose, read against the repository holding the link; `.env.example` lists each with its default:

- `DC_BIND_USER`, empty by default, for the image's user; `0:0` where Docker is rootless, so that `make format` can write to the tree.
- `DC_GID` and `DC_UID`, the image user's ids, by default 1000; the host user's where Docker is rootful and those differ, with a rebuild.
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

### Building a Word template

`bin/build-dotx` builds a Word template from `src/` and a design system, in the image, writing it to stdout. A repository links it and names its arguments in `dotx_args`:

```sh
ln -s ../lib/design-tools/bin/build-dotx bin/build-dotx
```

Each file in `src/` is a part, its package path flattened: `word__styles.xml` is `word/styles.xml`, and `-Content_Types-.xml` is `[Content_Types].xml`. `--design-system PATH` names the design system, which the other paths are read from. Each `--tokens FILE` is a stylesheet whose custom properties, in a rule that always matches the root, its selector holding `:root` alone or after `html` or `*`, at the top level or in `@layer` but in no other at-rule, a token declared more than once with `@layer` or `!important` failing the build, the parts name as `{--name}`, in a colour attribute or a style's character spacing, a later file's token winning. `--logo FILE` is an SVG, embedded as `word/media/image2.svg`, with a PNG fallback 1600 pixels wide as `word/media/image1.png`, the names the template's relationships target. Each `--literal PART=RRGGBB` lets `PART` write that colour as a literal; any other literal but the page's `FFFFFF` fails the build. The glossary's styles, numbering and font table are built from the template's, so `src/` holds none of them. Every entry is dated at `docProps/core.xml`'s `dcterms:modified`, which must be a date at midnight UTC, written `YYYY-MM-DDT00:00:00Z`, so the same parts give the same bytes.

```sh
docker compose --profile test run --rm -T tooling_test bin/build-dotx > Template.dotx
```

A repository setting `DOTX = FILE` before including `common.mk` gets `make dotx`, which writes the template to `FILE`, replacing it only once the build succeeds, and has the build added to `make upgrade`'s steps. `UPGRADE_STEPS` set with `=` after the include would replace that step, so a further step is added with `+=`:

```make
DOTX = Template.dotx

include lib/design-tools/common.mk
```

## Working on this repository

This repository uses its own runners, services and make targets as a repository that takes it does (*Adding the tools to a repository*); each task below gives only what is particular to it here.

### Setting up a working copy

Working on this repository needs what a repository taking it needs (*Adding the tools to a repository*), and:

- for `make upgrade` and `make workflows-approve`, which run on the host, the tools lib-shell-common's README lists for `docker/` and `github/` under *Adding the library to a repository*: `docker/upgrade` drives the Docker daemon, and the upgrade scripts read and write the checkout's git state, which the image leaves out

```sh
git clone --recurse-submodules https://github.com/TiredPixel/lib-design-tools.git
cd lib-design-tools
```

It works with no `.env`. `.env.example` lists each setting `docker-compose.yml` reads, each commented out with its default or an example; copy it to `.env` only to change one:

```sh
cp .env.example .env
```

`make format` writes to the tree as the container's user, whose ids default to those `.env.example` lists. Where Docker is rootless, uid 0 in the container is the host user, so set this in `.env`:

```sh
DC_BIND_USER=0:0
```

Where Docker is rootful and the host user's uid or gid differs from those defaults, leave `DC_BIND_USER` unset and instead set the host user's ids in `.env`, as `id -g` and `id -u` print them, then rebuild with `make build`:

```sh
DC_GID=1001
DC_UID=1001
```

### Testing

`make test` runs the lint, then the tests:

```sh
make test
```

Named tests skip the lint:

```sh
make test TESTS=test/tools/test_design_colour.py
```

CI runs `make test` on each pull request to `latest` and each push to it.

### Measuring coverage

`make coverage` runs the tests with coverage measured, taking `TESTS` as `make test` does, and writes the report to `tmp/coverage/index.html`:

```sh
make coverage
```

### Formatting

`make format` rewrites the tree in place with each tool's safe fixes:

```sh
make format
```

### Linting

`make lint` runs the lint alone and writes nothing:

```sh
make lint
```

### Building the images

```sh
make build
```

### Upgrading

`make upgrade` moves `lib/shell-common`, re-pins each `# TRACK` line in the `Dockerfile` and the workflows' `# TRACK USES` lines, proposes each workflow whose actions move in `.github/workflows.new/`, then builds, formats and tests and commits the result as `Upgrade pins`; it never pushes, and refuses to start while a workflow proposal is pending:

```sh
make upgrade
```

Where the build or tests fail, fix them, then finish the upgrade:

```sh
make upgrade-post
```

To abandon the upgrade instead, `make upgrade-discard` puts back what it moved and removes what it wrote:

```sh
make upgrade-discard
```

### Approving workflows

A change to a workflow is proposed as the whole file in `.github/workflows.new/`, and CI fails while one is pending. `make workflows-approve` shows each proposal as a diff against its live workflow, asks whether to approve it, then moves it into place, commits that move and pushes:

```sh
make workflows-approve
```

### Reviewing pull requests

`.github/workflows/review.yml` posts Codex's code and security reviews of a pull request's head commit as the checks `review/codex/code` and `review/codex/security`. lib-shell-common's README says how the checks are read under *Gating merges on reviews*, and sets the gate up under *Setting a repository up*. A comment on the pull request starts the reviews:

```text
@codex review
```

## Licence

Copyright © [Nicolas Williams](https://tiredpixel.com). It is free software, released under the BSD 3-Clause licence, and may be redistributed under the terms specified in `LICENSE`.
