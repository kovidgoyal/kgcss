# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The CSS Syntax Level 3 tokenizer.

All the work is done in C, see csrc/tokenizer_impl.h. Token values are
unescaped, every token knows its exact source text and position, and
tokenizing never raises on bad CSS; problems are recorded as ParseError
objects in TokenStream.errors.
"""

from kgcss._kgcss import ParseError, Token, TokenStream, tokenize
from kgcss.enums import TokenType

__all__ = ['ParseError', 'Token', 'TokenStream', 'TokenType', 'tokenize']
