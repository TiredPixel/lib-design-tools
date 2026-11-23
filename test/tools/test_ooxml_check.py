"""tools/ooxml-check, on packages built in each test."""

import contextlib
import io
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
        """The template, each part in CHANGES set to its text, or left out for None."""
        parts = {k: v for k, v in {**PARTS, **changes}.items() if v is not None}
        path = self.root / "a.dotx"
        with zipfile.ZipFile(path, "w") as archive:
            for name, text in parts.items():
                archive.writestr(name, text)
        return path

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
