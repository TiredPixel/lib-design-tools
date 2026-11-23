"""tools/design_fonts.py, on fonts directories built for each case."""

import hashlib
import pathlib
import struct
import tempfile
import unittest

import design_fonts
from design_fonts import SUMS, WOFF2_KNOWN_TAGS, WOFF2_TRANSFORMED, base128

BOTH = ("woff2", "truetype")
TRUETYPE = b"\x00\x01\x00\x00"


class ProblemsTest(unittest.TestCase):
    """problems() on a directory built for each case."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = pathlib.Path(temporary.name)
        (self.directory / "README.md").write_text("# Fonts\n")
        (self.directory / "families.toml").write_text("[[family]]\n")
        files = {
            "A-LICENCE.txt": b"licence\n",
            "A-Regular.ttf": b"\x00\x01\x00\x00regular",
        }
        for name, content in files.items():
            (self.directory / name).write_bytes(content)
        (self.directory / SUMS).write_text(
            "".join(
                f"{hashlib.sha256(content).hexdigest()}  {name}\n"
                for name, content in files.items()
            )
        )

    def test_pass(self):
        self.assertEqual(design_fonts.problems(self.directory), [])

    def test_altered_file(self):
        (self.directory / "A-Regular.ttf").write_bytes(b"\x00\x01\x00\x00altered")
        self.assertEqual(
            design_fonts.problems(self.directory), ["A-Regular.ttf: FAILED"]
        )

    def test_listed_file_missing(self):
        (self.directory / "A-LICENCE.txt").unlink()
        self.assertEqual(
            design_fonts.problems(self.directory),
            ["A-LICENCE.txt: FAILED open or read"],
        )

    def test_unlisted_file(self):
        (self.directory / "A-Bold.woff2").write_bytes(b"wOF2bold")
        self.assertEqual(
            design_fonts.problems(self.directory), ["A-Bold.woff2: not in SHA256SUMS"]
        )

    def test_malformed_list(self):
        with (self.directory / SUMS).open("a") as sums:
            sums.write("not a checksum line\n")
        found = design_fonts.problems(self.directory)
        self.assertEqual(len(found), 1)
        self.assertIn("improperly formatted", found[0])


def build_ttf(family, weight, italic, revision, glyphs=10, dsig=False, names=None):
    """A TrueType file holding only what ttf_face reads.

    It has a glyf and a loca for GLYPHS empty glyphs, and optionally a DSIG; NAMES,
    name ID to text, replace the one family record.
    """
    names = names or {1: family}
    records = strings = b""
    for name_id, text in names.items():
        encoded = text.encode("utf-16-be")
        records += struct.pack(">6H", 3, 1, 0x409, name_id, len(encoded), len(strings))
        strings += encoded
    tables = {
        b"OS/2": struct.pack(">4xH56xH", weight, 1 if italic else 0),
        b"glyf": b"\x00" * 4 * glyphs,
        b"head": struct.pack(">4xI46x", revision),
        b"loca": b"\x00" * 2 * (glyphs + 1),
        b"name": struct.pack(">3H", 0, len(names), 6 + len(records))
        + records
        + strings,
    }
    if dsig:
        tables[b"DSIG"] = b"\x00" * 8
    header = struct.pack(">4sH6x", TRUETYPE, len(tables))
    offset = 12 + 16 * len(tables)
    entries = data = b""
    for tag, table in tables.items():
        entries += struct.pack(">4sIII", tag, 0, offset + len(data), len(table))
        data += table
    return header + entries + data


def encode_base128(value):
    groups = [value & 0x7F]
    while value := value >> 7:
        groups.insert(0, value & 0x7F | 0x80)
    return bytes(groups)


def build_woff2(lengths, revision, flavour=TRUETYPE):
    """A WOFF2 header and table directory for tables of LENGTHS, by tag.

    A known tag is written by its index, any other in full, and glyf and loca are
    transformed.
    """
    header = struct.pack(
        ">4s4sIHH8xHH20x",
        b"wOF2",
        flavour,
        0,
        len(lengths),
        0,
        *divmod(revision, 1 << 16),
    )
    directory = b""
    for tag, length in lengths.items():
        if tag in WOFF2_KNOWN_TAGS:
            directory += bytes([WOFF2_KNOWN_TAGS.index(tag)])
        else:
            directory += b"\x3f" + tag
        directory += encode_base128(length)
        if tag in WOFF2_TRANSFORMED:
            directory += encode_base128(length // 2)
    return header + directory


def woff2_of(ttf, lengths=None, revision=None, flavour=TRUETYPE):
    """build_woff2 for the font a TrueType file holds.

    Its table lengths are updated from LENGTHS, where a None drops the table, and its
    revision replaced by REVISION.
    """
    face = design_fonts.ttf_face(ttf)
    lengths = {**face["lengths"], **(lengths or {})}
    return build_woff2(
        {tag: length for tag, length in lengths.items() if length is not None},
        face["revision"] if revision is None else revision,
        flavour,
    )


FACES = """
@font-face {
  font-family: "A";
  font-style: normal;
  font-weight: 400;
  src:
    url("fonts/A-Regular.woff2") format("woff2"),
    url("fonts/A-Regular.ttf") format("truetype");
}
@font-face {
  font-family: "A";
  font-style: normal;
  font-weight: 700;
  src:
    url("fonts/A-Bold.woff2") format("woff2"),
    url("fonts/A-Bold.ttf") format("truetype");
}
"""


class FaceProblemsTest(unittest.TestCase):
    """face_problems() on a stylesheet beside a fonts directory built for each case."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.directory = self.root / "fonts"
        self.directory.mkdir()
        self.write_cut("A-Regular", build_ttf("A", 400, False, 0x10189))
        self.write_cut("A-Bold", build_ttf("A", 700, False, 0x10189, 12))

    def write_cut(self, stem, ttf, **changes):
        (self.directory / f"{stem}.ttf").write_bytes(ttf)
        (self.directory / f"{stem}.woff2").write_bytes(woff2_of(ttf, **changes))

    def problems(self, stylesheet=FACES, formats=BOTH, name="fonts.css"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stylesheet)
        return design_fonts.face_problems(path, self.directory, formats)

    def test_pass(self):
        self.assertEqual(self.problems(), [])

    def test_a_last_descriptor_without_a_semicolon(self):
        stylesheet = FACES.replace("  font-weight: 400;\n", "", 1).replace(
            'A-Regular.ttf") format("truetype");\n',
            'A-Regular.ttf") format("truetype");\n  font-weight: 400\n',
            1,
        )
        self.assertNotEqual(stylesheet, FACES)
        self.assertEqual(self.problems(stylesheet), [])

    def test_urls_resolve_against_the_stylesheet(self):
        stylesheet = FACES.replace('url("fonts/', 'url("../fonts/')
        self.assertEqual(self.problems(stylesheet, name="tokens/fonts.css"), [])

    def test_truetype_alone_where_it_is_the_one_format(self):
        for path in self.directory.glob("*.woff2"):
            path.unlink()
        stylesheet = "\n".join(
            line for line in FACES.splitlines() if 'format("woff2")' not in line
        )
        self.assertEqual(self.problems(stylesheet, ("truetype",)), [])
        self.assertEqual(
            self.problems(stylesheet),
            [
                f"A 400 normal: not one each of {BOTH}",
                f"A 700 normal: not one each of {BOTH}",
            ],
        )

    def test_a_font_file_no_rule_names(self):
        self.write_cut("A-Light", build_ttf("A", 300, False, 0x10189))
        self.assertEqual(
            self.problems(),
            [
                "A-Light.ttf: named by no @font-face",
                "A-Light.woff2: named by no @font-face",
            ],
        )

    def test_no_rule(self):
        self.assertEqual(self.problems("/* none */"), ["no @font-face rule"])

    def test_weight(self):
        self.assertEqual(
            self.problems(FACES.replace("700", "600")),
            ["A-Bold.ttf: weight 700, not 600"],
        )

    def test_rule_says_italic(self):
        self.assertEqual(
            self.problems(FACES.replace("normal", "italic", 1)),
            ["A-Regular.ttf: upright, not font-style italic"],
        )

    def test_file_is_italic(self):
        self.write_cut("A-Regular", build_ttf("A", 400, True, 0x10189))
        self.assertEqual(
            self.problems(), ["A-Regular.ttf: italic, not font-style normal"]
        )

    def test_family(self):
        self.write_cut("A-Bold", build_ttf("B", 700, False, 0x10189))
        self.assertEqual(self.problems(), ["A-Bold.ttf: family B, not A"])

    def test_family_prefers_the_typographic_family(self):
        self.write_cut(
            "A-Bold", build_ttf("A", 700, False, 0x10189, names={1: "A Bold", 16: "A"})
        )
        self.assertEqual(self.problems(), [])
        self.write_cut(
            "A-Bold", build_ttf("A", 700, False, 0x10189, names={1: "A Bold"})
        )
        self.assertEqual(self.problems(), ["A-Bold.ttf: family A Bold, not A"])

    def test_woff2_of_another_cut(self):
        self.assertEqual(
            self.problems(FACES.replace("A-Regular.woff2", "A-Bold.woff2")),
            [
                "A-Bold.woff2: not the same cut as A-Regular.ttf",
                "A-Regular.woff2: named by no @font-face",
            ],
        )

    def test_woff2_of_another_revision(self):
        self.write_cut("A-Bold", build_ttf("A", 700, False, 0x10189), revision=0x10190)
        self.assertEqual(
            self.problems(), ["A-Bold.woff2: not the same cut as A-Bold.ttf"]
        )

    def test_woff2_with_another_table_length(self):
        ttf = build_ttf("A", 700, False, 0x10189)
        self.write_cut("A-Bold", ttf, lengths={b"name": 99})
        self.assertEqual(
            self.problems(), ["A-Bold.woff2: not the same cut as A-Bold.ttf"]
        )

    def test_woff2_of_another_flavour(self):
        ttf = build_ttf("A", 700, False, 0x10189)
        self.write_cut("A-Bold", ttf, flavour=b"OTTO")
        self.assertEqual(
            self.problems(), ["A-Bold.woff2: not the same cut as A-Bold.ttf"]
        )

    def test_woff2_may_drop_dsig_and_resize_glyf(self):
        ttf = build_ttf("A", 700, False, 0x10189, dsig=True)
        self.write_cut("A-Bold", ttf, lengths={b"DSIG": None, b"glyf": 14})
        self.assertEqual(self.problems(), [])

    def test_woff2_keeps_an_unknown_tag(self):
        ttf = build_ttf("A", 700, False, 0x10189, dsig=True)
        self.write_cut("A-Bold", ttf)
        self.assertEqual(self.problems(), [])
        self.write_cut("A-Bold", ttf, lengths={b"DSIG": 9})
        self.assertEqual(
            self.problems(), ["A-Bold.woff2: not the same cut as A-Bold.ttf"]
        )

    def test_not_a_woff2(self):
        (self.directory / "A-Bold.woff2").write_bytes(b"wOFF" + b"\x00" * 44)
        with self.assertRaisesRegex(ValueError, "not a WOFF2 file"):
            self.problems()

    def test_base128(self):
        self.assertEqual(base128(encode_base128(161930), 0), (161930, 3))
        self.assertEqual(base128(b"\x3f", 0), (63, 1))
        with self.assertRaisesRegex(ValueError, "leading zero"):
            base128(b"\x80\x01", 0)
        with self.assertRaisesRegex(ValueError, "longer than five bytes"):
            base128(b"\x81\x81\x81\x81\x81\x01", 0)
