# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""findFontFamily() of stylelint's lib/utils, which the font-family rules share."""

import re
from typing import Final

from kgcss._css_data import (
    BASIC_KEYWORDS,
    FONT_FAMILY_KEYWORDS,
    FONT_SHORTHAND_KEYWORDS,
    FONT_SIZE_KEYWORDS,
    LENGTH_UNITS,
)
from kgcss.linter._utils import is_numbery, is_standard_syntax_value, is_variable
from kgcss.linter._value_parser import ValueNode, parse, unit

RE_FONT_FAMILY_PROPERTY: Final = re.compile(r'^font(-family)?$', re.IGNORECASE)
NODE_TYPES: Final = frozenset({'word', 'string', 'space', 'div'})


def is_valid_font_size(word: str) -> bool:
    """isValidFontSize()."""
    if not word:
        return False
    if word in FONT_SIZE_KEYWORDS:
        return True
    number = unit(word)
    if number is None:
        return False
    return number[1] == '%' or number[1].lower() in LENGTH_UNITS


def is_comma(node: ValueNode | None) -> bool:
    return node is not None and node.type == 'div' and node.value == ','


def find_font_family(value: str) -> list[ValueNode]:
    """The font family names of a font or font-family value, a name of several words joined into one node."""
    nodes = parse(value)
    if len(nodes) == 1 and nodes[0].value.lower() in BASIC_KEYWORDS:
        return [nodes[0]]
    families: list[ValueNode] = []
    last: ValueNode | None = None
    merge: str | None = None
    # Functions are skipped with their arguments, so only the top level is walked
    for index, node in enumerate(nodes):
        if node.type == 'function':
            continue
        if node.type == 'comment':  # a comment separates words as a space does, but not after a slash
            if merge is None:
                merge = ' '
            continue
        if node.type not in NODE_TYPES:
            continue
        lower = node.value.lower()
        if not is_standard_syntax_value(lower) or is_variable(lower):
            continue
        is_keyword = lower in FONT_FAMILY_KEYWORDS
        if not is_keyword and (lower in FONT_SHORTHAND_KEYWORDS or is_valid_font_size(node.value)):
            continue
        prev = nodes[index - 1] if index >= 1 else None
        prev_prev = nodes[index - 2] if index >= 2 else None
        if is_keyword and lower in FONT_SIZE_KEYWORDS:  # math is a family and a size
            following = next((n for n in nodes[index + 1 :] if n.type not in ('space', 'comment')), None)
            if following is not None and not is_comma(following) and not any(is_comma(n) for n in nodes[:index]):
                continue
        if prev is not None and prev.value == '/' and prev_prev is not None and is_valid_font_size(prev_prev.value):
            continue  # a line height
        if is_numbery(lower):
            continue
        if is_comma(node):
            last = None
            continue
        if node.type in ('space', 'div'):  # a name may span several nodes: Lucida Grande, Red/Black
            merge = node.value
            continue
        if last is not None and merge is not None:
            last.value = last.value + merge + node.value
            last.source_end_index = node.source_end_index
        else:
            last = node
            families.append(node)
        merge = None
    return families
