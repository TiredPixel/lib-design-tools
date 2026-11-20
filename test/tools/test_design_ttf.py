"""tools/design_ttf.py, on fonts built in the test."""

import struct
import unittest

import design_ttf


def font(table_bytes):
    """A TrueType file holding TABLE_BYTES, a tag to its bytes each."""
    header = struct.pack(">4sH6x", b"\x00\x01\x00\x00", len(table_bytes))
    offset = 12 + 16 * len(table_bytes)
    records = data = b""
    for tag, table in table_bytes.items():
        records += struct.pack(">4sIII", tag, 0, offset + len(data), len(table))
        data += table
    return header + records + data


def name_table(*records):
    """A name table of RECORDS, each (platform, encoding, language, name ID, text)."""
    entries = strings = b""
    for platform, encoding, language, name_id, text in records:
        encoded = text.encode("utf-16-be")
        entries += struct.pack(
            ">6H", platform, encoding, language, name_id, len(encoded), len(strings)
        )
        strings += encoded
    return struct.pack(">3H", 0, len(records), 6 + len(entries)) + entries + strings


class TablesTest(unittest.TestCase):
    def test_gives_the_version_and_each_table_by_tag(self):
        data = font({b"head": b"\x01\x02", b"OS/2": b"\x03"})
        self.assertEqual(
            design_ttf.tables(data),
            (b"\x00\x01\x00\x00", {b"head": b"\x01\x02", b"OS/2": b"\x03"}),
        )


class NamesTest(unittest.TestCase):
    def test_reads_only_windows_unicode_bmp_us_english_records(self):
        data = font(
            {
                b"name": name_table(
                    (3, 1, 0x409, design_ttf.FAMILY, "A"),
                    (3, 1, 0x409, design_ttf.SUBFAMILY, "Bold Italic"),
                    (3, 1, 0x405, design_ttf.FAMILY, "Czech"),
                    (3, 10, 0x409, design_ttf.FAMILY, "Full repertoire"),
                    (1, 0, 0, design_ttf.FAMILY, "Mac"),
                )
            }
        )
        self.assertEqual(design_ttf.names(data), {1: "A", 2: "Bold Italic"})

    def test_keeps_the_typographic_family_beside_the_family(self):
        data = font(
            {
                b"name": name_table(
                    (3, 1, 0x409, design_ttf.FAMILY, "A Bold"),
                    (3, 1, 0x409, design_ttf.TYPOGRAPHIC_FAMILY, "A"),
                    (3, 1, 0x405, design_ttf.TYPOGRAPHIC_FAMILY, "B"),
                )
            }
        )
        self.assertEqual(design_ttf.names(data), {1: "A Bold", 16: "A"})
