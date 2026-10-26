# Migrating a Claude Design project to Git

A project exported from Claude Design carries the tool's own machinery, its own copies of its design system and its history. Migrating it removes all three, points every page at the design system's repository as a submodule, and gives the project the tooling, tests and CI that repository uses. The design system's README describes the submodule and the files to link, and its *Migrating from Claude Design* section lists what is particular to that design system.

## Removing Claude Design's machinery

- `.thumbnail`, and the homepage tiles `thumbnail.html` and `Template.dc.html`, with `support.js` where only a tile used it.
- `_ds_manifest.json`, `_ds_bundle.js`, `_adherence.oxlintrc.json` and `SKILL.md`.
- Scripts written for Claude Design's runner, which injects `readFile` and `saveFile`; containerised `bin/` scripts replace them.
- The "omelette starter … copy_starter_component" headers. The runtime they name, and the `omelette-*` meta it creates, stay.
- `CLAUDE.md` rules about `uploads/`, `check_design_system`, `/projects/<uuid>/` and `_ds/` refreshes, and a `CLAUDE.md` with nothing left once they go.

## Removing copies of the design system

- `_ds/<system>-<uuid>/`, Claude Design's bound mirror of the design system: `README.md`, `styles.css`, `colors_and_type.css`, `_ds_bundle.js`, `_ds_manifest.json`, `_adherence.oxlintrc.json`, and in some exports its fonts, tokens and palette files. Every page's `<script>` loading `_ds_bundle.js` goes with it.
- Every other copy: a `brand/` folder, palette files, and logos and marks under other names.

A file is a copy by what it depicts – a design-system logo, mark, token file, palette or font – whatever its bytes, so an older snapshot that has diverged is still a copy; a byte comparison only confirms. A file the design system does not ship is the project's own, or a candidate to add to the design system, and is never deleted on the byte test.

Duplicate assets within the project go too, one copy kept.

## Removing history

- `CHANGELOG.md`.
- History in documents, comments and shims, review rounds and applied revisions included.
- Changing values written into durable text, each replaced by where to read it.

## Replacing card headers

Each `<!-- @dsCard … -->` header becomes a `<title>`, from its `name`, and a `<meta name="description">`, from its `subtitle`. Its `group` and `viewport` go.

## Replacing embedded data

An embedded fallback copy of a data file goes. The page fetches the real file instead, and shows a visible error when the fetch fails.

## Linking the design system

Every page, exports included, links the submodule's files in place of its own copies, at the path the submodule is served or published at, as the design system's README describes.

## Replacing colour literals

Every colour literal – in stylesheets, in page `<style>` blocks, in JSX, and in JavaScript colour maths – becomes a token, or a palette property for a palette colour. An alpha variant is `color-mix()` on one.

## Renaming palette properties

Every palette property takes the name the design system's README gives it. Where the old names follow no single rule, each is mapped by the source key it colours rather than by text substitution, names built at runtime from key lists included. A code its owner has renamed takes its current code.

A composite with no replacement property, such as a gradient built from palette stops, is composed in the page from its stops, in order.

## Keeping runtimes

The runtimes the pages need stay: `support.js` and the `.dc.html` constructs it renders, `doc-page.js`, and in-browser JSX. `.gitattributes` marks them `linguist-vendored`. Nothing loads from a CDN: every script, stylesheet, font and icon is vendored.

## Adding the tooling

The project takes this repository as a submodule of its own, beside the design system's, and is set up as the README's [*Setting up a repository*](../README.md#setting-up-a-repository) describes. Its `bin/format-paths` names the project's pages, stylesheets, scripts and Markdown, and its own tests check its pages render. CI runs the gate, and the README names the submodule as the design system.
