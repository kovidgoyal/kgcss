# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Helpers for running the vendored css-parsing-tests conformance suite.

The suite's JSON representation is documented in css-parsing-tests/README.rst.
"""

import json
from collections.abc import Iterable, Iterator
from pathlib import Path

from kgcss import ParseError
from kgcss.syntax import AtRule, Block, ComponentValueList, Declaration, Function, NodeList, QualifiedRule
from kgcss.tokenizer import Token, TokenType

SUITE_DIR = Path(__file__).parent / 'css-parsing-tests'

type JSON = str | int | float | bool | list[JSON] | dict[str, JSON] | None

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


def item_to_json(item: Token | Block | Function) -> list[JSON]:
    """The suite representation of one component value. Comments produce nothing, and strings and urls closed
    by EOF are followed by an error marker, so this returns zero, one or two items."""
    match item:
        case Block():
            return [[item.opener + {'{': '}', '[': ']', '(': ')'}[item.opener], *values_to_json(item.content)]]
        case Function():
            return [['function', item.name, *values_to_json(item.arguments)]]
        case Token(type=TokenType.COMMENT):
            return []
    ans = [token_to_json(item)]
    if not item.closed and (marker := EOF_MARKERS.get(item.type)):
        ans.append(['error', marker])
    return ans


def values_to_json(values: ComponentValueList) -> list[JSON]:
    """Convert component values grouped by the C code to the suite representation."""
    return [x for item in values for x in item_to_json(item)]


def group_tokens(tokens: Iterable[Token]) -> list[JSON]:
    """Independent Python implementation of 'consume a list of component values', used to cross-check the C
    grouping."""
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


# Error kinds the suite represents as ["error", "invalid"]
INVALID_KINDS = frozenset(('invalid-rule', 'invalid-declaration'))


def node_to_json(node: AtRule | QualifiedRule | Declaration | ParseError) -> JSON:
    match node:
        case AtRule():
            block = None if node.block is None else values_to_json(node.block.content)
            return ['at-rule', node.name, values_to_json(node.prelude), block]
        case QualifiedRule():
            return ['qualified rule', values_to_json(node.prelude), values_to_json(node.block.content)]
        case Declaration():
            return ['declaration', node.name, values_to_json(node.value), node.important]
    return ['error', 'invalid' if node.kind in INVALID_KINDS else node.kind]


def node_list_to_json(nodes: NodeList) -> list[JSON]:
    """The suite puts errors inline, at the position of the construct that was dropped."""
    errors = [e for e in nodes.errors if e.kind in INVALID_KINDS]
    out: list[JSON] = []
    for node in nodes:
        if isinstance(node, Token):
            assert node.type is TokenType.COMMENT
            continue
        while errors and errors[0].offset < node.pos:
            out.append(node_to_json(errors.pop(0)))
        out.append(node_to_json(node))
    out.extend(node_to_json(e) for e in errors)
    return out


def strip_whitespace(values: JSON) -> JSON:
    assert isinstance(values, list)
    start, end = 0, len(values)
    while start < end and values[start] == ' ':
        start += 1
    while end > start and values[end - 1] == ' ':
        end -= 1
    return values[start:end]


def normalize_declarations(value: JSON) -> JSON:
    """The suite keeps whitespace around declaration values. The current CSS
    Syntax ED trims it (consume a declaration, steps 4 and 7)."""
    if isinstance(value, list) and value and value[0] == 'declaration':
        return [*value[:2], strip_whitespace(value[2]), *value[3:]]
    if isinstance(value, list) and value and isinstance(value[0], list):
        return [normalize_declarations(x) for x in value]
    return value
