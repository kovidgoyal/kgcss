# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""kgcss: fast and comprehensive CSS handling with a C99 backend."""

from kgcss._version import __version__
from kgcss.serialize import serialize_identifier, serialize_string
from kgcss.tokenizer import ParseError, Token, TokenStream, TokenType, tokenize

__all__ = [
    'ParseError',
    'Token',
    'TokenStream',
    'TokenType',
    '__version__',
    'serialize_identifier',
    'serialize_string',
    'tokenize',
]
