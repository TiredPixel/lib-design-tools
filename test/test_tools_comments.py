"""Tests for tools/comment-check."""

from __future__ import annotations

import contextlib
import io
import os
import pathlib
import subprocess
import tempfile
import unittest

import tools_loader

comment_check = tools_loader.load("comment-check")

EM_DASH = "\u2014"
EN_DASH = "\u2013"


class CheckTestCase(unittest.TestCase):
    kind = "css"

    def problems(self, source: str) -> list[tuple[int, int, str]]:
        return comment_check.check(source, self.kind)

    def assertFlagged(self, source: str, positions: list[tuple[int, int]]) -> None:
        found = self.problems(source)
        self.assertEqual(
            [(line, column) for line, column, _ in found], positions, found
        )

    def assertClean(self, source: str) -> None:
        self.assertFlagged(source, [])

    def assertLexError(self, source: str, line: int, fragment: str) -> None:
        with self.assertRaises(comment_check.LexError) as caught:
            self.problems(source)
        error = caught.exception
        self.assertEqual(comment_check.position(source, error.offset)[0], line)
        self.assertIn(fragment, error.message)


class PunctuationTest(CheckTestCase):
    def test_each_character_and_its_form(self) -> None:
        cases = [
            ("\u2014", "EM DASH", "--"),
            ("\u2026", "HORIZONTAL ELLIPSIS", "..."),
            ("\u2018", "LEFT SINGLE QUOTATION MARK", "'"),
            ("\u2019", "RIGHT SINGLE QUOTATION MARK", "'"),
            ("\u201c", "LEFT DOUBLE QUOTATION MARK", '"'),
            ("\u201d", "RIGHT DOUBLE QUOTATION MARK", '"'),
            ("\u2264", "LESS-THAN OR EQUAL TO", "<="),
            ("\u2265", "GREATER-THAN OR EQUAL TO", ">="),
            ("\u0394", "GREEK CAPITAL LETTER DELTA", "Delta"),
            ("\u2190", "LEFTWARDS ARROW", "<-"),
            ("\u2191", "UPWARDS ARROW", "->"),
            ("\u2192", "RIGHTWARDS ARROW", "->"),
            ("\u2194", "LEFT RIGHT ARROW", "<->"),
            ("\u21c4", "RIGHTWARDS ARROW OVER LEFTWARDS ARROW", "<->"),
            ("\u21d2", "RIGHTWARDS DOUBLE ARROW", "->"),
            ("\u21ff", "LEFT RIGHT OPEN-HEADED ARROW", "<->"),
        ]
        for char, name, form in cases:
            with self.subTest(name=name):
                code = f"U+{ord(char):04X}"
                self.assertEqual(
                    self.problems(f"/* a {char} b */"),
                    [(1, 6, f"{char} ({code} {name}); write {form}")],
                )

    def test_en_dash_closed_up_is_a_range(self) -> None:
        self.assertEqual(
            self.problems(f"/* 2024{EN_DASH}2026 */"),
            [(1, 8, f"{EN_DASH} (U+2013 EN DASH); write -")],
        )
        for source in (
            f"/* a {EN_DASH} b */",
            f"/* a {EN_DASH}b */",
            f"/* a{EN_DASH} b */",
        ):
            with self.subTest(source=source):
                [(_, _, message)] = self.problems(source)
                self.assertTrue(message.endswith("write --"), message)

    def test_en_dash_at_either_end_of_the_source(self) -> None:
        self.assertEqual(comment_check.typewriter(EN_DASH, 0), "--")
        self.assertEqual(comment_check.typewriter(f"a{EN_DASH}", 1), "--")

    def test_only_the_arrows_block(self) -> None:
        self.assertClean("/* \u218f \u2200 */")

    def test_letters_are_never_folded(self) -> None:
        source = (
            "/* Straße naïve Ångström żółw Dvořák "
            "tuffieħ два стола görüşmek δέλτα \u0394E */"
        )
        self.assertFlagged(source, [(1, source.index("\u0394") + 1)])

    def test_typewriter_punctuation_passes(self) -> None:
        self.assertClean("/* a -- b ... 'x' \"y\" -> <- <= >= Delta-E 2024-2026 */")

    def test_code_spans_are_exempt(self) -> None:
        self.assertClean(f"/* `{EM_DASH}` and ``a ` {EM_DASH}`` */")
        self.assertClean(f"/* ``a\n{EM_DASH}`` */")

    def test_unmatched_and_escaped_backticks_open_nothing(self) -> None:
        self.assertFlagged(f"/* ` {EM_DASH} */", [(1, 6)])
        self.assertFlagged(f"/* ``{EM_DASH}` */", [(1, 6)])
        self.assertFlagged(f"/* \\`{EM_DASH}` */", [(1, 6)])

    def test_code_spans(self) -> None:
        self.assertEqual(
            comment_check.code_spans("a `b` ``c ` d`` `e"), [(2, 5), (6, 15)]
        )
        self.assertEqual(comment_check.code_spans("`a`"), [(0, 3)])

    def test_position(self) -> None:
        self.assertEqual(comment_check.position("ab\ncd", 0), (1, 1))
        self.assertEqual(comment_check.position("ab\ncd", 4), (2, 2))


class PythonTest(CheckTestCase):
    kind = "python"

    def test_comments(self) -> None:
        self.assertFlagged(f"x = 1  # a {EM_DASH} b\n", [(1, 12)])
        self.assertFlagged(f"s = 'ěšč'  # {EM_DASH}\n", [(1, 14)])
        self.assertFlagged(f"# `{EM_DASH}` {EM_DASH}\n", [(1, 7)])

    def test_strings_are_not_comments(self) -> None:
        self.assertClean(f'x = \'# {EM_DASH}\'\ny = """{EM_DASH}"""\n')

    def test_docstrings(self) -> None:
        source = (
            f'"""Module {EM_DASH} doc."""\n'
            "\n"
            "class A:\n"
            f'    """Class {EM_DASH} doc."""\n'
            "\n"
            "    def f(self):\n"
            '        """Method\n'
            "\n"
            f"        {EM_DASH} doc.\n"
            '        """\n'
            "\n"
            "\n"
            "async def g():\n"
            f'    """Č {EM_DASH} ``{EM_DASH}``."""\n'
            f'    return "{EM_DASH}"\n'
            "\n"
            "\n"
            "def h():\n"
            "    x = 1\n"
            f'    "{EM_DASH}"\n'
        )
        self.assertFlagged(source, [(1, 11), (4, 14), (9, 9), (14, 10)])

    def test_first_statements_that_are_not_docstrings(self) -> None:
        self.assertClean("")
        self.assertClean(f'f()\n"{EM_DASH}"\n')
        self.assertClean(f'1\n"{EM_DASH}"\n')

    def test_argparse_usage_text(self) -> None:
        source = (
            "import argparse\n"
            "p = argparse.ArgumentParser(\n"
            f'    prog="{EM_DASH}",\n'
            f'    description="{EM_DASH}",\n'
            '    epilog=("a" "\u2026"),\n'
            ")\n"
            "p.add_argument(\n"
            '    "-x",\n'
            '    help=f"{1} \u2264",\n'
            f'    metavar="{EM_DASH}",\n'
            ")\n"
            'p.add_argument_group(title="\u2192")\n'
            'p.add_subparsers(help="``\u2265``")\n'
            'p.add_parser("a", usage="\u0394")\n'
            f'p.add_argument("-z", **{{"help": "{EM_DASH}"}})\n'
            f'other(help="{EM_DASH}")\n'
            f'p.method(help="{EM_DASH}")\n'
            f'(lambda **_: None)(help="{EM_DASH}")\n'
        )
        self.assertFlagged(
            source, [(4, 18), (5, 18), (9, 16), (12, 29), (13, 26), (14, 26)]
        )

    def test_a_comment_inside_usage_text_is_reported_once(self) -> None:
        source = f'p.add_argument(\n    help="a"  # {EM_DASH}\n    " b",\n)\n'
        self.assertFlagged(source, [(2, 17)])

    def test_not_python(self) -> None:
        self.assertLexError("x = 1\n)\n", 2, "not valid Python: unmatched ')'")
        self.assertLexError("x = 1\0\n", 1, "null bytes")


class CssTest(CheckTestCase):
    kind = "css"

    def test_comments(self) -> None:
        self.assertFlagged(f"a {{ color: red; }} /* {EM_DASH} */\n", [(1, 22)])
        self.assertFlagged(f"/*\n * a {EM_DASH}\n */\n", [(2, 6)])

    def test_strings(self) -> None:
        self.assertClean(f'a::before {{ content: "/* {EM_DASH} */"; }}\n')
        self.assertClean(f"b::after {{ content: '\\'/* {EM_DASH} */'; }}\n")
        self.assertClean(f'c::after {{ content: "a\\\n/* {EM_DASH} */"; }}\n')
        self.assertClean(f'd::after {{ content: "a\\\r\n/* {EM_DASH} */"; }}\n')

    def test_url(self) -> None:
        self.assertClean(f"a {{ background: url(//x/*{EM_DASH}*/y.png); }}\n")
        self.assertClean(f"b {{ background: URL( //x/*{EM_DASH}*/y ); }}\n")
        self.assertClean(f'c {{ background: url("/*{EM_DASH}*/") }}\n')
        self.assertClean(f"d {{ background: url(a\\)/*{EM_DASH}*/b) }}\n")

    def test_a_name_ending_in_url_is_not_url(self) -> None:
        self.assertFlagged(f"e {{ --my-url(/*{EM_DASH}*/) }}\n", [(1, 16)])

    def test_escapes(self) -> None:
        self.assertClean(f".a\\/* {EM_DASH} */ {{}}\n")

    def test_cannot_lex(self) -> None:
        self.assertLexError(f"a {{}}\n/* {EM_DASH}\n", 2, "comment never closed")
        self.assertLexError('a { content: "x\n}\n', 1, "string never closed")
        self.assertLexError("a { b: url(x\n", 1, "url( never closed")
        self.assertLexError("a { b: url(x'y) }\n", 1, "holds a quote")
        self.assertLexError("a { b: url(x(y) }\n", 1, "holds a quote")


class JavaScriptTest(CheckTestCase):
    kind = "javascript"

    def test_comments(self) -> None:
        self.assertFlagged(f"x(); // a {EM_DASH}\n", [(1, 11)])
        self.assertFlagged(f"/**\n * {EM_DASH}\n */\nf();\n", [(2, 4)])
        self.assertFlagged(f"#!/usr/bin/env node {EM_DASH}\nx();\n", [(1, 21)])
        self.assertFlagged(f"\ufeff// {EM_DASH}\n", [(1, 5)])
        self.assertFlagged(f"x(); // {EM_DASH}", [(1, 9)])

    def test_a_line_separator_ends_a_line_comment(self) -> None:
        self.assertClean(f"// a\u2028'{EM_DASH}';\n")

    def test_strings(self) -> None:
        self.assertClean(
            f"const a = '// {EM_DASH}' + \"/* {EM_DASH} */\" + 'it\\'s // {EM_DASH}';\n"
        )

    def test_template_literals(self) -> None:
        self.assertFlagged(
            f"const t = `// {EM_DASH} ${{b /* {EM_DASH} */}} \\${{c}} // {EM_DASH}`;\n",
            [(1, 24)],
        )
        self.assertClean(f"const u = `a ${{`b ${{c}} // {EM_DASH}`}} // {EM_DASH}`;\n")
        self.assertClean(f"const v = `${{ {{a: 1}}.a }} // {EM_DASH}`;\n")

    def test_regular_expression_literals(self) -> None:
        self.assertFlagged(
            f"const r = /\\/\\/ {EM_DASH} [/]/g; // {EM_DASH}\n", [(1, 29)]
        )

    def test_where_a_slash_opens_a_regular_expression(self) -> None:
        regex = f"/a[//] {EM_DASH}/"
        for source in (
            f"x = {regex};\n",
            f"f({regex});\n",
            f"return {regex}.test(s);\n",
            f"if (s) {regex}.test(s);\n",
            f"[...{regex}.source];\n",
            f"c ?.5 : {regex};\n",
        ):
            with self.subTest(source=source):
                self.assertClean(source)

    def test_where_a_slash_divides(self) -> None:
        for source, column in (
            (f"a / 2 // {EM_DASH}\n", 10),
            (f"f(x) / 2 // {EM_DASH}\n", 13),
            (f"a[0] / 2 // {EM_DASH}\n", 13),
            (f"a.return / 2 // {EM_DASH}\n", 17),
            (f"a?.typeof / 2 // {EM_DASH}\n", 18),
            (f"'s' / 2 // {EM_DASH}\n", 12),
            (f"`t` / 2 // {EM_DASH}\n", 12),
            (f"`${{a}}` / 2 // {EM_DASH}\n", 15),
            (f"\\u0061 / 2 // {EM_DASH}\n", 15),
            (f"x /= 2 // {EM_DASH}\n", 11),
            (f"/a/ / 2 // {EM_DASH}\n", 12),
        ):
            with self.subTest(source=source):
                self.assertFlagged(source, [(1, column)])

    def test_a_comment_after_a_closing_brace(self) -> None:
        self.assertFlagged(f"{{}}\n// {EM_DASH}\n", [(2, 4)])

    def test_cannot_tell_division_from_a_regular_expression(self) -> None:
        self.assertLexError("if (a) {}\n/b/.test(c);\n", 2, "may divide")
        self.assertLexError("a++ / 2;\n", 1, "may divide")
        self.assertLexError("a-- / 2;\n", 1, "may divide")

    def test_cannot_lex(self) -> None:
        for source, line, fragment in (
            ("x = 'abc\n';\n", 1, "string never closed"),
            ("x = 'abc", 1, "string never closed"),
            ("x = /abc\n/;\n", 1, "regular expression never closed"),
            ("x = /a\\", 1, "regular expression never closed"),
            ("x = `abc\n", 1, "template literal never closed"),
            ("x = 1;\n/* abc\n", 2, "comment never closed"),
            ("}\n", 1, "'}' closes nothing"),
            (")\n", 1, "')' closes nothing"),
            ("f() {\n", 1, "'{' or '${' never closed"),
            ("x = `${a\n", 1, "'{' or '${' never closed"),
            ("f(\n", 1, "'(' never closed"),
        ):
            with self.subTest(source=source):
                self.assertLexError(source, line, fragment)


class YamlTest(CheckTestCase):
    kind = "yaml"

    def test_comments(self) -> None:
        self.assertFlagged(
            f"# {EM_DASH}\nkey: value # {EM_DASH}\nother: a#{EM_DASH} b\n",
            [(1, 3), (2, 14)],
        )

    def test_quoted_scalars(self) -> None:
        self.assertFlagged(
            f'a: "x # {EM_DASH}"\n'
            f"b: 'it''s # {EM_DASH}'\n"
            f'c: "q \\" # {EM_DASH}"\n'
            f'd: "x" # {EM_DASH}\n',
            [(4, 10)],
        )

    def test_quoted_scalars_spanning_lines(self) -> None:
        self.assertFlagged(
            f"a: \"x\n  # {EM_DASH}\n  y\"\n# {EM_DASH}\nb: 'p\n\n  # {EM_DASH}'\n",
            [(4, 3)],
        )
        self.assertFlagged(f'"a\nb": c # {EM_DASH}\n', [(2, 9)])

    def test_block_scalars(self) -> None:
        self.assertFlagged(
            f"a: |\n  # {EM_DASH}\n  t {EM_DASH} # {EM_DASH}\n\n  # {EM_DASH}\n"
            f"b: x # {EM_DASH}\n",
            [(6, 8)],
        )
        self.assertFlagged(f"a: >- # {EM_DASH}\n  x # {EM_DASH}\n", [(1, 9)])

    def test_block_scalar_parent_indent(self) -> None:
        self.assertFlagged(
            f"- k: |\n    # {EM_DASH}\n  o: x # {EM_DASH}\n"
            f"- |\n  # {EM_DASH}\n- y # {EM_DASH}\n",
            [(3, 10), (6, 7)],
        )
        self.assertFlagged(
            f"? a # {EM_DASH}\n: b # {EM_DASH}\n? |\n  # {EM_DASH}\n: c\n",
            [(1, 7), (2, 7)],
        )

    def test_block_scalar_content_indent(self) -> None:
        self.assertFlagged(
            f"a: |\nb: x # {EM_DASH}\nc: |\n    d\n  e # {EM_DASH}\n",
            [(2, 8), (5, 7)],
        )

    def test_document_scalars_and_markers(self) -> None:
        self.assertClean(f"--- |\n# {EM_DASH}\n")
        self.assertClean(f"|\n # {EM_DASH}\n")
        self.assertFlagged(
            f"--- # {EM_DASH}\na: 1\n... # {EM_DASH}\n", [(1, 7), (3, 7)]
        )

    def test_directives(self) -> None:
        self.assertFlagged(
            f"%YAML 1.2 # {EM_DASH}\n%TAG ! x\n---\na: b # {EM_DASH}\n",
            [(1, 13), (4, 8)],
        )

    def test_plain_scalars_spanning_lines(self) -> None:
        for source, flagged in (
            (f"a: x\n  'y # {EM_DASH}\n", [(2, 8)]),
            (f"a: x\n\n  y\n  'z # {EM_DASH}\n", [(4, 8)]),
            (f"- x\n  'y # {EM_DASH}\n", [(2, 8)]),
            (f"x\n'y # {EM_DASH}\n", [(2, 6)]),
            (f"a: x\n  # c\n  'y # {EM_DASH}'\n", []),
            (f"- k: x\n  'y # {EM_DASH}'\n", []),
            (f"a: x # c\n  'y # {EM_DASH}'\n", []),
            (f"a: x\n  y # c\n  'z # {EM_DASH}'\n", []),
            (f"a: http://x # {EM_DASH}\n", [(1, 15)]),
        ):
            with self.subTest(source=source):
                self.assertFlagged(source, flagged)

    def test_flow_collections(self) -> None:
        for source, flagged in (
            (f'a: [b, "c # {EM_DASH}", d] # {EM_DASH}\n', [(1, 22)]),
            (f"a: {{b: c, d: e}} # {EM_DASH}\n", [(1, 19)]),
            (f"{{a:}} # {EM_DASH}\n", [(1, 8)]),
            (f"a: [ ] # {EM_DASH}\n", [(1, 10)]),
            (f"a: [b, , c] # {EM_DASH}\n", [(1, 15)]),
            (f"a: [-1, ? b] # {EM_DASH}\n", [(1, 16)]),
            (f"a: [&x b, *x, !t c] # {EM_DASH}\n", [(1, 23)]),
            (f"a: [&x, b] # {EM_DASH}\n", [(1, 14)]),
            (f"a: [[b], {{c: d}}] # {EM_DASH}\n", [(1, 20)]),
            (f'{{"a":b, "c":[d]}} # {EM_DASH}\n', [(1, 20)]),
            (f"a: [b:c] # {EM_DASH}\n", [(1, 12)]),
            (f"a: [b\n  :c] # {EM_DASH}\n", [(2, 9)]),
            (
                f'a: [\n  b, # {EM_DASH}\n  "c",\n  d e\n  f # {EM_DASH}\n]\n',
                [(2, 8), (5, 7)],
            ),
        ):
            with self.subTest(source=source):
                self.assertFlagged(source, flagged)

    def test_anchors_tags_and_aliases(self) -> None:
        self.assertFlagged(
            f"a: &x !!str v # {EM_DASH}\nb: *x # {EM_DASH}\n", [(1, 17), (2, 9)]
        )
        self.assertFlagged(f"a: &x\n  b: c # {EM_DASH}\n", [(2, 10)])

    def test_carriage_returns(self) -> None:
        self.assertFlagged(f"a: b # {EM_DASH}\r\nc: d\r\n", [(1, 8)])
        self.assertFlagged(f"a: |\r\n  # {EM_DASH}\r\nb: c # {EM_DASH}\r\n", [(3, 8)])

    def test_cannot_lex(self) -> None:
        for source, line, fragment in (
            ("a: [b]#\n", 1, "'#' straight after a token"),
            ("a: 1\nb: [c\n", 2, "flow collection never closed"),
            ('a: 1\nb: "x\n', 2, "quoted scalar never closed"),
            ('a: "x" y\n', 1, "unexpected 'y' after a scalar"),
            ('a: ["b" c]\n', 1, "unexpected 'c' after a scalar"),
            ('"a":b\n', 1, "unexpected ':' after a scalar"),
            ("a: [|b]\n", 1, "block scalar in a flow collection"),
            ("a: ]\n", 1, "']' outside a flow collection"),
            ("a: ,\n", 1, "',' outside a flow collection"),
            ("a: @x\n", 1, "reserved indicator '@'"),
            ("a: `x\n", 1, "reserved indicator '`'"),
            ("a: |2\n  x\n", 1, "explicit indentation indicator"),
            ("a: |x\n", 1, "malformed block scalar header"),
            ("a: | x\n", 1, "text after a block scalar header"),
            ("a:\n  |\n  x\n", 2, "no key or indicator"),
        ):
            with self.subTest(source=source):
                self.assertLexError(source, line, fragment)


class ShellTest(CheckTestCase):
    kind = "shell"

    def test_comments(self) -> None:
        self.assertFlagged(
            f"# {EM_DASH}\necho a # {EM_DASH}\necho a#{EM_DASH} b\n", [(1, 3), (2, 10)]
        )
        self.assertFlagged(f"x;# {EM_DASH}\n", [(1, 5)])
        self.assertFlagged(f"(# {EM_DASH}\n)\n", [(1, 4)])
        self.assertFlagged(f"echo a # {EM_DASH}", [(1, 10)])
        self.assertFlagged(f'cat <<< "# {EM_DASH}" # {EM_DASH}\n', [(1, 17)])

    def test_a_hash_inside_a_word_is_not_a_comment(self) -> None:
        self.assertClean(
            f"echo $# {EM_DASH} ${{#x}} {EM_DASH} a=#{EM_DASH} \\#{EM_DASH}\n"
        )

    def test_quotes(self) -> None:
        self.assertClean(
            f"echo '# {EM_DASH}' \"# {EM_DASH}\" $'# {EM_DASH} \\' x' $\"# {EM_DASH}\"\n"
        )
        self.assertClean(f'echo "a \\" # {EM_DASH}" "`a # {EM_DASH}`"\n')

    def test_dollar_quotes_inside_double_quotes_are_literal(self) -> None:
        self.assertFlagged(f'echo "$\'" # {EM_DASH}\n', [(1, 13)])
        self.assertFlagged(f'echo "a$" # {EM_DASH}\n', [(1, 13)])

    def test_line_continuations(self) -> None:
        self.assertFlagged(f"echo a \\\n  # {EM_DASH}\n", [(2, 5)])
        self.assertClean(f"echo a\\\n#{EM_DASH}\n")

    def test_here_documents(self) -> None:
        for source, flagged in (
            (f"cat <<EOF\n# {EM_DASH}\nEOF\n# {EM_DASH}\n", [(4, 3)]),
            (f"cat <<-EOF\n\t# {EM_DASH}\n\tEOF\n# {EM_DASH}\n", [(4, 3)]),
            (f"cat <<'EOF'\n# {EM_DASH}\nEOF\n", []),
            (f'cat << "E F"\n# {EM_DASH}\nE F\n', []),
            (f"cat <<\\EOF\n# {EM_DASH}\nEOF\n", []),
            (f"cat <<EOF\nit's # {EM_DASH}\nEOF\n", []),
            (f"cat <<A <<B\n# {EM_DASH}\nA\n# {EM_DASH}\nB\n# {EM_DASH}\n", [(6, 3)]),
            (f"cat <<EOF; echo # {EM_DASH}\nx\nEOF\n", [(1, 19)]),
            (f"cat <<EOF\n{EM_DASH}\nEOF", []),
        ):
            with self.subTest(source=source):
                self.assertFlagged(source, flagged)

    def test_command_substitutions_are_lexed_in_turn(self) -> None:
        for source, flagged in (
            (f"x=$(echo a # {EM_DASH}\n)\n", [(1, 14)]),
            (f"x=$(echo ')' \"# {EM_DASH}\")\n", []),
            (f"x=$(a $(b) c) # {EM_DASH}\n", [(1, 17)]),
            (f"x=$( (echo a) ) # {EM_DASH}\n", [(1, 19)]),
            (f"diff <(a # {EM_DASH}\n) >(b)\n", [(1, 12)]),
            (f'"$(echo "# {EM_DASH}")"\n', []),
            (f"x=$(cat <<EOF\n# {EM_DASH}\nEOF\n)\n", []),
            (f"x=$(echo $'a') # {EM_DASH}\n", [(1, 18)]),
        ):
            with self.subTest(source=source):
                self.assertFlagged(source, flagged)

    def test_parameter_expansions(self) -> None:
        self.assertClean(
            f"echo ${{x:-# {EM_DASH}}} ${{y:-'}}# {EM_DASH}'}} ${{z:-\"# {EM_DASH}\"}}"
            " ${w:-$(a)} ${v:-\\}} ${u:-`b`}\n"
        )

    def test_arithmetic(self) -> None:
        self.assertFlagged(f"echo $(( 16#ff + (2) )) # {EM_DASH}\n", [(1, 27)])
        self.assertClean("(( x = 16#ff ))\n")
        self.assertFlagged(f'echo $(( $x + "1" + `b` + \\# )) # {EM_DASH}\n', [(1, 35)])

    def test_backticks(self) -> None:
        self.assertFlagged(f"echo `a # {EM_DASH} \\` b` # {EM_DASH}\n", [(1, 21)])

    def test_usage_text(self) -> None:
        source = (
            "usage() {\n"
            f'    echo "usage: x {EM_DASH} y" >&2\n'
            f'    printf \'%s\\n\' "a \u2026 b" > "{EM_DASH}"\n'
            "    cat <<EOF\n"
            "Run \u2192 it\n"
            "EOF\n"
            f"    if true; then echo '``{EM_DASH}``'; fi\n"
            f"    {{ echo usage: {EM_DASH}; }}\n"
            f'    exit "{EM_DASH}"\n'
            f"    echo \"$(printf '{EM_DASH}')\"\n"
            "}\n"
            f'echo "{EM_DASH} outside"\n'
            f'[ $# -ge 2 ] || usage; echo "{EM_DASH}"\n'
            f"cat <<EOF\n{EM_DASH}\nEOF\n"
        )
        self.assertFlagged(source, [(2, 20), (3, 22), (5, 5), (7, 27), (8, 19)])

    def test_usage_function_forms(self) -> None:
        source = (
            "function usage {\n"
            f'  echo "{EM_DASH}"\n'
            "}\n"
            "function usage() {\n"
            f'  echo "{EM_DASH}"\n'
            "}\n"
            f'usage() {{ echo "{EM_DASH}" >&2; exit 2; }}\n'
            f'echo "{EM_DASH}"\n'
        )
        self.assertFlagged(source, [(2, 9), (5, 9), (7, 17)])

    def test_what_is_not_a_usage_function(self) -> None:
        self.assertClean(
            f'usage() ( echo "{EM_DASH}" )\n'
            f'{{ echo "{EM_DASH}"; }}\n'
            "function other {\n"
            f'  echo "{EM_DASH}"\n'
            "}\n"
            "[ -n x ] || usage"
        )

    def test_cannot_lex(self) -> None:
        for source, fragment in (
            ("x=$(case a in a) b;; esac)\n", "case inside $(...)"),
            ("echo 'a\n", "single quote never closed"),
            ('echo "a\n', "double quote never closed"),
            ("echo `a\n", "backtick never closed"),
            ("echo $'a\n", "$' never closed"),
            ("echo ${a\n", "${ never closed"),
            ("echo $(( 1\n", "(( never closed"),
            ("echo $(a\n", "command substitution never closed"),
            ("cat <<EOF\nx\n", "here-document never ends: no line 'EOF'"),
            ("cat <<EOF", "here-document never ends"),
            ("cat <<\n", "here-document with no delimiter"),
            ("cat << ", "here-document with no delimiter"),
            ("cat <<'EOF\n", "delimiter never closed"),
            ("echo $((a) )\n", "closed by a single ')'"),
            ("echo \"${x:-'a'}\"\n", "single quote inside a double-quoted"),
            ("echo ${x:-{a}}\n", "brace inside ${...}"),
            ("echo ${x:-'a}\n", "single quote never closed"),
        ):
            with self.subTest(source=source):
                self.assertLexError(source, 1, fragment)


class LanguageTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = pathlib.Path(directory.name)

    def language(self, name: str, content: str = "") -> str | None:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return comment_check.language(path)

    def test_by_suffix(self) -> None:
        for name, expected in (
            ("a.py", "python"),
            ("b.CSS", "css"),
            ("c.js", "javascript"),
            ("d.mjs", "javascript"),
            ("e.cjs", "javascript"),
            ("f.yml", "yaml"),
            ("g.yaml", "yaml"),
            ("h.sh", "shell"),
            ("i.bash", "shell"),
        ):
            with self.subTest(name=name):
                self.assertEqual(self.language(name, "#!/bin/sh\n"), expected)

    def test_by_shebang_or_directive(self) -> None:
        for content, expected in (
            ("#!/usr/bin/env python3\n", "python"),
            ("#!/usr/bin/python3.13\n", "python"),
            ("#!/usr/bin/python\n", "python"),
            ("#!/usr/bin/env -S python3 -u\n", "python"),
            ("#!/bin/bash\n", "shell"),
            ("#!/bin/sh\n", "shell"),
            ("#!/usr/bin/env node\n", "javascript"),
            ("#!/usr/bin/env perl\n", None),
            ("#!/usr/bin/env\n", None),
            ("#!\n", None),
            ("# shellcheck shell=bash\n", "shell"),
            ("# shellcheck disable=SC2034 shell=sh\n", "shell"),
            ("# shellcheck shell=zsh\n", None),
            ("plain text\n", None),
            ("", None),
        ):
            with self.subTest(content=content):
                self.assertEqual(self.language("tool", content), expected)
        self.assertEqual(self.language("notes.v2", "#!/bin/bash\n"), "shell")


class MainTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cwd = pathlib.Path(directory.name)

    def write(self, name: str, content: str | bytes) -> None:
        path = self.cwd / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")

    def run_main(self, *arguments: str) -> tuple[int, str]:
        stderr = io.StringIO()
        previous = os.getcwd()
        os.chdir(self.cwd)
        self.addCleanup(os.chdir, previous)
        with contextlib.redirect_stderr(stderr):
            status = comment_check.main(list(arguments))
        return status, stderr.getvalue()

    def test_clean(self) -> None:
        self.write("a.css", "/* a -- b */\n")
        self.write("b.py", "# Česká\n")
        status, output = self.run_main("a.css", "b.py")
        self.assertEqual(status, 0)
        self.assertEqual(output, "checked 2 file(s): 0 problem(s)\n")

    def test_problems(self) -> None:
        self.write("a.css", f"/* a {EM_DASH} b */\n/* 1{EN_DASH}2 */\n")
        self.write("b.yml", "a: b\n")
        status, output = self.run_main("a.css", "b.yml")
        self.assertEqual(status, 1)
        self.assertEqual(
            output.splitlines(),
            [
                f"a.css:1:6: {EM_DASH} (U+2014 EM DASH); write --",
                f"a.css:2:5: {EN_DASH} (U+2013 EN DASH); write -",
                "checked 2 file(s): 2 problem(s)",
            ],
        )

    def test_cannot_lex(self) -> None:
        self.write("a.js", "x = 1;\n}\n")
        self.write("b.css", f"/* {EM_DASH} */\n")
        status, output = self.run_main("a.js", "b.css")
        self.assertEqual(status, 2)
        self.assertEqual(
            output.splitlines(),
            [
                "a.js:2: cannot lex: '}' closes nothing",
                f"b.css:1:4: {EM_DASH} (U+2014 EM DASH); write --",
                "checked 2 file(s): 1 problem(s)",
            ],
        )

    def test_not_utf8(self) -> None:
        self.write("bad.css", b"\xff\n")
        status, output = self.run_main("bad.css")
        self.assertEqual(status, 2)
        self.assertIn("bad.css: not valid UTF-8", output)

    def test_runs_as_a_command(self) -> None:
        self.write("a.sh", f"# {EM_DASH}\n")
        result = subprocess.run(
            [str(tools_loader.TOOLS / "comment-check"), "a.sh"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr,
            f"a.sh:1:3: {EM_DASH} (U+2014 EM DASH); write --\n"
            "checked 1 file(s): 1 problem(s)\n",
        )

    def test_usage_errors(self) -> None:
        self.write("a.css", "")
        self.write("notes.txt", "text\n")
        for arguments in (["missing.css"], ["notes.txt"], ["a.css", "."], []):
            with self.subTest(arguments=arguments):
                with self.assertRaises(SystemExit) as caught:
                    self.run_main(*arguments)
                self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
