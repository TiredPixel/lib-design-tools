"""Assemble a Word template from src/ and a design system, writing it to stdout.

Each file in src/ is a part of the package, its path flattened: ``word__styles.xml``
is ``word/styles.xml`` and ``-Content_Types-.xml`` is ``[Content_Types].xml``. The parts
name design-system tokens wherever Word needs a literal: ``{--name}`` in a colour
attribute, resolved from the ``--tokens`` stylesheets, and ``{--name}`` as a style's
character spacing, resolved against that style's size. Word draws text and borders
opaque, so a translucent token is composited over the white page. The glossary's
styles, numbering and font table are copies of the template's, since Word resolves a
building block against the glossary's own parts. A ``--logo`` is embedded as its SVG,
with a PNG fallback rendered from it for a Word that cannot draw SVG.
"""

import argparse
import datetime
import io
import pathlib
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

import design_colour
import design_render

SOURCE = pathlib.Path("src")

LOGO_PNG = "word/media/image1.png"
LOGO_SVG = "word/media/image2.svg"
LOGO_PNG_WIDTH = 1600

# Word's page, which translucent tokens are composited over: the one colour a part may
# write as a literal without an exemption.
PAGE = "FFFFFF"

GLOSSARY_TWINS = ("styles.xml", "numbering.xml", "fontTable.xml")

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_TOKEN = re.compile(r"\{(--[a-z0-9-]+)\}")
_SPACING = re.compile(r'<w:spacing w:val="\{(--[a-z0-9-]+)\}"/>')
_STYLE = re.compile(r'<w:style [^>]*w:styleId="([^"]+)"[^>]*>.*?</w:style>', re.DOTALL)
_ROOT = re.compile(r":root\s*\{(.*?)\}", re.DOTALL)
_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_DECLARATION = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+);")
_VAR = re.compile(r"var\((--[a-z0-9-]+)\)")
_MIX = re.compile(
    r"color-mix\(in srgb, var\((--[a-z0-9-]+)\) ([0-9.]+)%, transparent\)"
)
_EM = re.compile(r"(-?(?:[0-9]*\.)?[0-9]+)em|0")
_HEX = re.compile(r"[0-9A-Fa-f]{6}")
_VIEW_BOX = re.compile(r'viewBox="-?[0-9.]+ -?[0-9.]+ ([0-9.]+) ([0-9.]+)"')
_TEMPLATE_VERSION = re.compile(r'name="TemplateVersion"><vt:lpwstr>([^<]+)<')
_MODIFIED = re.compile(r"<dcterms:modified[^>]*>([^<]*)</dcterms:modified>")


class BuildError(Exception):
    pass


def part_name(filename: str) -> str:
    """The package path a flattened src/ filename stands for."""
    if filename == "-Content_Types-.xml":
        return "[Content_Types].xml"
    return filename.replace("__", "/")


def part_order(path: str) -> tuple[int, str]:
    """[Content_Types].xml, _rels, docProps, then every other part, each by path."""
    if path == "[Content_Types].xml":
        return 0, path
    for rank, prefix in enumerate(("_rels/", "docProps/"), 1):
        if path.startswith(prefix):
            return rank, path
    return 3, path


def custom_properties(css: str) -> dict[str, str]:
    """The custom properties a stylesheet's :root blocks declare, by name."""
    found = {}
    for block in _ROOT.findall(_COMMENT.sub("", css)):
        for name, value in _DECLARATION.findall(block):
            found[name] = value.strip()
    return found


def tokens(token_files: list[pathlib.Path]) -> dict[str, str]:
    """Every token the template may name, by name, a later file's winning."""
    found = {}
    for path in token_files:
        found.update(custom_properties(path.read_text(encoding="utf-8")))
    return found


def colour(name: str, declared: dict[str, str]) -> str:
    """Token ``name`` as an opaque RRGGBB, as Word writes a colour."""
    if name not in declared:
        raise BuildError(f"{name} is not a design-system token")
    value = declared[name]
    if value.startswith("#"):
        try:
            return design_colour.flatten(value, "#" + PAGE)[1:].upper()
        except ValueError as error:
            raise BuildError(f"{name}: {error}") from error
    if alias := _VAR.fullmatch(value):
        return colour(alias.group(1), declared)
    if mix := _MIX.fullmatch(value):
        ink = "#" + colour(mix.group(1), declared)
        try:
            return design_colour.composite(ink, float(mix.group(2)), "#" + PAGE)[1:]
        except ValueError as error:
            raise BuildError(f"{name}: {error}") from error
    raise BuildError(f"{name} is {value!r}, which is not a colour Word can take")


def tracking_em(name: str, declared: dict[str, str]) -> float:
    """Letter-spacing token ``name`` in em."""
    if name not in declared:
        raise BuildError(f"{name} is not a design-system token")
    em = _EM.fullmatch(declared[name])
    if em is None:
        raise BuildError(f"{name} is {declared[name]!r}, not a length in em")
    return float(em.group(1) or 0)


def style_sizes(styles: str) -> dict[str, int]:
    """Each style's font size in half-points.

    A style without its own size takes it through its basedOn chain, ending at the
    document defaults.
    """
    root = ET.fromstring(styles.encode())
    default = root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr/{W}sz")
    by_id = {s.get(W + "styleId"): s for s in root.iter(W + "style")}

    def size(style_id: str) -> int:
        style = by_id[style_id]
        own = style.find(f"{W}rPr/{W}sz")
        if own is not None:
            return int(own.get(W + "val"))
        based = style.find(W + "basedOn")
        if based is None:
            return int(default.get(W + "val"))
        return size(based.get(W + "val"))

    return {style_id: size(style_id) for style_id in by_id}


def resolve_tracking(styles: str, declared: dict[str, str]) -> str:
    """styles.xml with each ``{--name}`` character spacing in twips.

    Each is worked out from the token and the size of the style it sits in.
    """
    sizes = style_sizes(styles)

    def style(match: re.Match) -> str:
        half_points = sizes[match.group(1)]

        def spacing(token: re.Match) -> str:
            twips = round(tracking_em(token.group(1), declared) * half_points * 10)
            return f'<w:spacing w:val="{twips}"/>'

        return _SPACING.sub(spacing, match.group(0))

    return _STYLE.sub(style, styles)


def literal_colours(part: str) -> list[str]:
    """The colour literals a source part writes, page white aside."""
    found = []
    for element in ET.fromstring(part.encode()).iter():
        tag = element.tag.rsplit("}", 1)[-1]
        for attribute, value in element.attrib.items():
            name = attribute.rsplit("}", 1)[-1]
            is_colour = name in ("color", "fill", "lastClr") or (
                name == "val" and tag in ("color", "srgbClr")
            )
            if is_colour and _HEX.fullmatch(value) and value.upper() != PAGE:
                found.append(value)
    return found


def resolve(
    path: str, part: str, declared: dict[str, str], literals: frozenset[str]
) -> str:
    """Source part ``path`` with every token it names resolved.

    ``literals`` are the colour literals the part may write.
    """
    refused = [c for c in literal_colours(part) if c.upper() not in literals]
    if refused:
        raise BuildError(f"{path} writes colour literals {refused}; name tokens")
    if path == "word/styles.xml":
        part = resolve_tracking(part, declared)
    return _TOKEN.sub(lambda token: colour(token.group(1), declared), part)


def view_box(svg: str) -> tuple[float, float]:
    """The width and height of an SVG's viewBox, wherever its origin lies."""
    match = _VIEW_BOX.search(svg)
    if match is None:
        raise BuildError("the logo has no viewBox")
    return float(match.group(1)), float(match.group(2))


def render_png(svg: pathlib.Path, width: int) -> bytes:
    """The SVG at ``width`` pixels, on a transparent ground, by headless Chromium."""
    view_width, view_height = view_box(svg.read_text(encoding="utf-8"))
    height = round(width * view_height / view_width)
    with tempfile.TemporaryDirectory() as scratch:
        page = pathlib.Path(scratch) / "logo.html"
        page.write_text(
            "<!doctype html><style>*{margin:0}img{display:block}</style>"
            f'<img src="{svg.resolve().as_uri()}" width="{width}" height="{height}">',
            encoding="utf-8",
        )
        return design_render.screenshot(page.as_uri(), width, height)


def release_date(custom: str) -> datetime.date:
    """The TemplateVersion custom property, the release's ISO 8601 date."""
    match = _TEMPLATE_VERSION.search(custom)
    if match is None:
        raise BuildError("docProps/custom.xml sets no TemplateVersion")
    try:
        return datetime.date.fromisoformat(match.group(1))
    except ValueError as error:
        raise BuildError(f"TemplateVersion: {error}") from error


def check_modified(core: str, date: datetime.date) -> None:
    """Refuse a docProps/core.xml modified other than at the release's midnight."""
    match = _MODIFIED.search(core)
    value = match.group(1) if match else ""
    if value != f"{date.isoformat()}T00:00:00Z":
        raise BuildError(
            f"docProps/core.xml: dcterms:modified is {value}, not RELEASET00:00:00Z"
        )


def read_part(file: pathlib.Path) -> str:
    """A src/ file as text, refused unless it is UTF-8 and parses as XML."""
    try:
        text = file.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise BuildError(f"{file}: not UTF-8: {error}") from error
    try:
        ET.fromstring(text.encode())
    except ET.ParseError as error:
        raise BuildError(f"{file}: not XML: {error}") from error
    return text


def parts(
    source: pathlib.Path,
    declared: dict[str, str],
    literals: dict[str, frozenset[str]],
    logo: tuple[bytes, bytes] | None,
) -> dict[str, bytes]:
    """Every part of the package, by path, in package order.

    ``logo`` is the SVG and the PNG rendered from it.
    """
    built = {}
    for file in sorted(source.iterdir()):
        path = part_name(file.name)
        for twin in GLOSSARY_TWINS:
            if path == f"word/glossary/{twin}":
                raise BuildError(f"{path} is built from word/{twin}")
        allowed = literals.get(path, frozenset())
        built[path] = resolve(path, read_part(file), declared, allowed).encode()
    for twin in GLOSSARY_TWINS:
        built[f"word/glossary/{twin}"] = built[f"word/{twin}"]
    if logo is not None:
        built[LOGO_SVG], built[LOGO_PNG] = logo
    return dict(sorted(built.items(), key=lambda item: part_order(item[0])))


def package(built: dict[str, bytes], date: datetime.date) -> bytes:
    """A stored zip of ``built`` in its order.

    Every entry is dated ``date``, so the same parts give the same bytes.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for path, data in built.items():
            entry = zipfile.ZipInfo(path, date.timetuple()[:6])
            entry.create_system = 0
            archive.writestr(entry, data)
    return buffer.getvalue()


def literal(value: str) -> tuple[str, str]:
    """A --literal PART=RRGGBB as its part and colour."""
    path, separator, hex_colour = value.partition("=")
    if not separator or not _HEX.fullmatch(hex_colour):
        raise argparse.ArgumentTypeError(f"{value!r} is not PART=RRGGBB")
    return path, hex_colour.upper()


def arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build-dotx",
        description="Assemble a Word template from src/ and a design system.",
    )
    parser.add_argument("--design-system", type=pathlib.Path, required=True)
    parser.add_argument(
        "--tokens",
        action="append",
        required=True,
        help="a token stylesheet in the design system, a later one's tokens winning",
    )
    parser.add_argument("--logo", help="the logo's SVG in the design system")
    parser.add_argument(
        "--literal",
        action="append",
        default=[],
        type=literal,
        help="a colour PART may write as a literal, as PART=RRGGBB",
    )
    return parser.parse_args(argv)


def build(options: argparse.Namespace) -> bytes:
    """The template's bytes."""
    declared = tokens([options.design_system / path for path in options.tokens])
    literals = {}
    for path, hex_colour in options.literal:
        literals[path] = literals.get(path, frozenset()) | {hex_colour}
    logo = None
    if options.logo is not None:
        svg = options.design_system / options.logo
        logo = svg.read_bytes(), render_png(svg, LOGO_PNG_WIDTH)
    built = parts(SOURCE, declared, literals, logo)
    date = release_date(built["docProps/custom.xml"].decode())
    check_modified(built["docProps/core.xml"].decode(), date)
    return package(built, date)


def main(argv: list[str]) -> None:
    options = arguments(argv)
    try:
        template = build(options)
    except (BuildError, design_render.RenderError) as error:
        sys.exit(f"build-dotx: {error}")
    sys.stdout.buffer.write(template)


if __name__ == "__main__":
    main(sys.argv[1:])
