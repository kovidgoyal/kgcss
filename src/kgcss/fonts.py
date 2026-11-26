# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Font family lists and the font shorthand.

These replace calibre's tinycss.fonts3 helpers. parse_font_family() and the serializers
give the same results; parse_font() follows the grammar of CSS Fonts 4, where tinycss
read what it could.

>>> parse_font_family('"Helvetica Neue", Arial, sans-serif')
['Helvetica Neue', 'Arial', 'sans-serif']
>>> serialize_font_family(['Helvetica Neue', 'Arial', 'SansSerif'])
'"Helvetica Neue", Arial, sans-serif'
>>> parse_font('bold 12pt/1.4 "A B", serif')
{'font-weight': 'bold', 'font-size': '12pt', 'line-height': '1.4', 'font-family': ['A B', 'serif']}
>>> serialize_font({'font-weight': 'bold', 'font-size': '12pt', 'font-family': ['A B', 'serif']})
'bold 12pt "A B", serif'
"""

import re
from collections.abc import Iterable, Sequence
from typing import Final, TypedDict, cast

from kgcss.cssom.values import Component, Ident, Separator, String, parse_value
from kgcss.escaping import serialize_string
from kgcss.grammar import (
    CSS_WIDE_KEYWORDS,
    FONT_SIZES,
    font_family,
    font_size,
    font_weight,
    is_angle,
    keyword,
    line_height,
)
from kgcss.syntax import Block, Token, parse_component_value_list
from kgcss.syntax import Function as SyntaxFunction
from kgcss.tokenizer import TokenType

__all__ = ['FontDict', 'parse_font', 'parse_font_family', 'serialize_font', 'serialize_font_family']

# Not closed (PEP 728), which would let type checkers merge it into a dict[str, str | list[str]]: TypedDict
# only takes closed= from Python 3.15, and ty does not use it for assignability yet
FontDict = TypedDict(
    'FontDict',
    {
        'font-style': str,
        'font-variant': str,
        'font-weight': str,
        'font-stretch': str,
        'font-size': str,
        'line-height': str,
        'font-family': list[str],
    },
    total=False,
)
FontDict.__doc__ = """The longhands of the font shorthand that a value sets, as parse_font() returns them."""

type Item = Token | Block | SyntaxFunction

STRETCH_KEYWORDS: Final = (
    'ultra-condensed', 'extra-condensed', 'condensed', 'semi-condensed', 'semi-expanded', 'expanded', 'extra-expanded',
    'ultra-expanded',
)  # fmt: skip
# The keywords that can come before the size in the font shorthand, other than normal and oblique, and what they set
BEFORE_SIZE_KEYWORDS: Final = {
    'italic': 'font-style', 'left': 'font-style', 'right': 'font-style', 'small-caps': 'font-variant',
    'bold': 'font-weight', 'bolder': 'font-weight', 'lighter': 'font-weight',
    **dict.fromkeys(STRETCH_KEYWORDS, 'font-stretch'),
}  # fmt: skip
SYSTEM_FONTS: Final = frozenset(('caption', 'icon', 'menu', 'message-box', 'small-caption', 'status-bar'))
GENERIC_FAMILIES: Final = frozenset(('serif', 'sans-serif', 'sansserif', 'cursive', 'fantasy', 'monospace'))
# Names that mean something else unquoted in font-family: the CSS-wide keywords, and default, which is reserved
RESERVED_NAMES: Final = CSS_WIDE_KEYWORDS | {'default'}
# Names that are keywords elsewhere in the font shorthand. Only the reserved names need quotes in its family
# list, but quoting these too keeps the value clear to readers and to parsers that are less strict.
FONT_KEYWORDS: Final = (
    RESERVED_NAMES | BEFORE_SIZE_KEYWORDS.keys() | FONT_SIZES | SYSTEM_FONTS | {'normal', 'oblique', 'math'}
)
SIMPLE_NAME: Final = re.compile(r'[a-zA-Z][a-zA-Z0-9_-]*')
IGNORED: Final = frozenset((TokenType.WHITESPACE, TokenType.COMMENT))


def family_names(items: Iterable[Item]) -> list[str]:
    """The names in a list of tokens: a string starts a name, idents are joined with spaces, commas
    separate names. Anything else is ignored."""
    families: list[str] = []
    current = ''
    for item in items:
        if not isinstance(item, Token):
            continue
        match item.type:
            case TokenType.STRING:
                if current and (name := current.strip()):
                    families.append(name)
                current = str(item.value)
            case TokenType.COMMA:
                if current and (name := current.strip()):
                    families.append(name)
                current = ''
            case TokenType.IDENT:
                current += ' ' + str(item.value)
    if current and (name := current.strip()):
        families.append(name)
    return families


def parse_font_family(text: str) -> list[str]:
    """The names of a font-family value: strings unquoted and unescaped, runs of idents joined with
    one space, escapes resolved, empty names dropped. Generic names are returned as written.

    A value that is a CSS-wide keyword is not a list of names, but this gives it as one: both
    ``inherit`` and ``"inherit"`` give ``['inherit']``. Check for the keywords first with
    :data:`CSS_WIDE_KEYWORDS`, as in ``text.strip().lower() in CSS_WIDE_KEYWORDS``."""
    return family_names(parse_component_value_list(text))


def serialize_single_font_family(name: str) -> str:
    lower = name.lower()
    if lower in GENERIC_FAMILIES:
        return 'sans-serif' if lower == 'sansserif' else lower
    if SIMPLE_NAME.fullmatch(name) is not None and lower not in RESERVED_NAMES:
        return name
    return serialize_string(name)


def serialize_font_family(names: Iterable[str]) -> str:
    """A font-family value: generic names lowercased, other names that are a single simple
    identifier as they are, the rest quoted."""
    return ', '.join(map(serialize_single_font_family, names))


def parse_font(text: str) -> FontDict:
    """The longhands that a value of the font shorthand sets, with only the keys that it sets, as in
    CSS Fonts 4::

        [ [ <'font-style'> || <font-variant-css2> || <'font-weight'> || <font-width-css3> ]?
          <'font-size'> [ / <'line-height'> ]? <'font-family'># ] | <system-font-family-name>

    Keywords are lowercased, other values are as written. A normal before the size sets the first of
    style, variant, weight and stretch that no other value sets. A system font, such as caption, gives
    {'font-family': ['sans-serif']}, as kgcss cannot know the system's fonts.

    A value that does not match the grammar gives {}, as CSS ignores the whole declaration. So does a
    value with var() and the like, which is not known before computing, and one that is a CSS-wide
    keyword: that sets every longhand, but is not a font. Check for those first with
    :data:`CSS_WIDE_KEYWORDS`. A math function is typed by its contents: calc(2px / 1px) is taken to
    be a length."""
    items = parse_value(text).items
    if not items:
        return {}
    if len(items) == 1 and keyword(items[0]) in SYSTEM_FONTS:
        return {'font-family': ['sans-serif']}
    found: dict[str, str] = {}
    normals = pos = 0
    while pos < len(items):
        c = items[pos]
        k = keyword(c)
        if k == 'normal':
            normals += 1
        elif k == 'oblique' and 'font-style' not in found:
            if pos + 1 < len(items) and is_angle(items[pos + 1], 90):
                pos += 1
                found['font-style'] = f'oblique {items[pos].css_text}'
            else:
                found['font-style'] = k
        elif (name := before_size_property(c, k)) is not None and name not in found:
            found[name] = c.css_text if k is None else k
        else:
            break
        pos += 1
    if len(found) + normals > 4:
        return {}
    ans: dict[str, str | list[str]] = {}
    for name in BEFORE_SIZE:
        if name in found:
            ans[name] = found[name]
        elif normals:
            normals -= 1
            ans[name] = 'normal'
    if pos >= len(items) or not font_size(size := items[pos]):
        return {}
    ans['font-size'] = size.css_text if (k := keyword(size)) is None else k
    pos += 1
    if pos < len(items) and isinstance(items[pos], Separator) and items[pos].css_text == '/':
        if pos + 1 >= len(items) or not line_height(height := items[pos + 1]):
            return {}
        ans['line-height'] = height.css_text if (k := keyword(height)) is None else k
        pos += 2
    family = items[pos:]
    if not family or not font_family(family):
        return {}
    ans['font-family'] = [
        name.value if isinstance(name, String) else ' '.join(cast('Ident', c).value for c in name)
        for name in split_families(family)
    ]
    return cast('FontDict', ans)


BEFORE_SIZE: Final = ('font-style', 'font-variant', 'font-weight', 'font-stretch')


def before_size_property(c: Component, k: str | None) -> str | None:
    """The longhand that c can set before the size, other than with normal"""
    if k is not None:
        return BEFORE_SIZE_KEYWORDS.get(k)
    return 'font-weight' if font_weight(c) else None


def split_families(items: Sequence[Component]) -> list[String | list[Component]]:
    """The names of a font-family list that matches the grammar: a string or a list of idents each"""
    ans: list[String | list[Component]] = [[]]
    for c in items:
        if isinstance(c, Separator):
            ans.append([])
        elif isinstance(c, String):
            ans[-1] = c
        else:
            cast('list[Component]', ans[-1]).append(c)
    return ans


def serialize_font_family_in_font(names: Sequence[str]) -> str:
    # A name that is a keyword elsewhere in the shorthand is quoted, see FONT_KEYWORDS
    return ', '.join(
        serialize_string(n) if n.lower() in FONT_KEYWORDS else serialize_single_font_family(n) for n in names
    )


def serialize_font(font: FontDict) -> str:
    """A value of the font shorthand: style, variant, weight, stretch, size[/line-height] and the
    families, those that are given. parse_font(serialize_font(d)) == d for what parse_font() returns."""
    parts = [v for k in ('font-style', 'font-variant', 'font-weight', 'font-stretch') if (v := font.get(k)) is not None]
    if (size := font.get('font-size')) is not None:
        height = font.get('line-height')
        parts.append(size if height is None else f'{size}/{height}')
    if families := font.get('font-family'):
        parts.append(serialize_font_family_in_font(families))
    return ' '.join(parts)
