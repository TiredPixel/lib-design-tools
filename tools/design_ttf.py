"""The tables and names of a TrueType font, read from its bytes.

Names are read from the Windows Unicode BMP US English records, platform 3, encoding 1
and language 0x409, the ones Word reads.
"""

import struct

FAMILY = 1
SUBFAMILY = 2
TYPOGRAPHIC_FAMILY = 16

_WINDOWS_ENGLISH = (3, 1, 0x409)


def tables(data: bytes) -> tuple[bytes, dict[bytes, bytes]]:
    """The font's sfnt version and each table's bytes, by tag."""
    version, count = struct.unpack_from(">4sH", data)
    found = {}
    for index in range(count):
        tag, _, offset, length = struct.unpack_from(">4sIII", data, 12 + 16 * index)
        found[tag] = data[offset : offset + length]
    return version, found


def names(data: bytes) -> dict[int, str]:
    """The font's Windows Unicode BMP US English name records, by name ID."""
    table = tables(data)[1][b"name"]
    _, count, strings = struct.unpack_from(">HHH", table)
    found = {}
    for index in range(count):
        *record, name_id, length, offset = struct.unpack_from(
            ">6H", table, 6 + 12 * index
        )
        if tuple(record) == _WINDOWS_ENGLISH:
            start = strings + offset
            found[name_id] = table[start : start + length].decode("utf-16-be")
    return found
