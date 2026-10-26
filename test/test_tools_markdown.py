"""Tests for tools/markdown-lint."""

from __future__ import annotations

import contextlib
import io
import os
import pathlib
import subprocess
import tempfile
import unittest

import tools_loader

markdown_lint = tools_loader.load("markdown-lint")

EM_DASH = "\u2014"
EN_DASH = "\u2013"


class MarkdownTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cwd = pathlib.Path(directory.name) / "repo"
        self.cwd.mkdir()

    def write(self, name: str, content: str | bytes) -> pathlib.Path:
        path = self.cwd / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def lint(
        self, text: str, name: str = "doc.md", properties: set[str] | None = None
    ) -> list[tuple[int, str]]:
        path = self.write(name, text)
        return markdown_lint.lint(path, self.cwd, properties)

    def assertClean(self, text: str, **options: object) -> None:
        self.assertEqual(self.lint(text, **options), [])

    def assertProblemLines(self, text: str, lines: list[int], fragment: str) -> None:
        problems = self.lint(text)
        self.assertEqual([line for line, _ in problems], lines, problems)
        for _, message in problems:
            self.assertIn(fragment, message)


class LayoutTest(MarkdownTestCase):
    def test_one_paragraph_per_line(self) -> None:
        self.assertClean("# Title\n\nOne paragraph.\n\nAnother paragraph.\n")

    def test_wrapped_paragraph(self) -> None:
        self.assertProblemLines(
            "First line\nwraps here\nand again.\n", [2, 3], "paragraph wraps"
        )

    def test_backslash_hard_break(self) -> None:
        self.assertClean("Line one\\\nLine two\\\nLine three\n")
        self.assertProblemLines("Line one\\\nLine two\nLine three\n", [3], "paragraph")

    def test_trailing_spaces_are_not_a_hard_break(self) -> None:
        self.assertProblemLines("Line one  \nLine two\n", [2], "paragraph wraps")

    def test_crlf_line_endings(self) -> None:
        self.assertClean("Line one\\\r\nLine two\r\n\r\nNext.\r\n")

    def test_wrapped_list_item(self) -> None:
        self.assertProblemLines(
            "- item one\n  wraps here\n- item two\nlazily wraps\n",
            [2, 4],
            "list item wraps",
        )
        self.assertProblemLines("1. first\n   wraps\n", [2], "list item wraps")

    def test_list_item_hard_break(self) -> None:
        self.assertClean("- item\\\n  continues\n- next\n")
        self.assertProblemLines(
            "- item\\\n  continues\n  wraps\n", [3], "list item wraps"
        )

    def test_nested_items_and_continuations(self) -> None:
        self.assertClean(
            "- item\n"
            "  - nested\n"
            "    - deeper\n"
            "\n"
            "    Continues the deeper item.\n"
            "\n"
            "  Continues the nested item.\n"
            "\n"
            "Back at the top level.\n"
            "\n"
            "1. ordered\n"
            "\n"
            "   Continues the ordered item.\n"
            "10) wide marker\n"
            "\n"
            "    Continues the wide item.\n"
            "-\n"
            "\n"
            "  Continues an empty item.\n"
        )

    def test_misaligned_continuation(self) -> None:
        self.assertProblemLines("- item\n\n   off by one\n", [3], "at 2")
        self.assertProblemLines("- item\n  - nested\n\n   between\n", [4], "at 2")
        self.assertProblemLines("- item\n  - nested\n\n     past\n", [4], "at 4")
        self.assertProblemLines("- item\n\n 1 space\n", [3], "indented 1 space")

    def test_code_inside_items_and_after_text(self) -> None:
        self.assertClean(
            "- item\n"
            "\n"
            "      indented code\n"
            "      more code\n"
            "\n"
            "Text.\n"
            "\n"
            "    top-level indented code\n"
            "    more\n"
        )

    def test_item_after_paragraph_and_paragraph_after_item(self) -> None:
        self.assertClean("Intro:\n- item\n- item\n\nAfter.\n")

    def test_blocks_keep_their_own_structure(self) -> None:
        self.assertClean(
            "---\n"
            "name: front matter\n"
            "description: two lines\n"
            "---\n"
            "# Heading\n"
            "Text after a heading.\n"
            "\n"
            "| a | b |\n"
            "| --- | -- |\n"
            "| 1 | 2 |\n"
            "row without pipe start | x\n"
            "\n"
            "> quote line\n"
            "> another\n"
            "lazy quote\n"
            "\n"
            "<details>\n"
            "<summary>s</summary>\n"
            "text in html\n"
            "\n"
            "<!-- a comment\n"
            "\n"
            "spanning a blank line -- with dashes -->\n"
            "<pre>\n"
            "\n"
            "keep -- this\n"
            "</pre>\n"
            "<!-- one line -->\n"
            "<script>x()</script>\n"
            "<?php ?>\n"
            "<br>\n"
            "\n"
            "[ref]: https://example.com/\n"
            "[other]: https://example.com/2\n"
            "\n"
            "***\n"
            "Text after a rule.\n"
        )

    def test_fenced_code(self) -> None:
        self.assertClean(
            "Text.\n"
            "```sh\n"
            "line one\n"
            "line two -- " + EM_DASH + " `missing/file.md`\n"
            "```\n"
            "~~~~\n"
            "```\n"
            "still code\n"
            "~~~~\n"
            "- item\n"
            "  ```\n"
            "  code\n"
            "  ```\n"
        )

    def test_backtick_fence_info_with_backtick_is_not_a_fence(self) -> None:
        self.assertProblemLines("Text\n```a`b\n", [2], "paragraph wraps")

    def test_unclosed_fence(self) -> None:
        self.assertProblemLines("Text.\n\n```\ncode\n", [3], "never closed")

    def test_unterminated_front_matter_is_a_rule(self) -> None:
        self.assertProblemLines("---\nfirst\nsecond\n", [3], "paragraph wraps")

    def test_setext_headings(self) -> None:
        self.assertClean("Title\n=====\nText.\n\nSub\n---\n")

    def test_lone_tag_cannot_interrupt_paragraph(self) -> None:
        self.assertProblemLines("Text\n<span>\n", [2], "paragraph wraps")
        self.assertClean("Text\n<div>\nhtml\n")
        self.assertClean("<kbd>Ctrl</kbd> copies.\n")
        self.assertClean("<span>\nraw html\nlines -- left alone\n\nText.\n")

    def test_footnote_is_prose(self) -> None:
        self.assertClean("[^1]: A footnote, not a link.\n")


class LinkTest(MarkdownTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.write(
            "docs/guide.md",
            "# Guide\n\n## Set-up & Use `tool`\n\n## Guide\n\n"
            '<a id="Custom"></a>\n\n```\n# not a heading\n```\n',
        )
        self.write("docs/space name.md", "# Spaced\n")
        self.write("img/logo.svg", "")

    def test_resolving_links(self) -> None:
        self.assertClean(
            "See [guide](docs/guide.md), [spaced](docs/space%20name.md),"
            ' [angle](<docs/space name.md>), ![logo](img/logo.svg "Logo"),'
            " [rooted](/docs/guide.md), [dir](docs/), [web](https://example.com/x),"
            " [mail](mailto:a@b), [paren](docs/guide.md#guide) and"
            " [query](docs/guide.md?plain=1).\n"
        )

    def test_missing_links(self) -> None:
        problems = self.lint(
            "[a](missing.md) and ![b](img/none.png) and [c](/none/x.md)\n\n"
            "[ref]: <gone.md>\n"
        )
        self.assertEqual(
            problems,
            [
                (1, "link target missing.md does not exist"),
                (1, "link target img/none.png does not exist"),
                (1, "link target /none/x.md does not exist"),
                (3, "link target gone.md does not exist"),
            ],
        )

    def test_anchors_in_other_files(self) -> None:
        self.assertClean(
            "[a](docs/guide.md#guide) [b](docs/guide.md#set-up--use-tool)"
            " [c](docs/guide.md#guide-1) [d](docs/guide.md#custom)"
            " [e](img/logo.svg#anything) [f](docs/guide.md#Guide)\n"
        )
        problems = self.lint(
            "[a](docs/guide.md#not-a-heading) [b](docs/guide.md#guide-2)\n"
        )
        self.assertEqual(
            [message for _, message in problems],
            [
                (
                    "link docs/guide.md#not-a-heading names no heading or anchor in "
                    "docs/guide.md"
                ),
                (
                    "link docs/guide.md#guide-2 names no heading or anchor in "
                    "docs/guide.md"
                ),
            ],
        )

    def test_anchors_in_same_file(self) -> None:
        self.write("i.png", "")
        text = (
            "# Café Menu \u2013 Today\n\n"
            "## [Linked](x.md) *emphasis* _under_ snake_case `code` ![i](i.png)\n\n"
            "## Closing hashes ##\n\n"
            "Setext\n------\n\n"
            '<span name="named"></span>\n\n'
            "[a](#café-menu--today) [b](#linked-emphasis-under-snake_case-code)"
            " [c](#closing-hashes) [d](#setext) [e](#named) [f](#)\n"
        )
        problems = self.lint(text)
        self.assertEqual(
            [message for _, message in problems], ["link target x.md does not exist"]
        )
        self.assertProblemLines("# Title\n\n[a](#nowhere)\n", [3], "names no heading")

    def test_links_inside_code_are_ignored(self) -> None:
        self.assertClean("Use `[a](missing.md)` literally.\n")

    def test_unclosed_angle_destination_is_ignored(self) -> None:
        self.assertClean("Odd [a](<unclosed and text.\n")

    def test_escaped_and_nested_parentheses(self) -> None:
        self.write("a(1).md", "")
        self.assertClean("[a](a(1).md) [b](a\\(1\\).md)\n")

    def test_empty_destination_is_ignored(self) -> None:
        self.assertClean("[a]() text\n")

    def test_space_before_destination(self) -> None:
        self.assertProblemLines("[a](  gone.md)\n", [1], "gone.md does not exist")


class SlugTest(unittest.TestCase):
    def test_github_rule(self) -> None:
        cases = {
            "Hello, World!": "hello-world",
            "  Spaced  Out  ": "spaced--out",
            "Ünïcödé Ñame": "ünïcödé-ñame",
            "A & B": "a--b",
            "5 Type": "5-type",
            "`--ink-3` token": "--ink-3-token",
            "Emoji \U0001f600 here": "emoji--here",
            "Tom &amp; Jerry": "tom--jerry",
        }
        for heading, expected in cases.items():
            with self.subTest(heading=heading):
                self.assertEqual(markdown_lint.slug(heading), expected)


class PathTest(MarkdownTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("tools/render-check", "")
        self.write("preview/card.css", "")
        self.write("docs/local.md", "")
        self.write("README.md", "")

    def test_existing_paths(self) -> None:
        self.assertClean(
            "Run `tools/render-check` on `preview/` with `preview/card.css`,"
            " see `README.md`, `local.md` and `./README.md`.\n",
            name="docs/page.md",
        )

    def test_missing_paths(self) -> None:
        problems = self.lint(
            "See `tools/gone`, `preview/old.css`, `../nowhere.md`, `./gone.md`"
            " and `` preview/spaced.css ``.\n",
            name="docs/page.md",
        )
        self.assertEqual(
            [message for _, message in problems],
            [
                "`tools/gone` names a path that does not exist",
                "`preview/old.css` names a path that does not exist",
                "`../nowhere.md` names a path that does not exist",
                "`./gone.md` names a path that does not exist",
                "`preview/spaced.css` names a path that does not exist",
            ],
        )

    def test_first_segment_found_beside_the_file_only(self) -> None:
        problems = self.lint("`local.md/x` and `elsewhere/x.md`\n", name="docs/page.md")
        self.assertEqual(
            problems, [(1, "`local.md/x` names a path that does not exist")]
        )

    def test_bare_names_are_never_checked(self) -> None:
        self.assertClean(
            "`missing.json`, `gone.md`, `type-02-families.html`, `README.md`,"
            " `FILE.MD` and `enum_descs.yml`.\n",
            name="docs/page.md",
        )
        for token in ("missing.json", "README.md", "Makefile"):
            with self.subTest(token=token):
                self.assertFalse(markdown_lint.looks_like_path(token))
        self.assertTrue(markdown_lint.looks_like_path("a/missing.json"))

    def test_ignore_path_globs(self) -> None:
        text = (
            "Consumers run `lib/design/tools/render-check`, `lib/design/x.css`,"
            " `vendor/a/b.js` and `tools/gone`.\n"
        )
        self.write("lib/README.md", "")
        self.write("vendor/README.md", "")
        path = self.write("doc.md", text)
        unignored = markdown_lint.lint(path, self.cwd, None)
        self.assertEqual(len(unignored), 4)
        problems = markdown_lint.lint(
            path, self.cwd, None, ("lib/design/*", "vendor/?/b.js")
        )
        self.assertEqual(
            [message for _, message in problems],
            ["`tools/gone` names a path that does not exist"],
        )

    def test_ignore_path_matches_the_token_as_written(self) -> None:
        self.write("lib/README.md", "")
        path = self.write("doc.md", "`./lib/gone.md` and `lib/gone.md`\n")
        problems = markdown_lint.lint(path, self.cwd, None, ("lib/*",))
        self.assertEqual(
            [message for _, message in problems],
            ["`./lib/gone.md` names a path that does not exist"],
        )

    def test_tokens_that_are_not_paths(self) -> None:
        self.assertClean(
            "`text/javascript` `and/or` `1/2` `--root` `-x/y` `/usr/bin/env`"
            " `a b/c.md` `<dir>/x.md` `*.md` `lib/\u2026/tools` `x/y?` `https://x/y.md`"
            " `user@host/x.md` `$HOME/x.md` `{a}/b.md` `.md` `.editorconfig`"
            " `Node.version` `console.log` `a=b/c.md` `x/y#frag` `~/x.md`"
            " `.title-1/2/3` `.dc.html` `.eslintrc.json` `` ` `` ``\n"
        )

    def test_unmatched_and_escaped_backticks(self) -> None:
        self.assertClean("A stray ` backtick and \\`preview/gone.md\\` escaped.\n")
        self.assertClean("Double ``code with ` inside`` here.\n")


class CustomPropertyTest(MarkdownTestCase):
    PROPERTIES = frozenset({"--ink-1", "--ink-3", "--blue-700", "--brand"})

    def test_defined_and_unrelated(self) -> None:
        self.assertClean(
            "Use `--ink-3`, `var(--blue-700)`, `--brand`, `--root`, `--css` and"
            " the `--ink-` prefix.\n",
            properties=self.PROPERTIES,
        )

    def test_undefined_in_known_family(self) -> None:
        problems = self.lint(
            "Use `--ink-2`, `var(--blue-900)` and `--brand-x`.\n",
            properties=self.PROPERTIES,
        )
        self.assertEqual(
            [message for _, message in problems],
            [
                "`--ink-2` is not defined in any --css stylesheet",
                "`var(--blue-900)` is not defined in any --css stylesheet",
                "`--brand-x` is not defined in any --css stylesheet",
            ],
        )

    def test_skipped_without_stylesheets(self) -> None:
        self.assertClean("Use `--ink-2`.\n", properties=None)

    def test_defined_properties_reads_definitions_only(self) -> None:
        sheet = self.write(
            "a.css",
            ":root {\n  --ink-1: #000;\n  --ink-2 : var(--ink-1);\n}\n"
            "/* --commented: 1; */\n"
            "@property --registered { syntax: '<color>'; }\n"
            ".x { color: var(--used-only); }\n",
        )
        other = self.write("b.css", ":root{--blue-700:#00f}")
        self.assertEqual(
            markdown_lint.defined_properties([sheet, other]),
            {"--ink-1", "--ink-2", "--registered", "--blue-700"},
        )


class DashTest(MarkdownTestCase):
    def test_spaced_en_dash_is_clean(self) -> None:
        self.assertClean(f"One {EN_DASH} two.\n\n# Heading {EN_DASH} sub\n")

    def test_em_dash(self) -> None:
        for text in (
            f"One{EM_DASH}two.\n",
            f"# Heading {EM_DASH} sub\n",
            f"- item {EM_DASH} x\n",
            f"| a {EM_DASH} | b |\n| - | - |\n",
            f"> quoted {EM_DASH}\n",
        ):
            with self.subTest(text=text):
                self.assertProblemLines(text, [1], "em dash")

    def test_double_hyphen_as_dash(self) -> None:
        for text in ("One -- two.\n", "Ends with --\n", "-- starts\n", "a\t--\tb\n"):
            with self.subTest(text=text):
                self.assertProblemLines(text, [1], "'--' used as a dash")

    def test_double_hyphen_that_is_not_a_dash(self) -> None:
        self.assertClean(
            "Use `a -- b` and `--flag`, [link](https://x/a--b) and"
            ' <!-- a -- b --> and <span title="a -- b">x</span>, the card--modifier'
            " class, a --- rule and ---.\n"
        )

    def test_dashes_in_table_delimiter_rows(self) -> None:
        self.assertClean("| a | b |\n| -- | -- |\n| 1 | 2 |\n")


class MainTest(MarkdownTestCase):
    def run_main(self, *arguments: str) -> tuple[int, str]:
        stderr = io.StringIO()
        previous = os.getcwd()
        os.chdir(self.cwd)
        self.addCleanup(os.chdir, previous)
        with contextlib.redirect_stderr(stderr):
            status = markdown_lint.main(list(arguments))
        return status, stderr.getvalue()

    def test_clean(self) -> None:
        self.write("a.md", "# A\n\nText `--ink-1` in [b](b.md).\n")
        self.write("b.md", "# B\n")
        self.write("tokens.css", ":root { --ink-1: #000; }")
        status, output = self.run_main("--css", "tokens.css", "a.md", "b.md")
        self.assertEqual(status, 0)
        self.assertEqual(output, "checked 2 file(s): 0 problem(s)\n")

    def test_problems(self) -> None:
        self.write("a.md", "Wrapped\nline -- here `--ink-9`.\n")
        self.write("one.css", ":root { --ink-1: #000; }")
        self.write("two.css", ":root { --blue-1: #000; }")
        status, output = self.run_main("--css", "one.css", "--css", "two.css", "a.md")
        self.assertEqual(status, 1)
        self.assertEqual(
            output.splitlines(),
            [
                (
                    "a.md:2: paragraph wraps onto a second line; write one paragraph"
                    " per line, or end the line with a backslash for a hard break"
                ),
                "a.md:2: `--ink-9` is not defined in any --css stylesheet",
                "a.md:2: '--' used as a dash; use a spaced en dash",
                "checked 1 file(s): 3 problem(s)",
            ],
        )

    def test_ignore_path_option(self) -> None:
        self.write("lib/README.md", "")
        self.write("a.md", "Run `lib/x/tool` or `lib/y/tool`.\n")
        status, output = self.run_main("--ignore-path", "lib/x/*", "a.md")
        self.assertEqual(status, 1)
        self.assertEqual(
            output.splitlines(),
            [
                "a.md:1: `lib/y/tool` names a path that does not exist",
                "checked 1 file(s): 1 problem(s)",
            ],
        )
        status, output = self.run_main(
            "--ignore-path", "lib/x/*", "--ignore-path", "lib/y/*", "a.md"
        )
        self.assertEqual(status, 0)
        self.assertEqual(output, "checked 1 file(s): 0 problem(s)\n")

    def test_runs_as_a_command(self) -> None:
        self.write("a.md", "Wrapped\nline.\n")
        result = subprocess.run(
            [str(tools_loader.TOOLS / "markdown-lint"), "a.md"],
            capture_output=True,
            text=True,
            check=False,
            cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("a.md:2: paragraph wraps", result.stderr)
        self.assertTrue(result.stderr.endswith("checked 1 file(s): 1 problem(s)\n"))

    def test_not_utf8(self) -> None:
        self.write("bad.md", b"\xff\n")
        status, output = self.run_main("bad.md")
        self.assertEqual(status, 1)
        self.assertIn("bad.md: not valid UTF-8", output)

    def test_usage_errors(self) -> None:
        self.write("a.md", "")
        for arguments in (["missing.md"], ["--css", "missing.css", "a.md"], []):
            with self.subTest(arguments=arguments):
                with self.assertRaises(SystemExit) as caught:
                    self.run_main(*arguments)
                self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
