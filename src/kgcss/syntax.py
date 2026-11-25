# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Component values and the generic CSS Syntax Level 3 parse algorithms.

Blocks and functions are resolved by the tokenizer in the same pass, so
ComponentValueList, Block and Function are cheap views over a TokenStream:
items are only turned into Python objects when they are accessed.
"""

from kgcss._kgcss import (
    Block,
    ComponentValueList,
    Function,
    ParseError,
    Token,
    parse_component_value_list,
    parse_one_component_value,
)

type ComponentValue = Token | Block | Function

__all__ = [
    'Block',
    'ComponentValue',
    'ComponentValueList',
    'Function',
    'ParseError',
    'parse_component_value_list',
    'parse_one_component_value',
]
