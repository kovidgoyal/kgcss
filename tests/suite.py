# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Helpers for running the vendored css-parsing-tests conformance suite.

The suite's JSON representation is documented in css-parsing-tests/README.rst.
"""

import json
from collections.abc import Iterable, Iterator
from pathlib import Path

from kgcss import Token, TokenType

SUITE_DIR = Path(__file__).parent / 'css-parsing-tests'

type JSON = str | int | float | bool | list[JSON] | None

# The suite predates the removal of these tokens from CSS Syntax 3. Each is
# equivalent to the two DELIM tokens it is made of.
MATCH_TOKENS = frozenset(('~=', '|=', '^=', '$=', '*=', '||'))

OPENERS: dict[TokenType, tuple[str, TokenType]] = {
    TokenType.LEFT_CURLY: ('{}', TokenType.RIGHT_CURLY),
    TokenType.LEFT_SQUARE: ('[]', TokenType.RIGHT_SQUARE),
    TokenType.LEFT_PAREN: ('()', TokenType.RIGHT_PAREN),
    TokenType.FUNCTION: ('function', TokenType.RIGHT_PAREN),
}
CLOSERS = frozenset((TokenType.RIGHT_CURLY, TokenType.RIGHT_SQUARE, TokenType.RIGHT_PAREN))
# The suite follows strings and urls closed by EOF with an error marker
EOF_MARKERS: dict[TokenType, str] = {TokenType.STRING: 'eof-in-string', TokenType.URL: 'eof-in-url'}
LITERALS: dict[TokenType, str] = {
    TokenType.WHITESPACE: ' ',
    TokenType.CDO: '<!--',
    TokenType.CDC: '-->',
    TokenType.COLON: ':',
    TokenType.SEMICOLON: ';',
    TokenType.COMMA: ',',
}


def load_cases(name: str) -> list[tuple[str, JSON]]:
    data = json.loads((SUITE_DIR / name).read_text(encoding='utf-8'))
    assert isinstance(data, list)
    return [(data[i], data[i + 1]) for i in range(0, len(data), 2)]


def normalize_expected(value: JSON) -> JSON:
    """Split the obsolete match tokens into their two DELIMs."""
    if not isinstance(value, list):
        return value
    out: list[JSON] = []
    for item in value:
        if isinstance(item, str) and item in MATCH_TOKENS:
            out.extend(item)
        else:
            out.append(normalize_expected(item))
    return out


def token_to_json(tok: Token) -> JSON:
    t, v = tok.type, tok.value
    match t:
        case TokenType.IDENT:
            return ['ident', v]
        case TokenType.AT_KEYWORD:
            return ['at-keyword', v]
        case TokenType.HASH:
            return ['hash', v, 'id' if tok.hash_is_id else 'unrestricted']
        case TokenType.STRING:
            return ['string', v]
        case TokenType.URL:
            return ['url', v]
        case TokenType.BAD_STRING:
            return ['error', 'bad-string']
        case TokenType.BAD_URL:
            return ['error', 'bad-url']
        case TokenType.DELIM:
            return v
        case TokenType.NUMBER | TokenType.PERCENTAGE | TokenType.DIMENSION:
            ans: list[JSON] = [t.name.lower(), tok.representation, v, 'integer' if tok.is_integer else 'number']
            if t is TokenType.DIMENSION:
                ans.append(tok.unit)
            return ans
        case TokenType.RIGHT_CURLY | TokenType.RIGHT_SQUARE | TokenType.RIGHT_PAREN:
            return ['error', tok.source_text]
    return LITERALS[t]


def group_tokens(tokens: Iterable[Token]) -> list[JSON]:
    """Reference implementation of 'consume a list of component values' (to be replaced by the C grouping)."""
    it: Iterator[Token] = iter(tokens)

    def consume_block(closer: TokenType | None) -> list[JSON]:
        out: list[JSON] = []
        for tok in it:
            if tok.type is closer:
                return out
            if (spec := OPENERS.get(tok.type)) is not None:
                head: list[JSON] = [spec[0]]
                if tok.type is TokenType.FUNCTION:
                    head.append(tok.value)
                out.append(head + consume_block(spec[1]))
            elif tok.type is TokenType.COMMENT:
                continue
            else:
                out.append(token_to_json(tok))
                if not tok.closed and (marker := EOF_MARKERS.get(tok.type)):
                    out.append(['error', marker])
        return out

    return consume_block(None)
