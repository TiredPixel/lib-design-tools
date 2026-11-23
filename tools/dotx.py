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
import itertools
import math
import pathlib
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Generator, Iterator
from xml.parsers import expat

import design_colour
import design_render
import tinycss2

SOURCE = pathlib.Path("src")

LOGO_PNG = "word/media/image1.png"
LOGO_SVG = "word/media/image2.svg"
LOGO_PNG_WIDTH = 1600

# Word's page, which translucent tokens are composited over: the one colour a part may
# write as a literal without an exemption.
PAGE = "FFFFFF"

GLOSSARY_TWINS = ("styles.xml", "numbering.xml", "fontTable.xml")

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# A custom property's name as a part writes it, in CSS's name characters: its case is
# significant, and anything from U+0080 up counts as a letter.
_NAME = r"--[-0-9A-Za-z_\u0080-\U0010FFFF]+"
_TOKEN = re.compile(rf"\{{({_NAME})\}}")
# In any white space and either quote XML allows, so laying a part out again changes
# nothing.
_SPACING = re.compile(rf"""<w:spacing\s+w:val\s*=\s*["']\{{({_NAME})\}}["']\s*/>""")
# A start tag the XML parser has accepted, so each quote in it opens or closes a value.
_START_TAG = re.compile(rb"""<(?:[^"'>]|"[^"]*"|'[^']*')*>""")
_HEX = re.compile(r"[0-9A-Fa-f]{6}")
# SVG's number and the comma or white space between a list's numbers.
_SVG_NUMBER = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_SVG_SEPARATOR = re.compile(r"[ \t\r\n]*,[ \t\r\n]*|[ \t\r\n]+")
DCTERMS_MODIFIED = "{http://purl.org/dc/terms/}modified"

# The colour spaces color-mix() takes, those with a hue able to name how it is
# interpolated: CSS Color 4, Candidate Recommendation Draft of 18 November 2026, §13,
# read 2026-11-23.
_POLAR_SPACES = frozenset({"hsl", "hwb", "lch", "oklch"})
_MIX_SPACES = _POLAR_SPACES | {
    "a98-rgb",
    "display-p3",
    "display-p3-linear",
    "lab",
    "oklab",
    "prophoto-rgb",
    "rec2020",
    "srgb",
    "srgb-linear",
    "xyz",
    "xyz-d50",
    "xyz-d65",
}
_HUE_METHODS = frozenset({"decreasing", "increasing", "longer", "shorter"})


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


def stripped(nodes: list) -> list:
    """Component values without the white space at either end."""
    start, end = 0, len(nodes)
    while start < end and nodes[start].type == "whitespace":
        start += 1
    while end > start and nodes[end - 1].type == "whitespace":
        end -= 1
    return nodes[start:end]


def comma_separated(nodes: list) -> list[list]:
    """Component values split at their top-level commas, each stripped."""
    groups = [[]]
    for node in nodes:
        if node == ",":
            groups.append([])
        else:
            groups[-1].append(node)
    return [stripped(group) for group in groups]


def is_root_compound(nodes: list) -> bool:
    """Whether a compound selector matches the root of every page a token styles.

    That is :root, which may repeat, alone or after ``*`` or ``html`` in any namespace,
    since the root of an HTML page is its ``html``.
    """
    rest = nodes[2:] if nodes[:2] == ["*", "|"] else nodes
    if rest and (
        rest[0] == "*" or (rest[0].type == "ident" and rest[0].lower_value == "html")
    ):
        rest = rest[1:]
    elif rest is not nodes:
        return False
    return (
        len(rest) >= 2
        and len(rest) % 2 == 0
        and all(
            colon == ":" and word.type == "ident" and word.lower_value == "root"
            for colon, word in zip(rest[::2], rest[1::2], strict=True)
        )
    )


def selects_root(prelude: list) -> bool:
    """Whether a rule's selector list holds a selector that always matches the root.

    A compound such as ``.dark:root`` matches only some of the time, so it declares no
    base token. A semicolon after the previous rule's block, which CSS reads as the
    start of this prelude, is passed over.
    """
    selectors = itertools.dropwhile(
        lambda node: node.type == "whitespace" or node == ";", prelude
    )
    return any(is_root_compound(s) for s in comma_separated(list(selectors)))


def block_items(content: list) -> Iterator:
    """The declarations and rules a block holds, one at a time."""
    return iter(
        tinycss2.parse_blocks_contents(
            content, skip_comments=True, skip_whitespace=True
        )
    )


def custom_properties(css: str) -> dict[str, list]:
    """The custom properties a stylesheet's :root rules declare, by name.

    Each value is its component values, as serialising a deeply nested one would reach
    the recursion limit whether or not the template names it. @layer always applies, so
    a rule in one counts, but a token declared more than once with @layer or !important
    is refused, since the cascade's order between them is not worked out. A rule inside
    any other at-rule, such as @media, applies only some of the time, so it declares no
    base token.
    """
    # tinycss2 reads each whole number as an int, which Python refuses past a set number
    # of digits, whether or not the template names that token.
    digits = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(0)
    try:
        rules = tinycss2.parse_stylesheet(
            css.removeprefix("\ufeff"), skip_comments=True, skip_whitespace=True
        )
    finally:
        sys.set_int_max_str_digits(digits)
    found = {}
    ranked = set()
    # The open blocks, innermost last, each as what is left of it and whether it lies
    # in a :root rule and in a layer: a stack, so layers nested to any depth are read.
    blocks = [(iter(rules), False, False)]
    while blocks:
        items, in_root, in_layer = blocks[-1]
        item = next(items, None)
        if item is None:
            blocks.pop()
        elif item.type == "declaration":
            if in_root and item.name.startswith("--"):
                is_ranked = in_layer or item.important
                if item.name in found and (is_ranked or item.name in ranked):
                    raise BuildError(
                        f"{item.name} is declared more than once with @layer or "
                        "!important, whose order is not worked out"
                    )
                if is_ranked:
                    ranked.add(item.name)
                found[item.name] = stripped(item.value)
        elif item.type == "at-rule":
            if item.lower_at_keyword == "layer" and item.content is not None:
                blocks.append((block_items(item.content), in_root, True))
        elif (
            item.type == "qualified-rule" and not in_root and selects_root(item.prelude)
        ):
            blocks.append((block_items(item.content), True, in_layer))
    return found


def tokens(token_files: list[pathlib.Path]) -> dict[str, list]:
    """Every token the template may name, by name, a later file's winning."""
    found = {}
    for path in token_files:
        found.update(custom_properties(read_text(path)))
    return found


def written(nodes: list) -> str:
    """Component values as CSS, for a message."""
    try:
        return tinycss2.serialize(nodes)
    except RecursionError:
        return "a value nested too deeply to show"


def var_reference(nodes: list) -> tuple[str, list | None] | None:
    """The token a value that is one var() names, with its fallback if it has one.

    A fallback may be empty, as ``var(--name,)`` is. None for any other value.
    """
    if len(nodes) != 1 or nodes[0].type != "function" or nodes[0].lower_name != "var":
        return None
    arguments = stripped(nodes[0].arguments)
    if not arguments or arguments[0].type != "ident":
        return None
    name = arguments[0].value
    rest = stripped(arguments[1:])
    if not name.startswith("--") or (rest and rest[0] != ","):
        return None
    return name, stripped(rest[1:]) if rest else None


def enter(name: str, declared: dict[str, list], through: dict[str, None]) -> None:
    """Add token ``name`` to those passed, refusing one unknown or passed already."""
    if name in through:
        loop = " -> ".join((*through, name))
        raise BuildError(f"{name} is defined through itself: {loop}")
    if name not in declared:
        raise BuildError(f"{name} is not a design-system token")
    through[name] = None


def mix_space(nodes: list) -> str | None:
    """The colour space of color-mix()'s first argument, or None if it is invalid."""
    words = [
        node.lower_value if node.type == "ident" else None
        for node in nodes
        if node.type != "whitespace"
    ]
    if len(words) < 2 or words[0] != "in" or words[1] not in _MIX_SPACES:
        return None
    if len(words) == 2:
        return words[1]
    is_hue_method = (
        len(words) == 4
        and words[1] in _POLAR_SPACES
        and words[2] in _HUE_METHODS
        and words[3] == "hue"
    )
    return words[1] if is_hue_method else None


def mix_terms(function) -> tuple[str, list[tuple[list, float | None]]] | None:
    """color-mix()'s colour space and its (colour, percentage) terms.

    CSS Color 5 (Working Draft of 16 November 2026, read 2026-11-23) makes the space
    optional, defaulting to Oklab, takes one or more terms, and takes a term's
    percentage on either side of its colour. None for a color-mix() of any other form.
    """
    arguments = comma_separated(function.arguments)
    space = "oklab"
    head = arguments[0][0] if arguments[0] else None
    if head is not None and head.type == "ident" and head.lower_value == "in":
        space = mix_space(arguments[0])
        arguments = arguments[1:]
    if space is None or not arguments:
        return None
    terms = []
    for argument in arguments:
        nodes = [node for node in argument if node.type != "whitespace"]
        percentages = [node.value for node in nodes if node.type == "percentage"]
        colours = [node for node in nodes if node.type != "percentage"]
        if len(colours) != 1 or len(percentages) > 1:
            return None
        terms.append((colours, percentages[0] if percentages else None))
    return space, terms


def is_transparent(nodes: list) -> bool:
    return (
        len(nodes) == 1
        and nodes[0].type == "ident"
        and nodes[0].lower_value == "transparent"
    )


def mix_weights(percentages: list[float | None]) -> list[float]:
    """Each color-mix() term's percentage of the result.

    Terms without one share what the others leave, and percentages over 100 in all are
    scaled to 100. Under 100 in all, the rest of the result is clear.
    """
    given = sum(p for p in percentages if p is not None)
    omitted = percentages.count(None)
    share = max(100 - given, 0) / omitted if omitted else 0
    return [
        share if p is None else p * 100 / given if given > 100 else p
        for p in percentages
    ]


def blend(inks: list[tuple[str, float]]) -> str:
    """Opaque inks, each at its percentage, with the page showing through the rest.

    Compositing is linear, so flattening each colour over the page before mixing gives
    what premultiplied sRGB mixing and then flattening would.
    """
    page = "#" + PAGE
    if not inks:
        return page
    if len(inks) == 1:
        return design_colour.composite(*inks[0], page)
    weights = [weight for _, weight in inks]
    rest = max(100 - sum(weights), 0)
    mixed = (
        math.floor(
            (sum(c * w for c, w in zip(values, weights, strict=True)) + g * rest) / 100
            + 0.5
        )
        for *values, g in zip(
            *(design_colour.channels(ink) for ink, _ in inks),
            design_colour.channels(page),
            strict=True,
        )
    )
    return "#" + "".join(f"{c:02X}" for c in mixed)


def run(task: Generator) -> str:
    """The result of a generator that yields each sub-task it needs done first.

    A stack of them in place of recursion, so a chain of tokens or nested values of any
    length resolves.
    """
    stack, result = [task], None
    while stack:
        try:
            needed = stack[-1].send(result)
        except StopIteration as done:
            stack.pop()
            result = done.value
        else:
            stack.append(needed)
            result = None
    return result


def colour(name: str, declared: dict[str, list]) -> str:
    """Token ``name`` as an opaque RRGGBB, as Word writes a colour.

    The tokens a var() or color-mix() names are followed, and one that comes back to a
    token it has passed is refused rather than followed for ever. A fallback is taken
    only for a token not declared. Each token's ink is kept, so a mix naming one token
    twice along a long chain takes linear time. ``transparent`` resolves to None, which
    a color-mix() can take and Word cannot.
    """
    inks = {}
    # Each token being worked out, in order, as a dict's keys keep it while testing in
    # one step.
    through = {}

    def token(target: str) -> Generator:
        if target in inks:
            return inks[target]
        enter(target, declared, through)
        ink = yield value(target, declared[target])
        del through[target]
        inks[target] = ink
        return ink

    def value(owner: str, nodes: list) -> Generator:
        if (reference := var_reference(nodes)) is not None:
            target, fallback = reference
            if target in declared or fallback is None:
                return (yield token(target))
            return (yield value(owner, fallback))
        if is_transparent(nodes):
            return None
        node = nodes[0] if len(nodes) == 1 else None
        if node is not None and node.type == "hash":
            try:
                flat = design_colour.flatten("#" + node.value, "#" + PAGE)
            except ValueError as error:
                raise BuildError(f"{owner}: {error}") from error
            return "#" + flat[1:].upper()
        terms = None
        if node is not None and node.type == "function":
            terms = mix_terms(node) if node.lower_name == "color-mix" else None
        if terms is not None:
            space, mixed = terms
            percentages = [percentage for _, percentage in mixed]
            for percentage in percentages:
                if percentage is not None and not 0 <= percentage <= 100:
                    raise BuildError(f"{owner}: {percentage}% is not an opacity")
            weighted = []
            for (colours, _), weight in zip(
                mixed, mix_weights(percentages), strict=True
            ):
                ink = yield value(owner, colours)
                if ink is not None:
                    weighted.append((ink, weight))
            # Mixed only with transparent, a colour keeps its own components whatever
            # the space, as premultiplying leaves transparent nothing to contribute.
            if len(weighted) <= 1 or space == "srgb":
                return blend(weighted)
            raise BuildError(
                f"{owner} mixes {len(weighted)} colours in {space}, and only a mix in "
                "srgb can be resolved"
            )
        raise BuildError(
            f"{owner} is {written(nodes)!r}, which is not a colour Word can take"
        )

    ink = run(token(name))
    if ink is None:
        raise BuildError(
            f"{name} is 'transparent', which is not a colour Word can take"
        )
    return ink[1:]


def tracking_em(name: str, declared: dict[str, list]) -> float:
    """Letter-spacing token ``name`` in em, followed through any var()."""
    through = {}
    enter(name, declared, through)
    owner, nodes = name, declared[name]
    while (reference := var_reference(nodes)) is not None:
        target, fallback = reference
        if target in declared or fallback is None:
            enter(target, declared, through)
            owner, nodes = target, declared[target]
        else:
            nodes = fallback
    node = nodes[0] if len(nodes) == 1 else None
    if node is not None and (
        (node.type == "dimension" and node.lower_unit == "em")
        or (node.type == "number" and node.value == 0)
    ):
        return float(node.value)
    raise BuildError(f"{owner} is {written(nodes)!r}, not a length in em")


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


def spacing_tags(source: bytes) -> list[tuple[int, int, str, str]]:
    """Each w:spacing tag in a w:style whose only attribute is a ``{--name}`` w:val.

    As the (start, end, style id, token name) of each, found by parsing, so a tag or
    style id written in any form XML allows is read as what it is.
    """
    found = []
    open_styles = []
    parser = expat.ParserCreate(namespace_separator="}")

    def start_element(name: str, attributes: dict[str, str]) -> None:
        if name == f"{W[1:]}style":
            open_styles.append(attributes.get(f"{W[1:]}styleId"))
        elif name == f"{W[1:]}spacing" and open_styles and open_styles[-1] is not None:
            value = attributes.get(f"{W[1:]}val", "")
            if len(attributes) == 1 and (token := _TOKEN.fullmatch(value)):
                begin = parser.CurrentByteIndex
                end = _START_TAG.match(source, begin).end()
                # One written with a character reference is no placeholder.
                if token.group(0).encode() in source[begin:end]:
                    found.append((begin, end, open_styles[-1], token.group(1)))

    def end_element(name: str) -> None:
        if name == f"{W[1:]}style":
            open_styles.pop()

    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    parser.Parse(source, True)
    return found


def resolve_tracking(styles: str, declared: dict[str, list]) -> str:
    """styles.xml with each ``{--name}`` character spacing in twips.

    Each is worked out from the token and the size of the style it sits in, a size
    looked up only for a style that names such a token. A self-closed ``w:spacing`` is
    written in one layout whatever its white space or quotes, and any other form keeps
    its own, with only the token replaced.
    """
    source = styles.encode()
    root = ET.fromstring(source)
    pieces = []
    position = 0
    for begin, end, style_id, name in spacing_tags(source):
        tag = source[begin:end].decode()
        try:
            half_points = style_size(root, style_id)
            em = tracking_em(name, declared)
            try:
                twips = round(em * half_points * 10)
            except (OverflowError, ValueError) as error:
                raise BuildError(f"{name}: {error}") from error
        except BuildError as error:
            raise BuildError(f"{style_id}: {error}") from error
        pieces.append(source[position:begin])
        pieces.append(
            f'<w:spacing w:val="{twips}"/>'.encode()
            if _SPACING.fullmatch(tag)
            else tag.replace(f"{{{name}}}", str(twips)).encode()
        )
        position = end
    pieces.append(source[position:])
    return b"".join(pieces).decode()


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
    path: str, part: str, declared: dict[str, list], literals: frozenset[str]
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
    """The width and height of the first viewBox in an SVG, wherever its origin lies.

    SVG separates its four numbers by white space, a comma or both.
    """
    try:
        root = ET.fromstring(svg.encode())
    except (ET.ParseError, LookupError) as error:
        raise BuildError(f"the logo is not XML: {error}") from error
    box = next(
        (e.get("viewBox") for e in root.iter() if e.get("viewBox") is not None), None
    )
    if box is None:
        raise BuildError("the logo has no viewBox")
    fields = _SVG_SEPARATOR.split(box.strip(" \t\r\n"))
    if len(fields) != 4:
        raise BuildError(f"the logo's viewBox {box!r} is not four numbers")
    for field in fields:
        if _SVG_NUMBER.fullmatch(field) is None:
            raise BuildError(
                f"the logo's viewBox: could not convert string to float: {field!r}"
            )
    width, height = float(fields[2]), float(fields[3])
    if width < 0 or height < 0:
        raise BuildError(f"the logo's viewBox {box!r} has a negative size")
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
    version = next(
        (
            child.text
            for element in ET.fromstring(custom.encode()).iter()
            if element.get("name") == "TemplateVersion"
            for child in element
            if child.tag.rsplit("}", 1)[-1] == "lpwstr" and child.text
        ),
        None,
    )
    if version is None:
        raise BuildError("docProps/custom.xml sets no TemplateVersion")
    try:
        date = datetime.date.fromisoformat(version)
    except ValueError as error:
        raise BuildError(f"TemplateVersion: {error}") from error
    # Each zip entry is dated the release, and a zip dates only these years.
    if not 1980 <= date.year <= 2107:
        raise BuildError(f"TemplateVersion: {date} is not from 1980 to 2107")
    return date


def check_modified(core: str, date: datetime.date) -> None:
    """Refuse a docProps/core.xml modified other than at the release's midnight."""
    modified = next(ET.fromstring(core.encode()).iter(DCTERMS_MODIFIED), None)
    value = "missing" if modified is None else modified.text or ""
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
    declared: dict[str, list],
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
