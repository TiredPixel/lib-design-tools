"""tools/dotx.py and bin/build-dotx, on a template and design system built per test."""

import io
import os
import pathlib
import subprocess
import sys
import zipfile

import dotx
import temp_tree
import tools_loader

BIN = pathlib.Path(__file__).resolve().parents[2] / "bin"

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
PARTS = {
    "-Content_Types-.xml": (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
    ),
    "_rels__.rels": (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"/>'
    ),
    "docProps__core.xml": (
        '<cp:coreProperties xmlns:cp="urn:cp" xmlns:dcterms="http://purl.org/dc/terms/">'
        "<dcterms:modified>2026-01-02T00:00:00Z</dcterms:modified>"
        "</cp:coreProperties>"
    ),
    "docProps__custom.xml": (
        '<Properties xmlns="urn:p" xmlns:vt="urn:vt"><property pid="2" '
        'name="TemplateVersion"><vt:lpwstr>2026-01-02</vt:lpwstr></property>'
        "</Properties>"
    ),
    "word__document.xml": (
        f"<w:document {W}><w:body><w:p><w:r><w:rPr>"
        '<w:color w:val="{--ink}"/><w:shd w:fill="{--veil}"/>'
        "</w:rPr></w:r></w:p></w:body></w:document>"
    ),
    "word__fontTable.xml": f"<w:fonts {W}/>",
    "word__numbering.xml": f"<w:numbering {W}/>",
    "word__styles.xml": (
        f"<w:styles {W}><w:docDefaults><w:rPrDefault><w:rPr>"
        '<w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:styleId="Title"><w:rPr>'
        '<w:spacing w:val="{--wide}"/><w:sz w:val="40"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Subtitle"><w:basedOn w:val="Title"/>'
        '<w:rPr><w:color w:val="{--alias}"/><w:spacing w:val="{--wide}"/></w:rPr>'
        "</w:style></w:styles>"
    ),
}
TOKENS = """
:root {
  --ink: #112233;
  /* A comment holding --ink: #000000; */
  --veil: #11223380;
  --alias: var(--ink);
  --mixed: color-mix(in srgb, var(--ink) 50%, transparent);
  --wide: 0.05em;
}
"""
LOGO = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 2">'
    '<rect width="8" height="2" fill="#112233"/></svg>\n'
)
ARGUMENTS = ("--design-system", "design", "--tokens", "tokens.css")


class Template(temp_tree.TempTreeTestCase):
    def setUp(self):
        super().setUp()
        for name, text in PARTS.items():
            self.write(f"src/{name}", text)
        self.write("design/tokens.css", TOKENS)

    def build(self, *arguments):
        return subprocess.run(
            [sys.executable, "-m", "dotx", *(arguments or ARGUMENTS)],
            cwd=self.root,
            env={**os.environ, "PYTHONPATH": str(tools_loader.TOOLS)},
            capture_output=True,
            check=False,
        )

    def built(self, *arguments):
        result = self.build(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return zipfile.ZipFile(io.BytesIO(result.stdout))

    def part(self, archive, path):
        return archive.read(path).decode()

    def assertRefused(self, message, *arguments):
        result = self.build(*arguments)
        self.assertEqual(result.returncode, 1, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertIn(f"build-dotx: {message}", result.stderr.decode())


class BuildTest(Template):
    def test_resolves_colour_and_spacing_tokens(self):
        archive = self.built()
        document = self.part(archive, "word/document.xml")
        self.assertIn('w:val="112233"', document)
        self.assertIn(
            f'w:fill="{dotx.design_colour.flatten("#11223380", "#FFFFFF")[1:]}"',
            document,
        )
        styles = self.part(archive, "word/styles.xml")
        # 0.05em of a 20 pt size, in twips; Subtitle takes its size from Title.
        self.assertEqual(styles.count('<w:spacing w:val="20"/>'), 2)
        self.assertIn('<w:color w:val="112233"/>', styles)

    def test_a_later_token_file_wins(self):
        self.write("design/more.css", ":root { --ink: #445566; }\n")
        archive = self.built(*ARGUMENTS, "--tokens", "more.css")
        self.assertIn('w:val="445566"', self.part(archive, "word/document.xml"))

    def test_a_last_declaration_without_a_semicolon(self):
        self.write("design/more.css", ":root { --ink: #445566 }\n")
        archive = self.built(*ARGUMENTS, "--tokens", "more.css")
        self.assertIn('w:val="445566"', self.part(archive, "word/document.xml"))

    def test_a_colour_mix_is_composited_over_the_page(self):
        self.write(
            "src/word__document.xml",
            f'<w:document {W}><w:color w:val="{{--mixed}}"/></w:document>',
        )
        expected = dotx.design_colour.composite("#112233", 50, "#FFFFFF")[1:]
        archive = self.built()
        self.assertIn(f'w:val="{expected}"', self.part(archive, "word/document.xml"))

    def colour_of(self, css, token="--t"):
        """The RRGGBB the built template writes for TOKEN, under the CSS given."""
        self.write("design/tokens.css", css)
        self.write(
            "src/word__document.xml",
            f'<w:document {W}><w:color w:val="{{{token}}}"/></w:document>',
        )
        self.write("src/word__styles.xml", f"<w:styles {W}/>")
        document = self.part(self.built(), "word/document.xml")
        return document.split('w:val="')[1].split('"')[0]

    def test_root_anywhere_in_a_selector_list(self):
        for selector in (
            ":root, .a",
            ".a, :root",
            ":ROOT",
            ":/* c */root",
            "a,:root,b",
        ):
            with self.subTest(selector=selector):
                self.assertEqual(
                    self.colour_of(f"{selector} {{ --t: #445566 }}"), "445566"
                )

    def test_a_byte_order_mark_or_a_stray_semicolon(self):
        for css in (
            "\ufeff:root { --t: #445566 }",
            ":root { --a: #000000 };\n:root { --t: #445566 }",
        ):
            with self.subTest(css=css):
                self.assertEqual(self.colour_of(css), "445566")

    def test_a_conditional_or_compound_root_leaves_base_tokens(self):
        for other in (
            "@media (prefers-color-scheme: dark) { :root { --t: #000000 } }",
            "@supports (color: red) { :root { --t: #000000 } }",
            ".dark:root { --t: #000000 }",
            ":root.dark { --t: #000000 }",
            ".dark :root { --t: #000000 }",
            ":root { .nested { --t: #000000 } }",
            "body:root { --t: #000000 }",
            "html :root { --t: #000000 }",
            "*|:root { --t: #000000 }",
            ":root { @media print { --t: #000000 } }",
            ":root { @layer a { .nested { --t: #000000 } } }",
            "@layer a { @media print { :root { --t: #000000 } } }",
            "@media print { @layer a { :root { --t: #000000 } } }",
            "@layer a { .dark:root { --t: #000000 } }",
        ):
            with self.subTest(other=other):
                self.assertEqual(
                    self.colour_of(f":root {{ --t: #445566 }}\n{other}"), "445566"
                )

    def test_a_selector_that_always_matches_the_root(self):
        for selector in (
            "html:root",
            "HTML:root",
            "*:root",
            ":root:root",
            "html:root:root",
            "*|*:root",
            "*|html:root",
            ".a, html:root",
        ):
            with self.subTest(selector=selector):
                self.assertEqual(
                    self.colour_of(f"{selector} {{ --t: #445566 }}"), "445566"
                )

    def test_a_root_rule_in_a_cascade_layer(self):
        for css in (
            "@layer base { :root { --t: #445566 } }",
            "@LAYER { :root { --t: #445566 } }",
            "@layer a { @layer b { :root { --t: #445566 } } }",
            "@layer a { html:root { --t: #445566 } }",
            "@layer a { :/* c */root { --t: #445566 } }",
            "@layer a { :root { --b: #000000 }; html:root { --t: #445566 } }",
            "@layer a { html:root, .b { --t: #445566 } }",
            "@layer a, b;\n@layer a { :root { --t: #445566 } }",
            ":root { @layer a { --t: #445566 } }",
            f"{'@layer a { ' * 5000}:root {{ --t: #445566 }}{' }' * 5000}",
        ):
            with self.subTest(css=css[:60]):
                self.assertEqual(self.colour_of(css), "445566")

    def test_a_token_whose_winner_needs_the_cascade_order(self):
        for css in (
            ":root { --t: #000000 }\n@layer a { :root { --t: #445566 } }",
            "@layer a { :root { --t: #000000 } }\n:root { --t: #445566 }",
            "@layer a { :root { --t: #000000 } }\n@layer b { :root { --t: #445566 } }",
            ":root { --t: #445566 !important; --t: #000000 }",
            ":root { --t: #000000; --t: #445566 !important }",
        ):
            with self.subTest(css=css):
                self.write("design/tokens.css", css)
                self.write(
                    "src/word__document.xml",
                    f'<w:document {W}><w:color w:val="{{--t}}"/></w:document>',
                )
                self.assertRefused("--t is declared more than once with @layer")

    def test_var_with_a_fallback(self):
        for value, expected in (
            ("var(--gone, #445566)", "445566"),
            ("var( --gone , var(--also-gone, #445566) )", "445566"),
            ("var(--set, #000000)", "112233"),
            ("VAR(--set)", "112233"),
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    self.colour_of(f":root {{ --set: #112233; --t: {value} }}"),
                    expected,
                )

    def test_a_colour_mix_in_another_form(self):
        half = dotx.design_colour.composite("#112233", 50, "#FFFFFF")[1:]
        quarter = dotx.design_colour.composite("#112233", 25, "#FFFFFF")[1:]
        for value, expected in (
            ("color-mix(in srgb, transparent, var(--ink) 25%)", quarter),
            ("color-mix(in srgb, 25% var(--ink), transparent)", quarter),
            ("color-mix(in srgb, var(--ink), transparent 75%)", quarter),
            ("color-mix(in srgb, var(--ink) 25%, transparent 25%)", quarter),
            ("color-mix(in srgb, var(--ink) 80%, transparent 80%)", half),
            ("color-mix(in srgb, var(--ink), transparent)", half),
            ("COLOR-MIX(IN SRGB, #112233 50%, TRANSPARENT)", half),
            ("color-mix(var(--ink) 50%, transparent)", half),
            ("color-mix(in oklch longer hue, var(--ink) 50%, transparent)", half),
            ("color-mix(in srgb, var(--ink))", "112233"),
            ("color-mix(in srgb, var(--ink) 0%, transparent 0%)", "FFFFFF"),
            ("color-mix(in srgb, #000000, #ffffff)", "808080"),
            ("color-mix(in srgb, #000000 25%, #ffffff 25%)", "BFBFBF"),
            ("color-mix(in srgb, #000000, #ffffff, #ffffff)", "AAAAAA"),
            ("color-mix(in srgb, var(--ink), var(--clear))", half),
            ("color-mix(in oklab, var(--clear), var(--ink))", half),
        ):
            with self.subTest(value=value):
                tokens = f"--ink: #112233; --clear: transparent; --t: {value}"
                self.assertEqual(self.colour_of(f":root {{ {tokens} }}"), expected)

    def test_a_token_name_with_upper_case_or_an_underscore(self):
        self.write(
            "design/tokens.css",
            ":root { --Ink_1: #445566; --Alias_A: var(--Ink_1); --ink_1: #000000;"
            " --Wide_1: 0.05em }",
        )
        self.write(
            "src/word__document.xml",
            f'<w:document {W}><w:color w:val="{{--Alias_A}}"/></w:document>',
        )
        self.write(
            "src/word__styles.xml",
            f'<w:styles {W}><w:style w:type="paragraph" w:styleId="S"><w:rPr>'
            '<w:spacing w:val="{--Wide_1}"/><w:sz w:val="40"/></w:rPr></w:style>'
            "</w:styles>",
        )
        archive = self.built()
        self.assertIn('w:val="445566"', self.part(archive, "word/document.xml"))
        self.assertIn('<w:spacing w:val="20"/>', self.part(archive, "word/styles.xml"))

    def test_a_spacing_token_through_var(self):
        self.write(
            "design/tokens.css",
            ":root { --base: 0.05em; --wide: var(--gone, var(--base)); --ink: #112233;"
            " --veil: #11223380; --alias: var(--ink) }",
        )
        styles = self.part(self.built(), "word/styles.xml")
        self.assertEqual(styles.count('<w:spacing w:val="20"/>'), 2)

    def test_a_view_box_in_another_form(self):
        for view_box in ("0,0,8,2", " 0 , 0 8,2 ", "+0 -0 8e0 .2e1", "0\n0\t8 2"):
            with self.subTest(view_box=view_box):
                self.write("design/logo.svg", LOGO.replace("0 0 8 2", view_box))
                archive = self.built(*ARGUMENTS, "--logo", "logo.svg")
                png = archive.read(dotx.LOGO_PNG)
                self.assertEqual(png[16:24], (1600).to_bytes(4) + (400).to_bytes(4))

    def test_release_properties_in_another_form(self):
        self.write(
            "src/docProps__custom.xml",
            "<p:Properties xmlns:p='urn:p' xmlns:t='urn:vt'><p:property "
            "name = 'TemplateVersion' pid='2'><t:lpwstr>2026-01-02</t:lpwstr>"
            "</p:property></p:Properties>",
        )
        self.write(
            "src/docProps__core.xml",
            '<c:coreProperties xmlns:c="urn:cp" xmlns:t="http://purl.org/dc/terms/">'
            "<t:modified>2026-01-02T00:00:00Z</t:modified></c:coreProperties>",
        )
        info = self.built().infolist()[0]
        self.assertEqual(info.date_time, (2026, 1, 2, 0, 0, 0))

    def test_generates_the_glossary_twins(self):
        archive = self.built()
        for twin in dotx.GLOSSARY_TWINS:
            with self.subTest(twin=twin):
                self.assertEqual(
                    archive.read(f"word/glossary/{twin}"), archive.read(f"word/{twin}")
                )

    def test_parts_in_package_order_dated_at_the_release(self):
        archive = self.built()
        names = archive.namelist()
        self.assertEqual(
            names[:4],
            [
                "[Content_Types].xml",
                "_rels/.rels",
                "docProps/core.xml",
                "docProps/custom.xml",
            ],
        )
        self.assertEqual(names[4:], sorted(names[4:]))
        for info in archive.infolist():
            with self.subTest(part=info.filename):
                self.assertEqual(info.date_time, (2026, 1, 2, 0, 0, 0))
                self.assertEqual(info.compress_type, zipfile.ZIP_STORED)

    def test_the_same_parts_give_the_same_bytes(self):
        self.assertEqual(self.build().stdout, self.build().stdout)

    def test_an_exempt_literal_passes(self):
        self.write(
            "src/word__document.xml",
            f'<w:document {W}><w:color w:val="abcdef"/></w:document>',
        )
        self.built(*ARGUMENTS, "--literal", "word/document.xml=ABCDEF")

    def test_embeds_the_logo_with_a_png_fallback(self):
        self.write("design/logo.svg", LOGO)
        archive = self.built(*ARGUMENTS, "--logo", "logo.svg")
        self.assertEqual(archive.read(dotx.LOGO_SVG), LOGO.encode())
        png = archive.read(dotx.LOGO_PNG)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(png[16:24], (1600).to_bytes(4) + (400).to_bytes(4))


class RefusalTest(Template):
    def test_a_glossary_twin_in_src(self):
        self.write("src/word__glossary__styles.xml", PARTS["word__styles.xml"])
        self.assertRefused("word/glossary/styles.xml is built from word/styles.xml")

    def test_a_colour_literal(self):
        self.write(
            "src/word__document.xml",
            f'<w:document {W}><w:color w:val="abcdef"/></w:document>',
        )
        self.assertRefused(
            "word/document.xml writes colour literals ['abcdef']; name tokens"
        )
        self.assertRefused(
            "word/document.xml writes colour literals ['abcdef']; name tokens",
            *ARGUMENTS,
            "--literal",
            "word/styles.xml=ABCDEF",
        )

    def test_a_template_version_missing_or_not_a_date(self):
        custom = PARTS["docProps__custom.xml"]
        self.write("src/docProps__custom.xml", custom.replace("TemplateVersion", "X"))
        self.assertRefused("docProps/custom.xml sets no TemplateVersion")
        self.write("src/docProps__custom.xml", custom.replace("2026-01-02", "soon"))
        self.assertRefused("TemplateVersion: Invalid isoformat string: 'soon'")

    def test_modified_other_than_the_release(self):
        core = PARTS["docProps__core.xml"].replace("2026-01-02", "2026-01-03")
        self.write("src/docProps__core.xml", core)
        self.assertRefused(
            "docProps/core.xml: dcterms:modified is 2026-01-03T00:00:00Z, not "
            "RELEASET00:00:00Z"
        )

    def test_a_file_that_is_not_xml(self):
        self.write("src/notes.txt", "not a part\n")
        self.assertRefused("src/notes.txt: not XML")

    def test_a_file_that_is_not_utf_8(self):
        self.write("src/word__footer1.xml", b"<w:ftr>\xff</w:ftr>")
        self.assertRefused("src/word__footer1.xml: not UTF-8")

    def test_an_argument_that_is_not_part_and_colour(self):
        result = self.build(*ARGUMENTS, "--literal", "word/document.xml")
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"is not PART=RRGGBB", result.stderr)


class HardeningTest(Template):
    """Input that once ended the build in a traceback, refused or built instead."""

    def styles(self, *styles):
        """styles.xml holding STYLES and no document defaults."""
        self.write(
            "src/word__styles.xml", f"<w:styles {W}>{''.join(styles)}</w:styles>"
        )

    def test_a_style_without_a_size_but_no_tracking_builds(self):
        self.styles(
            '<w:style w:type="paragraph" w:styleId="Normal"><w:rPr>'
            '<w:color w:val="{--ink}"/></w:rPr></w:style>'
        )
        archive = self.built()
        self.assertIn('w:val="112233"', self.part(archive, "word/styles.xml"))

    def test_tracking_in_a_style_without_a_size(self):
        def style(style_id, based="", size=""):
            return (
                f'<w:style w:type="paragraph" w:styleId="{style_id}">{based}'
                f'<w:rPr><w:spacing w:val="{{--wide}}"/>{size}</w:rPr></w:style>'
            )

        no_size = (
            "no size is set by it, a style it is based on or the document defaults"
        )
        for styles, message in (
            ((style("Normal"),), f"Normal: {no_size}"),
            (
                (style("Normal", '<w:basedOn w:val="Gone"/>'),),
                "Normal: its basedOn chain reaches Gone, which is no style",
            ),
            (
                (style("Normal", "<w:basedOn/>"),),
                "Normal: its basedOn chain reaches a basedOn with no w:val",
            ),
            (
                (style("Normal", '<w:basedOn w:val="Normal"/>'),),
                "Normal: its basedOn chain loops back to Normal",
            ),
            (
                (
                    style("A", '<w:basedOn w:val="B"/>'),
                    style("B", '<w:basedOn w:val="A"/>'),
                ),
                "A: its basedOn chain loops back to A",
            ),
            (
                (style("Normal", size='<w:sz w:val="big"/>'),),
                "Normal: w:sz 'big' is not a size in half-points",
            ),
            (
                (style("Normal", size="<w:sz/>"),),
                "Normal: w:sz None is not a size in half-points",
            ),
        ):
            with self.subTest(message=message):
                self.styles(*styles)
                self.assertRefused(f"word/styles.xml: {message}")

    def test_a_token_that_is_missing_looping_or_of_the_wrong_kind(self):
        self.write(
            "design/tokens.css",
            TOKENS.replace(
                "}",
                "--self: var(--self);\n--ping: var(--pong);\n--pong: color-mix(in "
                "srgb, var(--ping) 50%, transparent);\n}",
            ),
        )
        for token, message in (
            ("--gone", "--gone is not a design-system token"),
            ("--self", "--self is defined through itself: --self -> --self"),
            ("--ping", "--ping is defined through itself: --ping -> --pong -> --ping"),
            ("--wide", "--wide is '0.05em', which is not a colour Word can take"),
        ):
            with self.subTest(token=token):
                self.write(
                    "src/word__document.xml",
                    f'<w:document {W}><w:color w:val="{{{token}}}"/></w:document>',
                )
                self.assertRefused(f"word/document.xml: {message}")
        self.write("src/word__document.xml", PARTS["word__document.xml"])
        self.write(
            "src/word__styles.xml",
            PARTS["word__styles.xml"].replace(
                '<w:spacing w:val="{--wide}"/><w:sz',
                '<w:spacing w:val="{--ink}"/><w:sz',
            ),
        )
        self.assertRefused(
            "word/styles.xml: Title: --ink is '#112233', not a length in em"
        )
        self.write("design/tokens.css", TOKENS.replace("0.05em", "1" * 400 + "em"))
        self.write("src/word__styles.xml", PARTS["word__styles.xml"])
        self.assertRefused(
            "word/styles.xml: Title: --wide: cannot convert float infinity to integer"
        )

    def test_a_long_chain_of_tokens(self):
        # Longer than Python's default recursion limit, through var() and color-mix().
        links = 1200
        tokens = "".join(
            f"--a{i}: var(--a{i + 1});\n"
            f"--m{i}: color-mix(in srgb, var(--m{i + 1}) 100%, transparent);\n"
            for i in range(links)
        )
        self.write(
            "design/tokens.css",
            TOKENS.replace(
                "}", f"{tokens}--a{links}: var(--ink);\n--m{links}: var(--ink);\n}}"
            ),
        )
        self.write(
            "src/word__document.xml",
            f'<w:document {W}><w:color w:val="{{--a0}}"/><w:shd w:fill="{{--m0}}"/>'
            "</w:document>",
        )
        mixed = "#112233"
        for _ in range(links):
            mixed = dotx.design_colour.composite(mixed, 100, "#FFFFFF")
        document = self.part(self.built(), "word/document.xml")
        self.assertIn('w:val="112233"', document)
        self.assertIn(f'w:fill="{mixed[1:]}"', document)

    def test_a_spacing_token_in_other_white_space_or_quotes(self):
        for written in (
            '<w:spacing w:val="{--wide}" />',
            "<w:spacing  w:val = '{--wide}'/>",
            '<w:spacing\n w:val="{--wide}"\n/>',
        ):
            with self.subTest(written=written):
                self.styles(
                    '<w:style w:type="paragraph" w:styleId="S"><w:rPr>'
                    f'{written}<w:sz w:val="40"/></w:rPr></w:style>'
                )
                styles = self.part(self.built(), "word/styles.xml")
                self.assertIn('<w:spacing w:val="20"/>', styles)

    def test_a_style_tag_in_other_white_space_or_quotes(self):
        for opening in (
            """<w:style w:type="paragraph" w:styleId='S'>""",
            '<w:style\n w:styleId = "S"\n w:type="paragraph"\n>',
        ):
            with self.subTest(opening=opening):
                self.styles(
                    '<w:style w:type="paragraph" w:styleId="Empty"/>'
                    f'{opening}<w:rPr><w:spacing w:val="{{--wide}}"/>'
                    '<w:sz w:val="40"/></w:rPr></w:style >'
                )
                styles = self.part(self.built(), "word/styles.xml")
                self.assertIn('<w:spacing w:val="20"/>', styles)

    def test_a_style_id_written_with_a_reference(self):
        for written in ("A&amp;B", "A&#38;B"):
            with self.subTest(written=written):
                self.styles(
                    f'<w:style w:type="paragraph" w:styleId="{written}"><w:rPr>'
                    '<w:spacing w:val="{--wide}"/><w:sz w:val="40"/></w:rPr></w:style>'
                )
                styles = self.part(self.built(), "word/styles.xml")
                self.assertIn('<w:spacing w:val="20"/>', styles)

    def test_a_style_written_in_a_comment(self):
        self.styles('<!-- <w:style w:styleId="a&b"></w:style> -->')
        self.assertIn("<!--", self.part(self.built(), "word/styles.xml"))

    def test_a_spacing_tag_a_pattern_would_misread(self):
        for opening, spacing, expected in (
            (
                '<w:style w:type="paragraph" w:customStyle=">" w:styleId="S">',
                '<w:spacing w:val="{--wide}"/>',
                '<w:spacing w:val="20"/>',
            ),
            (
                '<w:style w:type="paragraph" w:styleId="S">',
                '<w:spacing w:val="{--wide}"></w:spacing>',
                '<w:spacing w:val="20"></w:spacing>',
            ),
            (
                """<w:style w:name="w:styleId='Z'" w:styleId="S">""",
                '<w:spacing w:val="{--wide}"/>',
                '<w:spacing w:val="20"/>',
            ),
        ):
            with self.subTest(opening=opening, spacing=spacing):
                self.styles(
                    '<w:style w:type="paragraph" w:styleId="Z"><w:rPr>'
                    '<w:sz w:val="80"/></w:rPr></w:style>'
                    f'{opening}<w:rPr>{spacing}<w:sz w:val="40"/></w:rPr></w:style>'
                )
                self.assertIn(expected, self.part(self.built(), "word/styles.xml"))

    def test_a_token_nested_or_long_past_a_limit_the_template_does_not_name(self):
        depth = 5000
        self.write(
            "design/tokens.css",
            TOKENS.replace(
                "}",
                f"--deep: {'var(--gone, ' * depth}#000000{')' * depth};\n"
                f"--long: {'1' * 5000}em;\n}}",
            ),
        )
        self.assertIn('w:val="112233"', self.part(self.built(), "word/document.xml"))

    def test_a_deep_fallback_the_template_names(self):
        depth = 5000
        self.write(
            "design/tokens.css",
            f":root {{ --t: {'var(--gone, ' * depth}#445566{')' * depth}; --wide: 0 }}",
        )
        self.styles()
        self.write(
            "src/word__document.xml",
            f'<w:document {W}><w:color w:val="{{--t}}"/></w:document>',
        )
        self.assertIn('w:val="445566"', self.part(self.built(), "word/document.xml"))

    def test_a_mix_naming_a_token_twice_along_a_chain(self):
        links = 200
        chain = "".join(
            f"--m{i}: color-mix(in srgb, var(--m{i + 1}), var(--m{i + 1}));\n"
            for i in range(links)
        )
        self.write(
            "design/tokens.css",
            f":root {{ {chain} --m{links}: #445566; --wide: 0 }}",
        )
        self.styles()
        self.write(
            "src/word__document.xml",
            f'<w:document {W}><w:color w:val="{{--m0}}"/></w:document>',
        )
        self.assertIn('w:val="445566"', self.part(self.built(), "word/document.xml"))

    def test_a_colour_mix_that_cannot_be_resolved(self):
        for value, message in (
            ("color-mix(in srgb, #000000 150%, transparent)", "--t: 150.0% is not"),
            (
                "color-mix(in oklab, #000000, #ffffff)",
                "--t mixes 2 colours in oklab, and only a mix in srgb",
            ),
            (
                "color-mix(#000000, #ffffff)",
                "--t mixes 2 colours in oklab, and only a mix in srgb",
            ),
            (
                "color-mix(in nowhere, #000000, transparent)",
                "--t is 'color-mix(in nowhere, #000000, transparent)', which is not",
            ),
            ("var(--gone)", "--gone is not a design-system token"),
            ("var(--t, #000000)", "--t is defined through itself"),
            ("var(--gone,)", "--t is '', which is not a colour"),
            ("transparent", "--t is 'transparent', which is not a colour"),
        ):
            with self.subTest(value=value):
                self.write("design/tokens.css", f":root {{ --t: {value}; --wide: 0 }}")
                self.write(
                    "src/word__document.xml",
                    f'<w:document {W}><w:color w:val="{{--t}}"/></w:document>',
                )
                self.assertRefused(f"word/document.xml: {message}")

    def test_a_placeholder_naming_no_token(self):
        self.write(
            "src/word__document.xml",
            f'<w:document {W}><w:color w:val="{{--Gone_1}}"/></w:document>',
        )
        self.assertRefused("word/document.xml: --Gone_1 is not a design-system token")

    def test_a_part_missing_from_src(self):
        for name, path in (
            ("word__numbering.xml", "word/numbering.xml"),
            ("docProps__custom.xml", "docProps/custom.xml"),
            ("docProps__core.xml", "docProps/core.xml"),
        ):
            with self.subTest(path=path):
                (self.root / "src" / name).rename(self.root / name)
                self.assertRefused(f"{path} is missing from src/")
                (self.root / name).rename(self.root / "src" / name)

    def test_a_modified_date_missing(self):
        core = PARTS["docProps__core.xml"].replace(
            "dcterms:modified", "dcterms:created"
        )
        self.write("src/docProps__core.xml", core)
        self.assertRefused(
            "docProps/core.xml: dcterms:modified is missing, not RELEASET00:00:00Z"
        )

    def test_a_release_a_zip_cannot_date(self):
        for date in ("1979-12-31", "2108-01-01"):
            with self.subTest(date=date):
                for name in ("docProps__core.xml", "docProps__custom.xml"):
                    self.write(f"src/{name}", PARTS[name].replace("2026-01-02", date))
                self.assertRefused(f"TemplateVersion: {date} is not from 1980 to 2107")

    def test_a_file_that_cannot_be_read(self):
        self.write("src/word__media/image3.png", "png")
        self.assertRefused("src/word__media: Is a directory")

    def test_a_part_in_an_unknown_encoding(self):
        self.write(
            "src/word__footer1.xml", '<?xml version="1.0" encoding="bogus"?><a/>'
        )
        self.assertRefused("src/word__footer1.xml: not XML: unknown encoding")

    def test_no_src(self):
        (self.root / "src").rename(self.root / "elsewhere")
        self.assertRefused("src: No such file or directory")

    def test_a_tokens_file_or_logo_missing(self):
        self.assertRefused(
            "design/gone.css: No such file or directory",
            "--design-system",
            "design",
            "--tokens",
            "gone.css",
        )
        self.assertRefused(
            "design/gone.svg: No such file or directory",
            *ARGUMENTS,
            "--logo",
            "gone.svg",
        )

    def test_a_logo_that_is_not_xml(self):
        self.write("design/logo.svg", '<svg viewBox="0 0 8 2">')
        self.assertRefused("the logo is not XML", *ARGUMENTS, "--logo", "logo.svg")

    def test_a_logo_without_a_usable_view_box(self):
        for view_box, message in (
            (None, "the logo has no viewBox"),
            ("0 0 0 2", "the logo's viewBox has no width to scale its height by"),
            (
                "0 0 1.2.3 2",
                "the logo's viewBox: could not convert string to float: '1.2.3'",
            ),
            (f"0 0 8 {'2' * 400}", "the logo's viewBox is too large to scale"),
            # Finite, but scaled past a float, by a vast height or a subnormal width.
            (f"0 0 8 1{'0' * 308}", "the logo's viewBox is too large to scale"),
            (f"0 0 0.{'0' * 323}5 2", "the logo's viewBox is too large to scale"),
            ("0 0 8", "the logo's viewBox '0 0 8' is not four numbers"),
            ("0,,0,8,2", "the logo's viewBox '0,,0,8,2' is not four numbers"),
            (
                "0 0 nan 2",
                "the logo's viewBox: could not convert string to float: 'nan'",
            ),
            ("0 0 -8 2", "the logo's viewBox '0 0 -8 2' has a negative size"),
        ):
            with self.subTest(message=message):
                logo = LOGO.replace(' viewBox="0 0 8 2"', "")
                if view_box is not None:
                    logo = LOGO.replace("0 0 8 2", view_box)
                self.write("design/logo.svg", logo)
                self.assertRefused(message, *ARGUMENTS, "--logo", "logo.svg")


class RunnerTest(Template):
    def setUp(self):
        super().setUp()
        (self.root / "bin").mkdir()
        (self.root / "lib").mkdir()
        (self.root / "lib" / "design-tools").symlink_to(BIN.parent)
        (self.root / "bin" / "build-dotx").symlink_to(
            "../lib/design-tools/bin/build-dotx"
        )
        self.write(
            "bin/settings",
            "# shellcheck shell=bash\nsource lib/design-tools/bin/layer\n"
            "dotx_args() {\n"
            "    printf '%s\\0' --design-system design --tokens tokens.css\n"
            "}\n",
        )

    def run_runner(self, *arguments):
        # Without kcov's trace settings, which make coverage gives the tests: the
        # descriptor they name is not open here.
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("BASH_ENV", "BASH_XTRACEFD")
        }
        return subprocess.run(
            ["bin/build-dotx", *arguments],
            cwd=self.root,
            env=env,
            capture_output=True,
            check=False,
        )

    def test_writes_the_template_to_stdout(self):
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(
            "word/glossary/styles.xml",
            zipfile.ZipFile(io.BytesIO(result.stdout)).namelist(),
        )

    def test_refuses_an_argument(self):
        result = self.run_runner("out.dotx")
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr),
            (2, b"", b"Usage: build-dotx > FILE\n"),
        )
