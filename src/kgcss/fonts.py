# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Font family lists and the font shorthand.

These replace calibre's tinycss.fonts3 helpers, with the same results, on kgcss's
tokenizer.

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
from typing import Final, TypedDict

from kgcss.escaping import serialize_string
from kgcss.syntax import Block, Token, parse_component_value_list
from kgcss.syntax import Function as SyntaxFunction
from kgcss.tokenizer import TokenType

__all__ = ['FontDict', 'parse_font', 'parse_font_family', 'serialize_font', 'serialize_font_family']

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

GLOBAL_IDENTS: Final = frozenset(('inherit', 'initial', 'unset', 'normal'))
STYLE_IDENTS: Final = frozenset(('italic', 'oblique'))
VARIANT_IDENTS: Final = frozenset(('small-caps',))
WEIGHT_IDENTS: Final = frozenset(('bold', 'bolder', 'lighter'))
STRETCH_IDENTS: Final = frozenset(
    [
        'ultra-condensed',
        'extra-condensed',
        'condensed',
        'semi-condensed',
        'semi-expanded',
        'expanded',
        'extra-expanded',
        'ultra-expanded',
    ]
)
BEFORE_SIZE_IDENTS: Final = STYLE_IDENTS | VARIANT_IDENTS | WEIGHT_IDENTS | STRETCH_IDENTS
SIZE_IDENTS: Final = frozenset(
    ['xx-small', 'x-small', 'small', 'medium', 'large', 'x-large', 'xx-large', 'larger', 'smaller']
)
WEIGHT_SIZES: Final = frozenset(range(100, 1000, 100))
LEGACY_FONTS: Final = frozenset(('caption', 'icon', 'menu', 'message-box', 'small-caption', 'status-bar'))
GENERIC_FAMILIES: Final = frozenset(('serif', 'sans-serif', 'sansserif', 'cursive', 'fantasy', 'monospace'))
# Names that mean something else unquoted in font-family: the CSS-wide keywords, and default, which is reserved
RESERVED_NAMES: Final = frozenset(('inherit', 'initial', 'unset', 'revert', 'revert-layer', 'default'))
# Names that mean something else unquoted in the font shorthand
FONT_KEYWORDS: Final = GLOBAL_IDENTS | BEFORE_SIZE_IDENTS | SIZE_IDENTS | LEGACY_FONTS
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
    one space, escapes resolved, empty names dropped. Generic names are returned as written."""
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


def keyword(item: Token) -> str | None:
    return str(item.value).lower() if item.type is TokenType.IDENT else None


def parse_font(text: str) -> FontDict:
    """The longhands that a value of the font shorthand sets, as in CSS Fonts 3, with only the keys
    that it sets. Keywords are lowercased. The legacy system fonts, such as caption, give
    {'font-family': ['sans-serif']}. Values that do not parse give what could be read before the
    problem."""
    style = variant = weight = stretch = size = height = None
    items: list[Item] = [
        x for x in parse_component_value_list(text) if not (isinstance(x, Token) and x.type in IGNORED)
    ]
    if items and isinstance(items[0], Token) and keyword(items[0]) in LEGACY_FONTS:
        return {'font-family': ['sans-serif']}
    # As tinycss does: take the items before the family one by one, then the rest are the family
    pos = 0
    while pos < len(items):
        tok = items[pos]
        pos += 1
        if not isinstance(tok, Token):
            continue  # functions and blocks are skipped
        t = tok.type
        if t is TokenType.STRING:
            pos -= 1
            break
        if t is TokenType.NUMBER and tok.is_integer:
            if size is None:
                if weight is None and tok.value in WEIGHT_SIZES:
                    weight = tok.source_text
                    continue
                break
            if height is None:
                height = tok.source_text
            break
        if t is TokenType.NUMBER:
            if size is not None and height is None:
                height = tok.source_text
            break
        if t is TokenType.DELIM or t is TokenType.COMMA:
            if tok.source_text == '/' and size is not None and height is None:
                continue
            break
        if t is TokenType.DIMENSION or t is TokenType.PERCENTAGE:
            if size is None:
                size = tok.source_text
                continue
            if height is None:
                height = tok.source_text
            break
        if (word := keyword(tok)) is not None:
            if word in GLOBAL_IDENTS:
                if size is not None:
                    if height is None:
                        height = word
                    else:
                        pos -= 1
                    break
                if style is None:
                    style = word
                elif variant is None:
                    variant = word
                elif weight is None:
                    weight = word
                elif stretch is None:
                    stretch = word
                else:
                    # size is None here: tinycss's branches for line-height after this could not be reached
                    size = word
                continue
            if word in BEFORE_SIZE_IDENTS:
                if size is not None:
                    break
                if word in STYLE_IDENTS:
                    style = word
                elif word in VARIANT_IDENTS:
                    variant = word
                elif word in WEIGHT_IDENTS:
                    weight = word
                else:
                    stretch = word
            elif word in SIZE_IDENTS:
                size = word
            else:
                pos -= 1
                break
    ans: FontDict = {}
    if style is not None:
        ans['font-style'] = style
    if variant is not None:
        ans['font-variant'] = variant
    if weight is not None:
        ans['font-weight'] = weight
    if stretch is not None:
        ans['font-stretch'] = stretch
    if size is not None:
        ans['font-size'] = size
    if height is not None:
        ans['line-height'] = height
    if families := family_names(items[pos:]):
        ans['font-family'] = families
    return ans


def serialize_font_family_in_font(names: Sequence[str]) -> str:
    # In the shorthand, a name that is a font keyword would be read as the keyword: quote it
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
    return ' '.join(parts)  # type: ignore[arg-type]
