# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""CSS colors, as CSS Color Module Level 4 defines them.

Two functions share one parser, written in C. It understands named colors, hex colors,
rgb(), hsl(), hwb(), lab(), lch(), oklab(), oklch() and color() with the predefined color
spaces, in the legacy and modern syntaxes. Colors that need computing, such as those with
calc(), var() or color-mix(), and system colors, are not colors here: both give None.

parse_rgba() is the fast path: any color as sRGB, each channel a float from 0 to 1,
clipped to the sRGB gamut.

>>> parse_rgba('rebeccapurple')
kgcss.RGBA(red=0.4, green=0.2, blue=0.6, alpha=1.0)
>>> parse_rgba('rgb(255 0 0 / 50%)').alpha
0.5
>>> parse_rgba('currentcolor')
'currentColor'
>>> parse_rgba('rgb(var(--x))') is None
True

parse_color() gives the color as specified, a Color: its color space, its channels, which
may be out of the sRGB gamut, and its alpha. to_css() writes it as CSS Color 4 serializes
specified values, and to_rgba() converts it as parse_rgba() does.

>>> c = parse_color('LCH(50% 30% 700deg / none)')
>>> c
Color(space='lch', channels=(50.0, 45.0, 340.0), alpha=None)
>>> c.to_css()
'lch(50 45 340 / none)'
>>> parse_color('hsl(120deg 50% 50% / 25%)').to_css()
'rgba(63.75, 191.25, 63.75, 0.25)'
"""

import math
from dataclasses import dataclass
from typing import Final, Literal, final, get_args

from kgcss._kgcss import RGBA, color_to_rgba, parse_color_raw, parse_rgba
from kgcss.cssom.values import Component

__all__ = ['RGBA', 'Color', 'ColorSpace', 'parse_color', 'parse_rgba']

# 'rgb' holds every legacy sRGB form: names, hex, rgb(), hsl() and hwb(), with channels from 0 to 255.
# The others are the functions lab() to oklch(), and the predefined spaces of color().
type ColorSpace = Literal[
    'rgb', 'lab', 'lch', 'oklab', 'oklch', 'srgb', 'srgb-linear', 'display-p3', 'a98-rgb', 'prophoto-rgb', 'rec2020',
    'xyz-d50', 'xyz-d65',
]  # fmt: skip
COLOR_SPACES: Final[frozenset[str]] = frozenset(get_args(ColorSpace.__value__))
FUNCTION_SPACES: Final[frozenset[str]] = frozenset(('lab', 'lch', 'oklab', 'oklch'))

type Channel = float | None


def format_number(x: float) -> str:
    """A number as CSS Color 4 serializations write it: at most 6 decimals, without trailing zeros."""
    text = f'{x:.6f}'.rstrip('0').rstrip('.')
    return '0' if text == '-0' else text


def format_channel(x: Channel) -> str:
    return 'none' if x is None else format_number(x)


@final
@dataclass(frozen=True, slots=True)
class Color:
    """A color as specified. channels are in the units of the space: 0 to 255 for rgb, as in lab()
    and the like for those (lightness from 0 to 100 for lab, 0 to 1 for oklab, hues in degrees),
    and 0 to 1 for the spaces of color(), though they may be outside that. None is the none
    keyword, which counts as 0 when converting. alpha is from 0 to 1, or None."""

    space: ColorSpace
    channels: tuple[Channel, Channel, Channel]
    alpha: Channel = 1.0

    def __post_init__(self) -> None:
        if self.space not in COLOR_SPACES:
            raise ValueError(f'Unknown color space: {self.space!r}')
        if len(self.channels) != 3:
            raise ValueError(f'A color has three channels, not {len(self.channels)}')
        for x in (*self.channels, self.alpha):
            if x is not None and not math.isfinite(x):
                raise ValueError(f'Color channels must be finite, not {x}')
        if self.space == 'rgb' and (None in self.channels or self.alpha is None):
            raise ValueError('The rgb space has no none: use 0')

    def to_rgba(self) -> RGBA:
        """The color in sRGB, each channel clipped to 0..1, as parse_rgba() gives it."""
        c0, c1, c2 = self.channels
        return color_to_rgba(self.space, c0, c1, c2, self.alpha)

    def to_css(self) -> str:
        """The color as CSS Color 4 serializes specified values: rgb() or rgba() for the legacy sRGB
        forms, the function for lab() to oklch(), and color() for the other spaces."""
        # An alpha that is written as 1 is left out, so that writing is idempotent
        alpha = format_channel(self.alpha)
        if self.space == 'rgb':
            values = ', '.join(map(format_channel, self.channels))
            return f'rgb({values})' if alpha == '1' else f'rgba({values}, {alpha})'
        values = ' '.join(map(format_channel, self.channels))
        alpha = '' if alpha == '1' else f' / {alpha}'
        if self.space in FUNCTION_SPACES:
            return f'{self.space}({values}{alpha})'
        return f'color({self.space} {values}{alpha})'


# The parser's output is valid, so parse_color() skips __init__ and __post_init__, which cost more than
# the parse. Setting the slots through their descriptors bypasses the frozen __setattr__.
_new = object.__new__
_set_space = Color.__dict__['space'].__set__
_set_channels = Color.__dict__['channels'].__set__
_set_alpha = Color.__dict__['alpha'].__set__


def _color(space: ColorSpace, c0: Channel, c1: Channel, c2: Channel, alpha: Channel) -> Color:
    ans = _new(Color)
    _set_space(ans, space)
    _set_channels(ans, (c0, c1, c2))
    _set_alpha(ans, alpha)
    return ans


def parse_color(text: str | Component) -> Color | Literal['currentColor'] | None:
    """The color as specified, 'currentColor' for currentColor in any case, None if text is not a color.
    A Component, from a parsed value, is parsed from its css_text."""
    if isinstance(text, Component):
        text = text.css_text
    raw = parse_color_raw(text)
    if raw is None or isinstance(raw, str):
        return raw
    return _color(*raw)
