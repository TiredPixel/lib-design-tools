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

    def test_a_colour_mix_is_composited_over_the_page(self):
        self.write(
            "src/word__document.xml",
            f'<w:document {W}><w:color w:val="{{--mixed}}"/></w:document>',
        )
        expected = dotx.design_colour.composite("#112233", 50, "#FFFFFF")[1:]
        archive = self.built()
        self.assertIn(f'w:val="{expected}"', self.part(archive, "word/document.xml"))

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
