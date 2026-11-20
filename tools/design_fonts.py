"""Checks of a fonts directory and the @font-face rules that name its files.

``problems`` checks the directory against its SHA256SUMS, which lists each font and
licence file with the checksum of its publisher's copy. ``face_problems`` checks each
@font-face rule in a stylesheet against the files it names, and that every font file in
the directory is named by one.
"""

import re
import struct
import subprocess

import design_ttf

SUMS = "SHA256SUMS"
UNLISTED_OK = frozenset({"README.md", "RELEASES", SUMS, "families.toml"})
FONT_SUFFIXES = (".ttf", ".woff2")

COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
FACE_RULE = re.compile(r"@font-face\s*\{([^}]*)\}")
FACE_DESCRIPTOR = re.compile(r"(font-family|font-style|font-weight)\s*:\s*([^;]+);")
FACE_URL = re.compile(r"""url\(["']([^"']+)["']\)\s*format\(["']([^"']+)["']\)""")
# The tags a WOFF2 table directory names by index: the spec's Known Table Tags, in
# order (W3C, WOFF File Format 2.0, §5.1).
WOFF2_KNOWN_TAGS = tuple(
    tag.ljust(4).encode()
    for tag in [
        "cmap",
        "head",
        "hhea",
        "hmtx",
        "maxp",
        "name",
        "OS/2",
        "post",
        "cvt",
        "fpgm",
        "glyf",
        "loca",
        "prep",
        "CFF",
        "VORG",
        "EBDT",
        "EBLC",
        "gasp",
        "hdmx",
        "kern",
        "LTSH",
        "PCLT",
        "VDMX",
        "vhea",
        "vmtx",
        "BASE",
        "GDEF",
        "GPOS",
        "GSUB",
        "EBSC",
        "JSTF",
        "MATH",
        "CBDT",
        "CBLC",
        "COLR",
        "CPAL",
        "SVG",
        "sbix",
        "acnt",
        "avar",
        "bdat",
        "bloc",
        "bsln",
        "cvar",
        "fdsc",
        "feat",
        "fmtx",
        "fvar",
        "gvar",
        "hsty",
        "just",
        "lcar",
        "mort",
        "morx",
        "opbd",
        "prop",
        "trak",
        "Zapf",
        "Silf",
        "Glat",
        "Gloc",
        "Feat",
        "Sill",
    ]
)
# The tables a WOFF2 stores transformed by default, its other tables untransformed.
WOFF2_TRANSFORMED = (b"glyf", b"loca")
# A WOFF2 encoder may drop DSIG, whose signature no longer holds once the font is
# re-encoded.
WOFF2_DROPPED = b"DSIG"
# A transformed glyf's length once decompressed depends on how the encoder pads each
# glyph, so it need not equal the original's.
WOFF2_RESIZED = b"glyf"


def ttf_face(data):
    """What a TrueType file holds.

    That is its typographic family, else its family, its weight class, whether it is
    italic, its sfnt version, its head.fontRevision and each table's length.
    """
    version, tables = design_ttf.tables(data)
    names = design_ttf.names(data)
    weight = struct.unpack_from(">H", tables[b"OS/2"], 4)[0]
    selection = struct.unpack_from(">H", tables[b"OS/2"], 62)[0]
    return {
        "family": names.get(
            design_ttf.TYPOGRAPHIC_FAMILY, names.get(design_ttf.FAMILY)
        ),
        "weight": weight,
        "italic": bool(selection & 1),
        "flavour": version,
        "revision": struct.unpack_from(">I", tables[b"head"], 4)[0],
        "lengths": {tag: len(table) for tag, table in tables.items()},
    }


def base128(data, offset):
    """A WOFF2 UIntBase128 at OFFSET in DATA, and the offset after it."""
    value = 0
    for i in range(5):
        byte = data[offset + i]
        if i == 0 and byte == 0x80:
            raise ValueError("UIntBase128 with a leading zero")
        value = value << 7 | byte & 0x7F
        if not byte & 0x80:
            return value, offset + i + 1
    raise ValueError("UIntBase128 longer than five bytes")


def woff2_face(data):
    """What a WOFF2 file's header and table directory say of the font it compresses.

    That is its sfnt version, its font revision and each table's length once
    decompressed.
    """
    signature, flavour, _, count = struct.unpack_from(">4s4sIH", data)
    if signature != b"wOF2":
        raise ValueError("not a WOFF2 file")
    major, minor = struct.unpack_from(">HH", data, 24)
    lengths = {}
    offset = 48
    for _ in range(count):
        flags = data[offset]
        offset += 1
        if flags & 0x3F == 0x3F:
            tag = data[offset : offset + 4]
            offset += 4
        else:
            tag = WOFF2_KNOWN_TAGS[flags & 0x3F]
        lengths[tag], offset = base128(data, offset)
        transform = flags >> 6
        if (transform == 0) == (tag in WOFF2_TRANSFORMED):
            _, offset = base128(data, offset)
    return {"flavour": flavour, "revision": major << 16 | minor, "lengths": lengths}


def same_cut(ttf, woff2):
    """Whether a WOFF2 compresses the font a TrueType file holds.

    It is told by what the WOFF2's header and directory say, without decompressing it.
    """
    lengths = dict(ttf["lengths"])
    if WOFF2_DROPPED not in woff2["lengths"]:
        lengths.pop(WOFF2_DROPPED, None)
    compressed = dict(woff2["lengths"])
    if WOFF2_RESIZED in lengths and WOFF2_RESIZED in compressed:
        lengths[WOFF2_RESIZED] = compressed[WOFF2_RESIZED]
    expected = (ttf["flavour"], ttf["revision"], lengths)
    return expected == (woff2["flavour"], woff2["revision"], compressed)


def face_problems(stylesheet, directory, formats):
    """What keeps each @font-face in the file STYLESHEET from naming its face's files.

    Each url() is read against the stylesheet's own directory. A problem is a src that
    is not one file of each of FORMATS, such as ("woff2", "truetype"), a TrueType file
    whose family, weight class or italic bit is not the rule's, and a woff2 that is not
    the same cut as the TrueType file; then each font file in DIRECTORY that no rule
    names.
    """
    rules = FACE_RULE.findall(COMMENT.sub("", stylesheet.read_text()))
    if not rules:
        return ["no @font-face rule"]
    found = []
    named = set()
    for rule in rules:
        values = {key: value.strip() for key, value in FACE_DESCRIPTOR.findall(rule)}
        family = values.get("font-family", "").strip("\"'")
        weight = values.get("font-weight", "")
        style = values.get("font-style", "")
        urls = FACE_URL.findall(rule)
        files = {fmt: stylesheet.parent / url for url, fmt in urls}
        named.update(path.resolve() for path in files.values())
        if len(urls) != len(formats) or set(files) != set(formats):
            found.append(f"{family} {weight} {style}: not one each of {formats}")
            continue
        data = {fmt: path.read_bytes() for fmt, path in files.items()}
        if "truetype" not in data:
            continue
        ttf = ttf_face(data["truetype"])
        name = files["truetype"].name
        if ttf["family"] != family:
            found.append(f"{name}: family {ttf['family']}, not {family}")
        if str(ttf["weight"]) != weight:
            found.append(f"{name}: weight {ttf['weight']}, not {weight}")
        if ttf["italic"] != (style == "italic"):
            shape = "italic" if ttf["italic"] else "upright"
            found.append(f"{name}: {shape}, not font-style {style}")
        if "woff2" in data and not same_cut(ttf, woff2_face(data["woff2"])):
            found.append(f"{files['woff2'].name}: not the same cut as {name}")
    found += [
        f"{path.name}: named by no @font-face"
        for path in sorted(directory.iterdir())
        if path.name.endswith(FONT_SUFFIXES) and path.resolve() not in named
    ]
    return found


def problems(directory):
    """What keeps DIRECTORY from matching its SHA256SUMS.

    That is each line sha256sum -c fails, or its error where it names no line, and each
    file the list leaves out.
    """
    check = subprocess.run(
        ["sha256sum", "--check", "--quiet", "--strict", SUMS],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
    )
    found = check.stdout.splitlines()
    if check.returncode and not check.stdout:
        found.append(check.stderr.strip())
    listed = {
        line.partition("  ")[2] for line in (directory / SUMS).read_text().splitlines()
    }
    found += [
        f"{path.name}: not in {SUMS}"
        for path in sorted(directory.iterdir())
        if path.name not in listed | UNLISTED_OK
    ]
    return found
