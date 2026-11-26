# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Value grammar for the longhands of the common shorthands.

This is not a validator for all of CSS. It knows the value types and the longhands that
shorthand expansion needs: the edges of margin and padding, the parts of border, list-style
and font. matches() says whether a value is valid for one of them, and None for a property
it does not know.

>>> matches('border-top-style', 'solid'), matches('border-top-width', 'solid')
(True, False)
>>> matches('list-style-type', 'upper-roman'), matches('list-style-position', 'upper-roman')
(True, False)
>>> matches('margin-left', '-2.5em'), matches('padding-left', '-2.5em')
(True, False)
>>> matches('color', 'red') is None
True
>>> is_color('#abcd'), is_color('inherit')
(True, False)
"""

from collections.abc import Callable, Sequence
from functools import lru_cache
from typing import Final

from kgcss.colors import parse_rgba
from kgcss.cssom.values import (
    Component,
    Dimension,
    Function,
    Hash,
    Ident,
    Number,
    Percentage,
    PropertyValue,
    Separator,
    String,
    Url,
    parse_value,
)

__all__ = ['PROPERTIES', 'is_color', 'matches']

type Check = Callable[[Component], bool]

CSS_WIDE_KEYWORDS: Final = frozenset(('inherit', 'initial', 'unset', 'revert', 'revert-layer'))
LENGTH_UNITS: Final = frozenset(
    (
        'em', 'rem', 'ex', 'rex', 'cap', 'rcap', 'ch', 'rch', 'ic', 'ric', 'lh', 'rlh',
        'vw', 'vh', 'vi', 'vb', 'vmin', 'vmax', 'svw', 'svh', 'svi', 'svb', 'svmin', 'svmax',
        'lvw', 'lvh', 'lvi', 'lvb', 'lvmin', 'lvmax', 'dvw', 'dvh', 'dvi', 'dvb', 'dvmin', 'dvmax',
        'cqw', 'cqh', 'cqi', 'cqb', 'cqmin', 'cqmax', 'cm', 'mm', 'q', 'in', 'pt', 'pc', 'px',
    )
)  # fmt: skip
ANGLE_UNITS: Final = frozenset(('deg', 'grad', 'rad', 'turn'))
# Math functions give a number, length or percentage, depending on what is inside: they are taken
# to match any numeric type, as checking their insides is not worth it here.
MATH_FUNCTIONS: Final = frozenset(
    (
        'calc', '-webkit-calc', '-moz-calc', 'min', 'max', 'clamp', 'round', 'mod', 'rem', 'abs', 'sign',
        'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'atan2', 'pow', 'sqrt', 'hypot', 'log', 'exp',
    )
)  # fmt: skip
IMAGE_FUNCTIONS: Final = frozenset(
    (
        'linear-gradient', 'radial-gradient', 'conic-gradient', 'repeating-linear-gradient',
        'repeating-radial-gradient', 'repeating-conic-gradient', '-webkit-linear-gradient',
        '-webkit-radial-gradient', '-webkit-repeating-linear-gradient', '-webkit-repeating-radial-gradient',
        '-webkit-gradient', 'image-set', '-webkit-image-set', 'image', 'cross-fade', '-webkit-cross-fade',
        'element', '-moz-element', 'paint',
    )
)  # fmt: skip
# <system-color>, which are colors though parse_rgba() cannot give their RGB: they depend on the system
SYSTEM_COLORS: Final = frozenset(
    (
        'accentcolor', 'accentcolortext', 'activetext', 'buttonborder', 'buttonface', 'buttontext', 'canvas',
        'canvastext', 'field', 'fieldtext', 'graytext', 'highlight', 'highlighttext', 'linktext', 'mark',
        'marktext', 'selecteditem', 'selecteditemtext', 'visitedtext',
        # Deprecated, but still valid
        'activeborder', 'activecaption', 'appworkspace', 'background', 'buttonhighlight', 'buttonshadow',
        'captiontext', 'inactiveborder', 'inactivecaption', 'inactivecaptiontext', 'infobackground', 'infotext',
        'menu', 'menutext', 'scrollbar', 'threeddarkshadow', 'threedface', 'threedhighlight',
        'threedlightshadow', 'threedshadow', 'window', 'windowframe', 'windowtext',
    )
)  # fmt: skip
LINE_STYLES: Final = frozenset(
    ('none', 'hidden', 'dotted', 'dashed', 'solid', 'double', 'groove', 'ridge', 'inset', 'outset')
)
LINE_WIDTHS: Final = frozenset(('thin', 'medium', 'thick'))
# Words that cannot be a counter style name in list-style-type: they would make list-style ambiguous
NOT_COUNTER_STYLES: Final = CSS_WIDE_KEYWORDS | {'none', 'inside', 'outside', 'default'}
FONT_STYLES: Final = frozenset(('normal', 'italic', 'oblique'))
FONT_VARIANTS: Final = frozenset(('normal', 'none', 'small-caps', 'all-small-caps', 'petite-caps',
                                  'all-petite-caps', 'unicase', 'titling-caps'))  # fmt: skip
FONT_WEIGHTS: Final = frozenset(('normal', 'bold', 'bolder', 'lighter'))
FONT_STRETCHES: Final = frozenset(
    (
        'normal', 'ultra-condensed', 'extra-condensed', 'condensed', 'semi-condensed', 'semi-expanded', 'expanded',
        'extra-expanded', 'ultra-expanded',
    )
)  # fmt: skip
FONT_SIZES: Final = frozenset(
    ('xx-small', 'x-small', 'small', 'medium', 'large', 'x-large', 'xx-large', 'xxx-large', 'larger', 'smaller')
)


def keyword(c: Component) -> str | None:
    return c.value.lower() if isinstance(c, Ident) and c.value.isascii() else None


def is_math(c: Component) -> bool:
    return isinstance(c, Function) and c.name.lower() in MATH_FUNCTIONS


def is_length(c: Component, non_negative: bool = False) -> bool:
    if isinstance(c, Dimension):
        return c.unit.lower() in LENGTH_UNITS and not (non_negative and c.value < 0)
    if isinstance(c, Number):
        return c.value == 0  # a unitless zero is a length
    return is_math(c)


def is_length_percentage(c: Component, non_negative: bool = False) -> bool:
    if isinstance(c, Percentage):
        return not (non_negative and c.value < 0)
    return is_length(c, non_negative)


def is_number(c: Component, non_negative: bool = False) -> bool:
    if isinstance(c, Number):
        return not (non_negative and c.value < 0)
    return is_math(c)


def is_color_component(c: Component) -> bool:
    if (k := keyword(c)) is not None and k in SYSTEM_COLORS:
        return True
    return isinstance(c, Ident | Hash | Function) and parse_rgba(c.css_text) is not None


def is_image(c: Component) -> bool:
    return isinstance(c, Url) or (isinstance(c, Function) and c.name.lower() in IMAGE_FUNCTIONS)


def one_of(words: frozenset[str]) -> Check:
    def check(c: Component) -> bool:
        return keyword(c) in words

    return check


def either(*checks: Check) -> Check:
    def check(c: Component) -> bool:
        return any(f(c) for f in checks)

    return check


def line_width(c: Component) -> bool:
    return keyword(c) in LINE_WIDTHS or is_length(c, non_negative=True)


def counter_style(c: Component) -> bool:
    """<counter-style> or <string> or none, as list-style-type allows (CSS Lists 3)"""
    if isinstance(c, String) or (isinstance(c, Function) and c.name.lower() == 'symbols'):
        return True
    k = keyword(c)
    if k == 'none':
        return True
    return isinstance(c, Ident) and k not in NOT_COUNTER_STYLES and not c.value.startswith('--')


def font_weight(c: Component) -> bool:
    if keyword(c) in FONT_WEIGHTS:
        return True
    return is_math(c) or (isinstance(c, Number) and 1 <= c.value <= 1000)


def font_size(c: Component) -> bool:
    return keyword(c) in FONT_SIZES or keyword(c) == 'math' or is_length_percentage(c, non_negative=True)


def line_height(c: Component) -> bool:
    return keyword(c) == 'normal' or is_number(c, non_negative=True) or is_length_percentage(c, non_negative=True)


def font_stretch(c: Component) -> bool:
    return keyword(c) in FONT_STRETCHES or (isinstance(c, Percentage) and c.value >= 0) or is_math(c)


def single(check: Check) -> Callable[[Sequence[Component]], bool]:
    def match(items: Sequence[Component]) -> bool:
        return len(items) == 1 and check(items[0])

    return match


def font_style(items: Sequence[Component]) -> bool:
    if len(items) == 1:
        return keyword(items[0]) in FONT_STYLES
    # oblique <angle>
    return (
        len(items) == 2
        and keyword(items[0]) == 'oblique'
        and ((isinstance(items[1], Dimension) and items[1].unit.lower() in ANGLE_UNITS) or is_math(items[1]))
    )


def font_family(items: Sequence[Component]) -> bool:
    """[ <family-name> | <generic-family> ]#: each name is a string, or one or more idents other than the
    CSS-wide keywords and default"""
    names: list[list[Component]] = [[]]
    for c in items:
        if isinstance(c, Separator):
            names.append([])
        else:
            names[-1].append(c)
    for name in names:
        if len(name) == 1 and isinstance(name[0], String):
            continue
        if not name or not all(isinstance(c, Ident) and keyword(c) not in NOT_FAMILY_WORDS for c in name):
            return False
    return True


NOT_FAMILY_WORDS: Final = CSS_WIDE_KEYWORDS | {'default'}
EDGES: Final = ('top', 'right', 'bottom', 'left')
# The longhands, and how their whole value is matched. A value is the components of the value, with
# the comma separators kept, for font-family.
LONGHANDS: dict[str, Callable[[Sequence[Component]], bool]] = {
    **{f'margin-{e}': single(either(one_of(frozenset(('auto',))), is_length_percentage)) for e in EDGES},
    **{f'padding-{e}': single(lambda c: is_length_percentage(c, non_negative=True)) for e in EDGES},
    **{f'border-{e}-style': single(one_of(LINE_STYLES)) for e in EDGES},
    **{f'border-{e}-width': single(line_width) for e in EDGES},
    **{f'border-{e}-color': single(is_color_component) for e in EDGES},
    'list-style-type': single(counter_style),
    'list-style-position': single(one_of(frozenset(('inside', 'outside')))),
    'list-style-image': single(either(one_of(frozenset(('none',))), is_image)),
    'font-style': font_style,
    'font-variant': single(one_of(FONT_VARIANTS)),
    'font-weight': single(font_weight),
    'font-stretch': single(font_stretch),
    'font-size': single(font_size),
    'line-height': single(line_height),
    'font-family': font_family,
}
PROPERTIES: Final = frozenset(LONGHANDS)


def components(value: PropertyValue) -> list[Component]:
    # The commas of font-family matter; the slashes of other values are not in any of these grammars
    return [c for c in value.items if not isinstance(c, Separator) or c.value == ',']


@lru_cache(maxsize=4096)
def matches_text(name: str, text: str) -> bool | None:
    match = LONGHANDS.get(name)
    if match is None:
        return None
    items = components(parse_value(text))
    if len(items) == 1 and keyword(items[0]) in CSS_WIDE_KEYWORDS:
        return True
    if any(isinstance(c, Function) and c.name.lower() in ('var', 'env', 'attr') for c in items):
        # Not known until computed
        return False
    return match(items)


def matches(property_name: str, value: str | Component) -> bool | None:
    """Whether value is a valid value of the longhand property_name, or None if the property is not one of
    PROPERTIES. Property names are ASCII case-insensitive. A CSS-wide keyword, such as inherit, is valid for
    any of them. A value with var() is not: its value is unknown until computed. Results for text are cached."""
    name = property_name.lower()
    if isinstance(value, Component):
        value = value.css_text
    return matches_text(name, value)


def is_color(value: str | Component) -> bool:
    """Whether value is a color that parse_rgba() understands, including transparent and currentColor.
    CSS-wide keywords such as inherit are not colors."""
    return parse_rgba(value.css_text if isinstance(value, Component) else value) is not None
