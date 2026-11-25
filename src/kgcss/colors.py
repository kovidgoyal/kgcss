# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""CSS colors, as CSS Color Module Level 4 defines them (docs/plan-04-media-colors.md, §4).

parse_rgba() is the fast path: any color as sRGB, each channel a float from 0 to 1,
clipped to the sRGB gamut. It understands named colors, hex colors, rgb(), hsl(),
hwb(), lab(), lch(), oklab(), oklch() and color() with the predefined color spaces,
in the legacy and modern syntaxes. Colors that need computing, such as those with
calc(), var() or color-mix(), and system colors, are not colors here: they give None.

>>> parse_rgba('rebeccapurple')
kgcss.RGBA(red=0.4, green=0.2, blue=0.6, alpha=1.0)
>>> parse_rgba('rgb(255 0 0 / 50%)').alpha
0.5
>>> parse_rgba('currentcolor')
'currentColor'
>>> parse_rgba('rgb(var(--x))') is None
True
"""

from kgcss._kgcss import RGBA, parse_rgba

__all__ = ['RGBA', 'parse_rgba']
