# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""kgcss: fast and comprehensive CSS handling with a C99 backend."""

from kgcss._version import __version__
from kgcss.serialize import serialize_identifier, serialize_string
from kgcss.syntax import (
    Block,
    ComponentValue,
    ComponentValueList,
    Function,
    parse_component_value_list,
    parse_one_component_value,
)
from kgcss.tokenizer import ParseError, Token, TokenStream, TokenType, tokenize

__all__ = [
    'Block',
    'ComponentValue',
    'ComponentValueList',
    'Function',
    'ParseError',
    'Token',
    'TokenStream',
    'TokenType',
    '__version__',
    'parse_component_value_list',
    'parse_one_component_value',
    'serialize_identifier',
    'serialize_string',
    'tokenize',
]
