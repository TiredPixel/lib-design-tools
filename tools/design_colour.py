"""Colour maths for checking a design: WCAG contrast, compositing, CIELAB and Delta-E.

Every colour is an opaque sRGB hex string, ``#RGB`` or ``#RRGGBB`` in either case; any
other string, an alpha form included, raises ValueError, since a translucent colour has
no contrast or Lab value until it is composited over a ground with ``composite``.
Nothing here reads a file.
"""

import itertools
import math
import re
from collections.abc import Iterable

_HEX = re.compile(r"#([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})")

# The CIE standard illuminant D65 white point, Y normalised to 1.
D65 = (0.95047, 1.0, 1.08883)

# Linear sRGB to CIE XYZ under D65, from IEC 61966-2-1.
SRGB_TO_XYZ = (
    (0.4124564, 0.3575761, 0.1804375),
    (0.2126729, 0.7151522, 0.0721750),
    (0.0193339, 0.1191920, 0.9503041),
)


def channels(hex_colour: str) -> tuple[int, int, int]:
    """The 8-bit (red, green, blue) of an opaque hex colour."""
    match = _HEX.fullmatch(hex_colour)
    if match is None:
        raise ValueError(f"{hex_colour!r} is not an opaque #RGB or #RRGGBB colour")
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(digit * 2 for digit in digits)
    red, green, blue = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
    return red, green, blue


def linear(channel: int) -> float:
    """An 8-bit sRGB channel as linear light, per IEC 61966-2-1."""
    c = channel / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_colour: str) -> float:
    """WCAG 2 relative luminance."""
    r, g, b = (linear(c) for c in channels(hex_colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG 2 contrast ratio between two colours, from 1 to 21."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def composite(colour: str, percent: float, ground: str) -> str:
    """COLOUR at PERCENT opacity over GROUND, mixed in sRGB as browsers do, as #RRGGBB
    rounded to 8 bits per channel."""
    if not 0 <= percent <= 100:
        raise ValueError(f"{percent}% is not an opacity")
    mixed = (
        round(c * percent / 100 + g * (1 - percent / 100))
        for c, g in zip(channels(colour), channels(ground), strict=True)
    )
    return "#" + "".join(f"{c:02X}" for c in mixed)


def _f(t: float) -> float:
    delta = 6 / 29
    return t ** (1 / 3) if t > delta**3 else t / (3 * delta**2) + 4 / 29


def lab(hex_colour: str) -> tuple[float, float, float]:
    """CIELAB (L*, a*, b*) under D65."""
    rgb = [linear(c) for c in channels(hex_colour)]
    xyz = [sum(m * c for m, c in zip(row, rgb, strict=True)) for row in SRGB_TO_XYZ]
    fx, fy, fz = (_f(v / w) for v, w in zip(xyz, D65, strict=True))
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def lightness(hex_colour: str) -> float:
    """CIELAB L*."""
    return lab(hex_colour)[0]


def chroma(hex_colour: str) -> float:
    """CIELAB C*ab."""
    _, a, b = lab(hex_colour)
    return math.hypot(a, b)


def hue(hex_colour: str) -> float:
    """CIELAB h(ab), in degrees from 0 to 360."""
    _, a, b = lab(hex_colour)
    return math.degrees(math.atan2(b, a)) % 360


def delta_e(a: str, b: str) -> float:
    """CIE76 Delta-E*ab between two colours."""
    return math.dist(lab(a), lab(b))


def hue_gap(a: float, b: float) -> float:
    """The smaller angle between two hue angles, in degrees."""
    gap = abs(a - b) % 360
    return min(gap, 360 - gap)


def hue_spread(colours: Iterable[str]) -> float:
    """The widest hue gap between any two of COLOURS."""
    pairs = itertools.combinations(colours, 2)
    return max(hue_gap(hue(a), hue(b)) for a, b in pairs)


def hue_between(middle: float, start: float, end: float) -> bool:
    """Whether hue MIDDLE lies on the shorter arc from hue START to hue END."""
    arc = hue_gap(start, middle) + hue_gap(middle, end)
    return math.isclose(arc, hue_gap(start, end))
