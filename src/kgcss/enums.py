# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Enumerations shared with the C extension.

This module must not import anything from kgcss, since the C extension
imports it while it is being initialised.
"""

from enum import IntEnum, unique


@unique
class TokenType(IntEnum):
    """The type of a Token. Values match kg_token_type in csrc/tokens.h."""

    IDENT = 1
    FUNCTION = 2
    AT_KEYWORD = 3
    HASH = 4
    STRING = 5
    BAD_STRING = 6
    URL = 7
    BAD_URL = 8
    DELIM = 9
    NUMBER = 10
    PERCENTAGE = 11
    DIMENSION = 12
    WHITESPACE = 13
    CDO = 14
    CDC = 15
    COLON = 16
    SEMICOLON = 17
    COMMA = 18
    LEFT_SQUARE = 19
    RIGHT_SQUARE = 20
    LEFT_PAREN = 21
    RIGHT_PAREN = 22
    LEFT_CURLY = 23
    RIGHT_CURLY = 24
    COMMENT = 25
