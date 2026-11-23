"""tools/ooxml-check, on packages built in each test."""

import contextlib
import io
import pathlib
import re
import struct
import zipfile

import temp_tree
import tools_loader

ooxml_check = tools_loader.load("ooxml-check")

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
RELS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml"
STYLES = (
    f"<w:styles {W}>"
    '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="A" w:hAnsi="A" w:cs="A"/>'
    "</w:rPr></w:rPrDefault></w:docDefaults>"
    '<w:style w:type="paragraph" w:styleId="Normal"/>'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:basedOn w:val="Normal"/>'
    '<w:next w:val="Normal"/><w:link w:val="Heading1Char"/>'
    "<w:rPr><w:b/></w:rPr></w:style>"
    '<w:style w:type="character" w:styleId="Heading1Char"><w:link w:val="Heading1"/>'
    "<w:rPr><w:b/></w:rPr></w:style>"
    "</w:styles>"
)
NUMBERING = (
    f'<w:numbering {W}><w:abstractNum w:abstractNumId="1"/>'
    '<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num></w:numbering>'
)
FONTS = f'<w:fonts {W}><w:font w:name="A"/></w:fonts>'
DOCUMENT = (
    f'<w:document {W} {R}><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/>'
    '<w:numPr><w:numId w:val="1"/></w:numPr></w:pPr>'
    '<w:hyperlink r:id="rId1"/></w:p></w:body></w:document>'
)
DOCUMENT_RELS = (
    f"<Relationships {RELS}>"
    '<Relationship Id="rId1" Target="styles.xml" Type="t"/>'
    '<Relationship Id="rId2" Target="https://example.org/" TargetMode="External" '
    'Type="t"/></Relationships>'
)
TYPES = "[Content_Types].xml"
PARTS = {
    "[Content_Types].xml": (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
        'package.relationships+xml"/>'
        f'<Default Extension="xml" ContentType="{ooxml_check.GENERIC}"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        + "".join(
            f'<Override PartName="/{part}" ContentType="{MAIN}.{kind}+xml"/>'
            for part, kind in (
                ("word/document.xml", "document.main"),
                ("word/styles.xml", "styles"),
                ("word/numbering.xml", "numbering"),
                ("word/fontTable.xml", "fontTable"),
                ("word/glossary/document.xml", "document.glossary"),
                ("word/glossary/styles.xml", "styles"),
                ("word/glossary/numbering.xml", "numbering"),
                ("word/glossary/fontTable.xml", "fontTable"),
            )
        )
        + "</Types>"
    ),
    "_rels/.rels": (
        f'<Relationships {RELS}><Relationship Id="rId1" '
        'Target="word/document.xml" Type="t"/></Relationships>'
    ),
    "word/document.xml": DOCUMENT,
    "word/_rels/document.xml.rels": DOCUMENT_RELS,
    "word/styles.xml": STYLES,
    "word/numbering.xml": NUMBERING,
    "word/fontTable.xml": FONTS,
    "word/media/image1.png": "png",
    "word/glossary/document.xml": f"<w:glossaryDocument {W}/>",
    "word/glossary/styles.xml": STYLES,
    "word/glossary/numbering.xml": NUMBERING,
    "word/glossary/fontTable.xml": FONTS,
}


def font(family, subfamily):
    """A TrueType file holding only a name table naming FAMILY and SUBFAMILY."""
    records = strings = b""
    for name_id, text in ((1, family), (2, subfamily)):
        encoded = text.encode("utf-16-be")
        records += struct.pack(">6H", 3, 1, 0x409, name_id, len(encoded), len(strings))
        strings += encoded
    name = struct.pack(">3H", 0, 2, 6 + len(records)) + records + strings
    return (
        struct.pack(">4sH6x", b"\x00\x01\x00\x00", 1)
        + struct.pack(">4sIII", b"name", 0, 28, len(name))
        + name
    )


class OoxmlCheckTest(temp_tree.TempTreeTestCase):
    def package(self, **changes):
        """The template, each part in CHANGES set to its text, or left out for None.

        A part left out loses its Override too, as a template without it has none.
        """
        parts = {k: v for k, v in {**PARTS, **changes}.items() if v is not None}
        for name in (k for k, v in changes.items() if v is None and TYPES in parts):
            parts[TYPES] = re.sub(
                f'<Override PartName="/{re.escape(name)}"[^>]*/>', "", parts[TYPES]
            )
        path = self.root / "a.dotx"
        with zipfile.ZipFile(path, "w") as archive:
            for name, text in parts.items():
                archive.writestr(name, text)
        return path

    def fonts(self):
        """A directory of the regular and bold faces of A, as STYLES needs."""
        fonts = self.root / "fonts"
        fonts.mkdir()
        for subfamily in ("Regular", "Bold"):
            (fonts / f"A-{subfamily}.ttf").write_bytes(font("A", subfamily))
        return fonts

    def check(self, *arguments):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = ooxml_check.main([str(a) for a in arguments])
        return status, stderr.getvalue().splitlines()

    def assertProblems(self, path, *problems, arguments=()):
        status, lines = self.check(*arguments, path)
        self.assertEqual(lines[:-1], [f"{path}: {p}" for p in problems])
        self.assertEqual(status, 1 if problems else 0)
        self.assertEqual(
            lines[-1],
            f"ooxml-check: checked 1 template(s): {len(problems)} problem(s)",
        )

    def test_a_consistent_template_passes(self):
        self.assertProblems(self.package())

    def test_a_template_that_is_no_regular_file(self):
        status, lines = self.check(pathlib.Path("/dev/zero"))
        self.assertEqual(status, 1)
        self.assertEqual(lines[0], "ooxml-check: /dev/zero: not a regular file")

    def test_a_directory_entry_is_no_part(self):
        path = self.package()
        with zipfile.ZipFile(path, "a") as archive:
            for directory in ("word/", "word/media/", "word/glossary/"):
                archive.writestr(directory, "")
        self.assertProblems(path)

    def test_a_template_without_a_glossary_passes(self):
        glossary = {part: None for part in PARTS if part.startswith("word/glossary/")}
        self.assertProblems(self.package(**glossary))

    def test_a_part_without_a_content_type(self):
        self.assertProblems(
            self.package(**{"word/media/x.svg": "svg"}),
            "word/media/x.svg: has no content type",
        )

    def test_an_xml_part_only_the_generic_default_types(self):
        self.assertProblems(
            self.package(**{"word/footer1.xml": f"<w:ftr {W}/>"}),
            "word/footer1.xml: has no Override, only the application/xml Default",
        )

    def test_a_relationship_to_no_part(self):
        rels = DOCUMENT_RELS.replace('Target="styles.xml"', 'Target="gone.xml"')
        self.assertProblems(
            self.package(**{"word/_rels/document.xml.rels": rels}),
            "word/_rels/document.xml.rels: rId1 targets gone.xml, which is no part",
        )

    def test_a_relationship_id_the_rels_lacks(self):
        document = DOCUMENT.replace('r:id="rId1"', 'r:id="rId9"')
        self.assertProblems(
            self.package(**{"word/document.xml": document}),
            "word/document.xml: names rId9, which word/_rels/document.xml.rels does "
            "not hold",
        )

    def test_a_style_reference_of_the_wrong_type_or_missing(self):
        document = DOCUMENT.replace('"Heading1"', '"Heading1Char"')
        self.assertProblems(
            self.package(**{"word/document.xml": document}),
            "word/document.xml: pStyle: Heading1Char is no paragraph style",
        )
        styles = STYLES.replace('<w:next w:val="Normal"/>', '<w:next w:val="Gone"/>')
        self.assertProblems(
            self.package(**{"word/glossary/styles.xml": styles}),
            "word/glossary/styles.xml: Heading1: next: Gone is no paragraph style",
        )

    def test_a_list_not_defined(self):
        document = DOCUMENT.replace('<w:numId w:val="1"/>', '<w:numId w:val="2"/>')
        numbering = NUMBERING.replace(
            '<w:abstractNumId w:val="1"/>', '<w:abstractNumId w:val="3"/>'
        )
        self.assertProblems(
            self.package(
                **{"word/document.xml": document, "word/numbering.xml": numbering}
            ),
            "word/document.xml: numId 2 is not defined",
            "word/numbering.xml: num 1: abstractNum 3 is not defined",
        )

    def test_a_template_without_lists_needs_no_numbering(self):
        document = DOCUMENT.replace('<w:numPr><w:numId w:val="1"/></w:numPr>', "")
        self.assertProblems(
            self.package(**{"word/document.xml": document, "word/numbering.xml": None})
        )

    def test_a_reference_into_an_absent_part(self):
        self.assertProblems(
            self.package(**{"word/numbering.xml": None, "word/fontTable.xml": None}),
            "word/document.xml: numId 1 is not defined",
            "word/styles.xml: names font A, which fontTable.xml does not declare",
        )

    def test_a_font_not_declared(self):
        styles = STYLES.replace('w:cs="A"', 'w:cs="B"')
        self.assertProblems(
            self.package(**{"word/styles.xml": styles}),
            "word/styles.xml: names font B, which fontTable.xml does not declare",
        )

    def test_pandoc_style_ids(self):
        path = self.package()
        status, lines = self.check("--pandoc", path)
        self.assertEqual(status, 1)
        self.assertIn(
            f"{path}: word/styles.xml: no paragraph style Abstract, which pandoc reads",
            lines,
        )

    def test_a_face_turned_off_is_not_looked_for(self):
        fonts = self.root / "fonts"
        fonts.mkdir()
        (fonts / "A-Regular.ttf").write_bytes(font("A", "Regular"))
        for value in ("0", "false", "off"):
            with self.subTest(value=value):
                styles = STYLES.replace("<w:b/>", f'<w:b w:val="{value}"/>')
                self.assertProblems(
                    self.package(**{"word/styles.xml": styles}),
                    arguments=("--fonts", fonts),
                )

    def test_the_nearest_toggle_along_a_chain_wins(self):
        def style(style_id, based=None, toggles="<w:b/><w:i/><w:bCs/><w:iCs/>"):
            based_on = f'<w:basedOn w:val="{based}"/>' if based else ""
            return (
                f'<w:style w:type="paragraph" w:styleId="{style_id}">{based_on}'
                f"<w:rPr>{toggles}</w:rPr></w:style>"
            )

        def package(*chain):
            head = STYLES[: STYLES.index("<w:style ")]
            return self.package(
                **{
                    "word/document.xml": f"<w:document {W}/>",
                    "word/styles.xml": f"{head}{''.join(chain)}</w:styles>",
                    "word/glossary/styles.xml": None,
                }
            )

        # Only bold italic is shipped, so a repeat that cancelled would be reported.
        bold_italic = self.root / "bold-italic"
        bold_italic.mkdir()
        (bold_italic / "A.ttf").write_bytes(font("A", "Bold Italic"))
        self.assertProblems(
            package(style("One"), style("Two", "One"), style("Three", "Two")),
            arguments=("--fonts", bold_italic),
        )
        # Only regular is shipped, so the parent alone is reported, and a child turning
        # the toggles off is not.
        regular = self.root / "regular"
        regular.mkdir()
        (regular / "A.ttf").write_bytes(font("A", "Regular"))
        off = '<w:b w:val="0"/><w:i w:val="0"/><w:bCs w:val="0"/><w:iCs w:val="0"/>'
        self.assertProblems(
            package(style("One"), style("Off", "One", off)),
            *(
                f"word/styles.xml: One: {script} face ('A', True, True) is not shipped"
                for script in ("ascii", "hAnsi", "cs")
            ),
            arguments=("--fonts", regular),
        )

    def test_an_override_for_no_part_or_retyping_relationships(self):
        types = PARTS["[Content_Types].xml"].replace(
            "</Types>",
            '<Override PartName="/word/gone.xml" ContentType="application/xml"/>'
            '<Override PartName="/_rels/.rels" ContentType="application/xml"/></Types>',
        )
        self.assertProblems(
            self.package(**{"[Content_Types].xml": types}),
            "[Content_Types].xml: Override names word/gone.xml, which is no part",
            "[Content_Types].xml: Override types _rels/.rels as no relationships part",
        )

    def test_an_override_without_a_content_type(self):
        types = PARTS["[Content_Types].xml"].replace(
            f'<Override PartName="/word/document.xml" ContentType="{MAIN}.document.'
            'main+xml"/>',
            '<Override PartName="/word/document.xml"/>',
        )
        self.assertNotEqual(types, PARTS["[Content_Types].xml"])
        self.assertProblems(
            self.package(**{"[Content_Types].xml": types}),
            "[Content_Types].xml: Override for /word/document.xml has no ContentType",
            f"word/document.xml: has no Override, only the {ooxml_check.GENERIC} "
            "Default",
        )

    def test_each_style_face_is_shipped(self):
        fonts = self.root / "fonts"
        fonts.mkdir()
        (fonts / "A-Regular.ttf").write_bytes(font("A", "Regular"))
        path = self.package()
        self.assertProblems(
            path,
            *(
                f"word/styles.xml: {style}: {script} face ('A', True, False) is not "
                "shipped"
                for style in ("Heading1", "Heading1Char")
                for script in ("ascii", "hAnsi")
            ),
            arguments=("--fonts", fonts),
        )
        (fonts / "A-Bold.ttf").write_bytes(font("A", "Bold"))
        self.assertProblems(path, arguments=("--fonts", fonts))

    def test_a_based_on_naming_no_style_is_reported_once(self):
        styles = STYLES.replace(
            '<w:basedOn w:val="Normal"/>', '<w:basedOn w:val="Gone"/>'
        )
        self.assertProblems(
            self.package(**{"word/styles.xml": styles}),
            "word/styles.xml: Heading1: basedOn: Gone is no paragraph style",
            arguments=("--fonts", self.fonts()),
        )

    def test_a_based_on_cycle(self):
        fonts = self.fonts()
        for styles, problems in (
            (
                STYLES.replace(
                    '<w:basedOn w:val="Normal"/>', '<w:basedOn w:val="Heading1"/>'
                ),
                ["Heading1"],
            ),
            (
                STYLES.replace(
                    'w:styleId="Normal"/>',
                    'w:styleId="Normal"><w:basedOn w:val="Heading1"/></w:style>',
                ),
                ["Normal", "Heading1"],
            ),
        ):
            with self.subTest(problems=problems):
                self.assertProblems(
                    self.package(**{"word/styles.xml": styles}),
                    *(
                        f"word/styles.xml: {name}: basedOn: loops back to {name}"
                        for name in problems
                    ),
                    arguments=("--fonts", fonts),
                )

    def test_a_reference_without_a_value(self):
        document = DOCUMENT.replace('<w:pStyle w:val="Heading1"/>', "<w:pStyle/>")
        self.assertProblems(
            self.package(**{"word/document.xml": document}),
            "word/document.xml: pStyle: has no w:val",
        )
        styles = STYLES.replace('<w:basedOn w:val="Normal"/>', "<w:basedOn/>")
        self.assertProblems(
            self.package(**{"word/styles.xml": styles}),
            "word/styles.xml: Heading1: basedOn: has no w:val",
            arguments=("--fonts", self.fonts()),
        )

    def test_a_style_without_a_type_is_a_paragraph_style(self):
        styles = STYLES.replace(
            'w:type="paragraph" w:styleId="Normal"', 'w:styleId="Normal"'
        )
        self.assertProblems(self.package(**{"word/styles.xml": styles}))

    def test_a_table_condition_without_a_type(self):
        # Beside a typed one, so the face check passes over the untyped one rather than
        # sorting it with the types.
        styles = STYLES.replace(
            "</w:styles>",
            '<w:style w:type="table" w:styleId="Grid"><w:tblStylePr w:type="firstRow">'
            "<w:rPr><w:b/></w:rPr></w:tblStylePr><w:tblStylePr><w:rPr><w:i/>"
            "</w:rPr></w:tblStylePr></w:style></w:styles>",
        )
        self.assertProblems(
            self.package(**{"word/styles.xml": styles}),
            "word/styles.xml: Grid: tblStylePr has no w:type",
            arguments=("--fonts", self.fonts()),
        )

    def test_a_list_without_an_abstract_or_a_value(self):
        for numbering, problem in (
            (
                NUMBERING.replace('<w:abstractNumId w:val="1"/>', ""),
                "num 1: has no abstractNumId",
            ),
            (
                NUMBERING.replace('<w:abstractNumId w:val="1"/>', "<w:abstractNumId/>"),
                "num 1: abstractNumId has no w:val",
            ),
        ):
            with self.subTest(problem=problem):
                self.assertProblems(
                    self.package(**{"word/numbering.xml": numbering}),
                    f"word/numbering.xml: {problem}",
                )
        document = DOCUMENT.replace('<w:numId w:val="1"/>', "<w:numId/>")
        self.assertProblems(
            self.package(**{"word/document.xml": document}),
            "word/document.xml: numId has no w:val",
        )

    def test_content_types_missing_or_without_their_attributes(self):
        self.assertProblems(
            self.package(**{"[Content_Types].xml": None}),
            "[Content_Types].xml: is missing, so no part has a content type",
        )
        types = PARTS["[Content_Types].xml"].replace(
            "</Types>",
            '<Default ContentType="image/gif"/><Override ContentType="x"/></Types>',
        )
        self.assertProblems(
            self.package(**{"[Content_Types].xml": types}),
            "[Content_Types].xml: Default has no Extension",
            "[Content_Types].xml: Override has no PartName",
        )

    def test_a_relationship_without_a_target_or_outside_the_package(self):
        rels = DOCUMENT_RELS.replace('Target="styles.xml" ', "")
        self.assertProblems(
            self.package(**{"word/_rels/document.xml.rels": rels}),
            "word/_rels/document.xml.rels: Relationship has no Target",
        )
        rels = DOCUMENT_RELS.replace('Target="styles.xml"', 'Target="../../out.xml"')
        self.assertProblems(
            self.package(**{"word/_rels/document.xml.rels": rels}),
            "word/_rels/document.xml.rels: rId1 targets ../../out.xml, which is no "
            "part",
        )

    def test_a_relationship_without_an_id(self):
        rels = DOCUMENT_RELS.replace('Id="rId2" ', "")
        self.assertNotEqual(rels, DOCUMENT_RELS)
        self.assertProblems(
            self.package(**{"word/_rels/document.xml.rels": rels}),
            "word/_rels/document.xml.rels: Relationship has no Id",
        )

    def test_an_external_relationship_without_a_target(self):
        rels = DOCUMENT_RELS.replace('Target="https://example.org/" ', "")
        self.assertNotEqual(rels, DOCUMENT_RELS)
        self.assertProblems(
            self.package(**{"word/_rels/document.xml.rels": rels}),
            "word/_rels/document.xml.rels: Relationship has no Target",
        )

    def test_a_package_without_its_rels(self):
        self.assertProblems(
            self.package(**{"_rels/.rels": None}),
            "_rels/.rels: is missing, so the package names no document",
        )

    def test_a_list_definition_without_its_id(self):
        numbering = NUMBERING.replace(' w:abstractNumId="1"/>', "/>").replace(
            '<w:num w:numId="1">', "<w:num>"
        )
        self.assertProblems(
            self.package(**{"word/numbering.xml": numbering}),
            "word/document.xml: numId 1 is not defined",
            "word/numbering.xml: abstractNum has no w:abstractNumId",
            "word/numbering.xml: num has no w:numId",
        )

    def test_a_part_without_its_rels(self):
        self.assertProblems(
            self.package(**{"word/_rels/document.xml.rels": None}),
            "word/document.xml: names rId1, which word/_rels/document.xml.rels does "
            "not hold",
        )

    def test_a_template_that_is_not_a_zip_or_not_xml(self):
        good = self.package()
        bad = self.root / "b.dotx"
        gone = self.root / "gone.dotx"
        for data, message in (
            (b"not a zip", f"ooxml-check: {bad}: not a zip: File is not a zip file"),
            (None, f"ooxml-check: {gone}: No such file or directory"),
        ):
            with self.subTest(message=message):
                if data is not None:
                    bad.write_bytes(data)
                status, lines = self.check(bad if data else gone, good)
                self.assertEqual(
                    (status, lines),
                    (1, [message, "ooxml-check: checked 2 template(s): 1 problem(s)"]),
                )
        for text, error in (
            ("<w:styles", "unclosed token"),
            ('<?xml version="1.0" encoding="bogus"?><a/>', "unknown encoding"),
        ):
            with self.subTest(error=error):
                path = self.package(**{"word/styles.xml": text})
                status, lines = self.check(path)
                self.assertEqual(status, 1)
                self.assertEqual(len(lines), 2)
                self.assertTrue(
                    lines[0].startswith(
                        f"ooxml-check: {path}: word/styles.xml: not XML: {error}"
                    ),
                    lines[0],
                )

    def test_a_zip_whose_member_cannot_be_read(self):
        good = self.package()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("a.xml", "<a>" + "x" * 300 + "</a>")
        whole = buffer.getvalue()
        # The central directory's flags and compression method, and the member's data.
        central = whole.rindex(b"PK\x01\x02")
        start = 30 + len("a.xml")

        def patched(at, data):
            return whole[:at] + data + whole[at + len(data) :]

        bad = self.root / "b.dotx"
        for kind, data in (
            ("encrypted", patched(central + 8, struct.pack("<H", 1))),
            ("compressed by no method zipfile has", patched(central + 10, b"\x63\x00")),
            ("damaged", patched(start, b"\xff\xff\xff\xff")),
            ("truncated", whole[1:]),
        ):
            with self.subTest(kind=kind):
                bad.write_bytes(data)
                status, lines = self.check(bad, good)
                self.assertEqual(status, 1)
                self.assertEqual(
                    lines[1:], ["ooxml-check: checked 2 template(s): 1 problem(s)"]
                )
                self.assertTrue(
                    lines[0].startswith(f"ooxml-check: {bad}: not a zip: "), lines[0]
                )

    def test_a_fonts_directory_that_is_missing_or_no_directory(self):
        path = self.package(**{"word/media/x.svg": "svg"})
        gone = self.root / "gone"
        file = self.root / "file"
        file.write_bytes(b"")
        for fonts, error in (
            (gone, "No such file or directory"),
            (file, "Not a directory"),
        ):
            with self.subTest(error=error):
                status, lines = self.check("--fonts", fonts, path)
                self.assertEqual(
                    (status, lines),
                    (
                        1,
                        [
                            f"ooxml-check: {fonts}: {error}",
                            f"{path}: word/media/x.svg: has no content type",
                            "ooxml-check: checked 1 template(s): 2 problem(s)",
                        ],
                    ),
                )

    def test_a_font_that_is_not_truetype(self):
        fonts = self.fonts()
        (fonts / "B.ttf").write_bytes(b"junk")
        path = self.package()
        status, lines = self.check("--fonts", fonts, path)
        self.assertEqual(status, 1)
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(
            lines[0].startswith(f"ooxml-check: {fonts / 'B.ttf'}: not a TrueType font"),
            lines[0],
        )
