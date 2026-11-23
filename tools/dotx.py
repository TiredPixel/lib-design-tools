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
import math
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
# A style id holds no "<", which keeps what this captures parseable as an attribute.
_STYLE = re.compile(r'<w:style [^>]*w:styleId="([^"<]+)"[^>]*>.*?</w:style>', re.DOTALL)
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
        found.update(custom_properties(read_text(path)))
    return found


def colour(name: str, declared: dict[str, str]) -> str:
    """Token ``name`` as an opaque RRGGBB, as Word writes a colour.

    The tokens a var() or color-mix() names are followed in a loop rather than by
    recursion, so a chain of any length resolves, and one that comes back to a token
    it has passed is refused rather than followed for ever. Each color-mix() along it
    is then applied from the innermost out.
    """
    # Each token passed, in order, as a dict's keys keep it while testing in one step.
    through = {}
    mixes = []
    while True:
        if name in through:
            loop = " -> ".join((*through, name))
            raise BuildError(f"{name} is defined through itself: {loop}")
        if name not in declared:
            raise BuildError(f"{name} is not a design-system token")
        through[name] = None
        value = declared[name]
        if value.startswith("#"):
            try:
                ink = "#" + design_colour.flatten(value, "#" + PAGE)[1:].upper()
            except ValueError as error:
                raise BuildError(f"{name}: {error}") from error
            break
        if alias := _VAR.fullmatch(value):
            name = alias.group(1)
        elif mix := _MIX.fullmatch(value):
            mixes.append((name, mix.group(2)))
            name = mix.group(1)
        else:
            raise BuildError(
                f"{name} is {value!r}, which is not a colour Word can take"
            )
    for mixed, percentage in reversed(mixes):
        try:
            ink = design_colour.composite(ink, float(percentage), "#" + PAGE)
        except ValueError as error:
            raise BuildError(f"{mixed}: {error}") from error
    return ink[1:]


def tracking_em(name: str, declared: dict[str, str]) -> float:
    """Letter-spacing token ``name`` in em."""
    if name not in declared:
        raise BuildError(f"{name} is not a design-system token")
    em = _EM.fullmatch(declared[name])
    if em is None:
        raise BuildError(f"{name} is {declared[name]!r}, not a length in em")
    return float(em.group(1) or 0)


def style_size(styles: ET.Element, style_id: str) -> int:
    """Style ``style_id``'s font size in half-points.

    A style without its own size takes it through its basedOn chain, ending at the
    document defaults. A chain that names no style, loops back or ends with no size is
    refused, since no size can be worked out from it.
    """
    by_id = {s.get(W + "styleId"): s for s in styles.iter(W + "style")}
    reached = []
    name = style_id
    while True:
        if name is None:
            raise BuildError("its basedOn chain reaches a basedOn with no w:val")
        style = by_id.get(name)
        if style is None:
            raise BuildError(f"its basedOn chain reaches {name}, which is no style")
        if name in reached:
            raise BuildError(f"its basedOn chain loops back to {name}")
        reached.append(name)
        size = style.find(f"{W}rPr/{W}sz")
        based = style.find(W + "basedOn")
        if size is None and based is None:
            size = styles.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr/{W}sz")
            if size is None:
                raise BuildError(
                    "no size is set by it, a style it is based on or the document "
                    "defaults"
                )
        if size is not None:
            break
        name = based.get(W + "val")
    try:
        return int(size.get(W + "val"))
    except (TypeError, ValueError) as error:
        raise BuildError(
            f"w:sz {size.get(W + 'val')!r} is not a size in half-points"
        ) from error


def resolve_tracking(styles: str, declared: dict[str, str]) -> str:
    """styles.xml with each ``{--name}`` character spacing in twips.

    Each is worked out from the token and the size of the style it sits in, a size
    looked up only for a style that names such a token.
    """
    root = ET.fromstring(styles.encode())

    def style(match: re.Match) -> str:
        # The pattern matches the attribute as written, so its references are resolved
        # as the parser resolved them for the ids style_size looks it up among. One the
        # parser refuses alone, as a DTD's entity or text in a comment is, stays as
        # written.
        try:
            style_id = ET.fromstring(f'<a v="{match.group(1)}"/>').get("v")
        except ET.ParseError:
            style_id = match.group(1)

        def spacing(token: re.Match) -> str:
            half_points = style_size(root, style_id)
            em = tracking_em(token.group(1), declared)
            try:
                twips = round(em * half_points * 10)
            except (OverflowError, ValueError) as error:
                raise BuildError(f"{token.group(1)}: {error}") from error
            return f'<w:spacing w:val="{twips}"/>'

        try:
            return _SPACING.sub(spacing, match.group(0))
        except BuildError as error:
            raise BuildError(f"{style_id}: {error}") from error

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
    try:
        if path == "word/styles.xml":
            part = resolve_tracking(part, declared)
        return _TOKEN.sub(lambda token: colour(token.group(1), declared), part)
    except BuildError as error:
        raise BuildError(f"{path}: {error}") from error


def view_box(svg: str) -> tuple[float, float]:
    """The width and height of an SVG's viewBox, wherever its origin lies."""
    match = _VIEW_BOX.search(svg)
    if match is None:
        raise BuildError("the logo has no viewBox")
    try:
        width, height = float(match.group(1)), float(match.group(2))
    except ValueError as error:
        raise BuildError(f"the logo's viewBox: {error}") from error
    if width == 0:
        raise BuildError("the logo's viewBox has no width to scale its height by")
    if math.isinf(width) or math.isinf(height):
        raise BuildError("the logo's viewBox is too large to scale")
    return width, height


def render_png(svg: pathlib.Path, width: int) -> bytes:
    """The SVG at ``width`` pixels, on a transparent ground, by headless Chromium."""
    view_width, view_height = view_box(read_text(svg))
    # A finite viewBox can still scale past a float, as a vast height or a subnormal
    # width does.
    try:
        height = round(width * view_height / view_width)
    except OverflowError as error:
        raise BuildError("the logo's viewBox is too large to scale") from error
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
        date = datetime.date.fromisoformat(match.group(1))
    except ValueError as error:
        raise BuildError(f"TemplateVersion: {error}") from error
    # Each zip entry is dated the release, and a zip dates only these years.
    if not 1980 <= date.year <= 2107:
        raise BuildError(f"TemplateVersion: {date} is not from 1980 to 2107")
    return date


def check_modified(core: str, date: datetime.date) -> None:
    """Refuse a docProps/core.xml modified other than at the release's midnight."""
    match = _MODIFIED.search(core)
    value = match.group(1) if match else "missing"
    if value != f"{date.isoformat()}T00:00:00Z":
        raise BuildError(
            f"docProps/core.xml: dcterms:modified is {value}, not RELEASET00:00:00Z"
        )


def read_text(file: pathlib.Path) -> str:
    """A file as text, refused unless it can be read and is UTF-8."""
    try:
        return file.read_bytes().decode("utf-8")
    except OSError as error:
        raise BuildError(f"{file}: {error.strerror or error}") from error
    except UnicodeDecodeError as error:
        raise BuildError(f"{file}: not UTF-8: {error}") from error


def read_part(file: pathlib.Path) -> str:
    """A src/ file as text, refused unless it is UTF-8 and parses as XML."""
    text = read_text(file)
    try:
        ET.fromstring(text.encode())
    except (ET.ParseError, LookupError) as error:
        raise BuildError(f"{file}: not XML: {error}") from error
    return text


def built_part(built: dict[str, bytes], path: str) -> bytes:
    """Part ``path`` of ``built``, refused where src/ has none."""
    if path not in built:
        raise BuildError(f"{path} is missing from src/")
    return built[path]


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
    try:
        files = sorted(source.iterdir())
    except OSError as error:
        raise BuildError(f"{source}: {error.strerror or error}") from error
    for file in files:
        path = part_name(file.name)
        for twin in GLOSSARY_TWINS:
            if path == f"word/glossary/{twin}":
                raise BuildError(f"{path} is built from word/{twin}")
        allowed = literals.get(path, frozenset())
        built[path] = resolve(path, read_part(file), declared, allowed).encode()
    for twin in GLOSSARY_TWINS:
        built[f"word/glossary/{twin}"] = built_part(built, f"word/{twin}")
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
        logo = read_text(svg).encode(), render_png(svg, LOGO_PNG_WIDTH)
    built = parts(SOURCE, declared, literals, logo)
    date = release_date(built_part(built, "docProps/custom.xml").decode())
    check_modified(built_part(built, "docProps/core.xml").decode(), date)
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
