# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""A port of @csstools/css-tokenizer 4.0.3 and @csstools/css-parser-algorithms 4.0.3.

stylelint's rules tokenize and parse values with these libraries, mutate the
tokens and nodes, and serialize them again. The tokens here have the shape of
@csstools' tokens: a mutable CSSToken with the type, the raw text, the start
and the inclusive end, and the data (value, unit, sign character, number or
hash type) that @csstools keeps in the fifth element of its token arrays.

The tokens come from kgcss's own tokenizer, so offsets are Python string
indexes (code points), not the UTF-16 code units of JavaScript. They are
adjusted where @csstools differs from it:

- @csstools follows the current CSS Syntax draft, in which some code points
  from U+0080 (U+00D7, U+00F7, general punctuation, arrows, private use...)
  are not ident code points; kgcss follows the 2021 CR, in which all are.
- The whitespace between url( and a quote is a whitespace token in
  @csstools, and part of the function token in kgcss.
- @csstools checks for non-printable code points in an unquoted url before
  it replaces NUL, so a NUL makes the url a bad-url (CSS Syntax replaces NUL
  first, as kgcss does).
- Numbers are JavaScript numbers: floats, with -0 for '-0'.
- The parse errors that @csstools reports are worked out from the tokens.

Unicode-range tokens (unicodeRangesAllowed) are not supported.

The JavaScript behaviours that rule ports must know about are kept:

- A function or block that is not closed before the end of the input, inside
  another one that is not closed either, has no end token: end_token is None,
  None is in its tokens(), and to_string() and stringify() raise TypeError as
  the JavaScript does.
- walk() does not return what the callback returns: the callback returning
  False only stops the walk of the list that the node is in.
- The state of a walk is shallow-copied for each child, and the callback can
  change the copy that the descendants of its node get.
"""

import copy
import math
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from enum import StrEnum
from typing import Final, Literal, NamedTuple, TypeIs

from kgcss.enums import TokenType as KgTokenType
from kgcss.tokenizer import Token as KgToken
from kgcss.tokenizer import tokenize as kg_tokenize

# css-tokenizer {{{


class TokenType(StrEnum):
    """The type of a token, with the names that @csstools gives them."""

    COMMENT = 'comment'
    AT_KEYWORD = 'at-keyword-token'
    BAD_STRING = 'bad-string-token'
    BAD_URL = 'bad-url-token'
    CDC = 'CDC-token'
    CDO = 'CDO-token'
    COLON = 'colon-token'
    COMMA = 'comma-token'
    DELIM = 'delim-token'
    DIMENSION = 'dimension-token'
    EOF = 'EOF-token'
    FUNCTION = 'function-token'
    HASH = 'hash-token'
    IDENT = 'ident-token'
    NUMBER = 'number-token'
    PERCENTAGE = 'percentage-token'
    SEMICOLON = 'semicolon-token'
    STRING = 'string-token'
    URL = 'url-token'
    WHITESPACE = 'whitespace-token'
    OPEN_PAREN = '(-token'
    CLOSE_PAREN = ')-token'
    OPEN_SQUARE = '[-token'
    CLOSE_SQUARE = ']-token'
    OPEN_CURLY = '{-token'
    CLOSE_CURLY = '}-token'
    UNICODE_RANGE = 'unicode-range-token'


class NumberType(StrEnum):
    """Whether a number was written as an integer."""

    INTEGER = 'integer'
    NUMBER = 'number'


class HashType(StrEnum):
    """Whether a hash token would be a valid ID selector."""

    UNRESTRICTED = 'unrestricted'
    ID = 'id'


class ValueData:
    """The data of an ident, function, at-keyword, string, url or delim token."""

    __slots__ = ('value',)

    def __init__(self, value: str) -> None:
        self.value = value

    def __repr__(self) -> str:
        return f'ValueData({self.value!r})'


class NumericData:
    """The data of a number, percentage or dimension token.

    type is None for a percentage, as is unit for a number or percentage.
    sign_character is '+' or '-' if the number was written with a sign.
    """

    __slots__ = ('sign_character', 'type', 'unit', 'value')

    def __init__(
        self, value: float, type: NumberType | None, sign_character: str | None, unit: str | None = None
    ) -> None:
        self.value = value
        self.type = type
        self.sign_character = sign_character
        self.unit = unit

    def __repr__(self) -> str:
        return f'NumericData({self.value!r}, {self.type}, {self.sign_character!r}, {self.unit!r})'


class HashData:
    """The data of a hash token."""

    __slots__ = ('type', 'value')

    def __init__(self, value: str, type: HashType) -> None:
        self.value = value
        self.type = type

    def __repr__(self) -> str:
        return f'HashData({self.value!r}, {self.type})'


type TokenData = ValueData | NumericData | HashData


class CSSToken:
    """A token: @csstools' [type, raw, start, end, data] array.

    raw is the text of the token, start the index of its first character and
    end the index of its last character (inclusive), -1 for tokens that were
    not in the source. data is None for the tokens that have no data in
    @csstools. Tokens compare by identity, as JavaScript arrays do.
    """

    __slots__ = ('data', 'end', 'raw', 'start', 'type')

    def __init__(self, type: TokenType, raw: str, start: int, end: int, data: TokenData | None = None) -> None:
        self.type = type
        self.raw = raw
        self.start = start
        self.end = end
        self.data = data

    def __repr__(self) -> str:
        return f'CSSToken({self.type.value!r}, {self.raw!r}, {self.start}, {self.end}, {self.data!r})'

    @property
    def value_data(self) -> ValueData:
        """The data of a token that has a string value; raises TypeError for other tokens."""
        if isinstance(self.data, ValueData):
            return self.data
        raise TypeError(f'{self.type} has no ValueData')

    @property
    def numeric_data(self) -> NumericData:
        """The data of a numeric token; raises TypeError for other tokens."""
        if isinstance(self.data, NumericData):
            return self.data
        raise TypeError(f'{self.type} has no NumericData')

    @property
    def hash_data(self) -> HashData:
        """The data of a hash token; raises TypeError for other tokens."""
        if isinstance(self.data, HashData):
            return self.data
        raise TypeError(f'{self.type} has no HashData')

    @property
    def string_value(self) -> str:
        """The value of an ident, function, at-keyword, string, url, delim or hash token."""
        if isinstance(self.data, (ValueData, HashData)):
            return self.data.value
        raise TypeError(f'{self.type} has no string value')


class ParseErrorMessage:
    """The messages of the parse errors of the tokenizer."""

    UNEXPECTED_NEW_LINE_IN_STRING: Final = 'Unexpected newline while consuming a string token.'
    UNEXPECTED_EOF_IN_STRING: Final = 'Unexpected EOF while consuming a string token.'
    UNEXPECTED_EOF_IN_COMMENT: Final = 'Unexpected EOF while consuming a comment.'
    UNEXPECTED_EOF_IN_URL: Final = 'Unexpected EOF while consuming a url token.'
    UNEXPECTED_EOF_IN_ESCAPED_CODE_POINT: Final = 'Unexpected EOF while consuming an escaped code point.'
    UNEXPECTED_CHARACTER_IN_URL: Final = 'Unexpected character while consuming a url token.'
    INVALID_ESCAPE_SEQUENCE_IN_URL: Final = 'Invalid escape sequence while consuming a url token.'
    INVALID_ESCAPE_SEQUENCE_AFTER_BACKSLASH: Final = 'Invalid escape sequence after "\\"'


class ParseError(Exception):
    """A parse error, given to the on_parse_error callback rather than raised."""

    def __init__(self, message: str, source_start: int, source_end: int, parser_state: list[str]) -> None:
        super().__init__(message)
        self.message = message
        self.source_start = source_start
        self.source_end = source_end
        self.parser_state = parser_state


class ParseErrorWithToken(ParseError):
    """A parse error about a token."""

    def __init__(
        self, message: str, source_start: int, source_end: int, parser_state: list[str], token: CSSToken
    ) -> None:
        super().__init__(message, source_start, source_end, parser_state)
        self.token = token


type OnParseError = Callable[[ParseError], object]

_TYPES: Final = {
    KgTokenType.IDENT: TokenType.IDENT,
    KgTokenType.FUNCTION: TokenType.FUNCTION,
    KgTokenType.AT_KEYWORD: TokenType.AT_KEYWORD,
    KgTokenType.HASH: TokenType.HASH,
    KgTokenType.STRING: TokenType.STRING,
    KgTokenType.BAD_STRING: TokenType.BAD_STRING,
    KgTokenType.URL: TokenType.URL,
    KgTokenType.BAD_URL: TokenType.BAD_URL,
    KgTokenType.DELIM: TokenType.DELIM,
    KgTokenType.NUMBER: TokenType.NUMBER,
    KgTokenType.PERCENTAGE: TokenType.PERCENTAGE,
    KgTokenType.DIMENSION: TokenType.DIMENSION,
    KgTokenType.WHITESPACE: TokenType.WHITESPACE,
    KgTokenType.CDO: TokenType.CDO,
    KgTokenType.CDC: TokenType.CDC,
    KgTokenType.COLON: TokenType.COLON,
    KgTokenType.SEMICOLON: TokenType.SEMICOLON,
    KgTokenType.COMMA: TokenType.COMMA,
    KgTokenType.LEFT_SQUARE: TokenType.OPEN_SQUARE,
    KgTokenType.RIGHT_SQUARE: TokenType.CLOSE_SQUARE,
    KgTokenType.LEFT_PAREN: TokenType.OPEN_PAREN,
    KgTokenType.RIGHT_PAREN: TokenType.CLOSE_PAREN,
    KgTokenType.LEFT_CURLY: TokenType.OPEN_CURLY,
    KgTokenType.RIGHT_CURLY: TokenType.CLOSE_CURLY,
    KgTokenType.COMMENT: TokenType.COMMENT,
}
_VALUE_TYPES: Final = frozenset({
    TokenType.IDENT, TokenType.FUNCTION, TokenType.AT_KEYWORD, TokenType.STRING, TokenType.URL, TokenType.DELIM
})  # fmt: skip
_CSS_WHITESPACE: Final = ' \t\n\r\f'
_NEWLINES: Final = '\n\r\f'
_HEX_DIGITS: Final = frozenset('0123456789abcdefABCDEF')


def _is_non_printable(ch: str) -> bool:
    c = ord(ch)
    return c == 0x0B or c == 0x7F or 0 <= c <= 8 or 0x0E <= c <= 0x1F


def _skip_escape(text: str, i: int) -> int:
    """The index after the escaped code point that starts at i, just after a backslash."""
    if i >= len(text):
        return i
    if text[i] in _HEX_DIGITS:
        n = 1
        i += 1
        while i < len(text) and n < 6 and text[i] in _HEX_DIGITS:
            i += 1
            n += 1
        if i < len(text) and text[i] in _CSS_WHITESPACE:
            i += 2 if text.startswith('\r\n', i) else 1
        return i
    return i + 1


def _url_error(raw: str) -> str | Literal[False] | None:
    """How @csstools reads the url( token raw.

    False if it is a url-token, otherwise the message of its bad-url-token
    error or None for a bad-url-token without an error. Unlike CSS Syntax and
    kgcss, @csstools does not replace NUL before it checks for non-printable
    code points, so a NUL makes a url bad.
    """
    i = raw.index('(') + 1
    n = len(raw)
    while i < n and raw[i] in _CSS_WHITESPACE:
        i += 1
    while i < n:
        ch = raw[i]
        if ch == ')':
            return False
        if ch in _CSS_WHITESPACE:
            while i < n and raw[i] in _CSS_WHITESPACE:
                i += 1
            if i >= n or raw[i] == ')':
                return False
            return None
        if ch in '"\'(' or _is_non_printable(ch):
            return ParseErrorMessage.UNEXPECTED_CHARACTER_IN_URL
        if ch == '\\':
            if i + 1 < n and raw[i + 1] in _NEWLINES:
                return ParseErrorMessage.INVALID_ESCAPE_SEQUENCE_IN_URL
            i = _skip_escape(raw, i + 1)
            continue
        i += 1
    return False


def _ends_with_escape(raw: str) -> bool:
    """Whether raw, at the end of the input, ends with a backslash that starts an escape."""
    n = len(raw) - len(raw.rstrip('\\'))
    return n % 2 == 1


class _KgToken(NamedTuple):
    """A token of kgcss's tokenizer, with its offsets in the whole text."""

    type: KgTokenType
    raw: str
    start: int
    end: int  # exclusive
    value: str | float | None
    unit: str | None
    representation: str | None
    is_integer: bool
    hash_is_id: bool
    closed: bool


# The code points from U+0080 that are not non-ASCII ident code points in the current CSS Syntax draft
RE_NON_IDENT: Final = re.compile(
    '[\x80-\xb6\xb8-\xbf\xd7\xf7\u037e\u2000-\u200b\u200e-\u203e\u2041-\u206f\u2190-\u2bff'
    '\u2ff0-\u3000\ue000-\uf8ff\ufdd0-\ufdef\ufffe\uffff]'
)
_IDENT_LIKE: Final = frozenset({
    KgTokenType.IDENT, KgTokenType.FUNCTION, KgTokenType.AT_KEYWORD, KgTokenType.HASH, KgTokenType.DIMENSION
})  # fmt: skip


def _non_ident_index(raw: str) -> int:
    """The index of the first code point of raw, outside escapes, that is not a non-ASCII ident code point."""
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch == '\\' and i + 1 < n and raw[i + 1] not in _NEWLINES:
            i = _skip_escape(raw, i + 1)
            continue
        if RE_NON_IDENT.match(ch) is not None:
            return i
        i += 1
    return -1


def _kg_tokens(css: str) -> Iterator[_KgToken]:
    """The tokens of kgcss's tokenizer for css, adjusted for code points that are not ident code points.

    kgcss follows the CSS Syntax CR of 2021, in which all code points from
    U+0080 are ident code points. @csstools follows the current draft, in
    which some are not (U+00D7 and U+00F7, punctuation, arrows, private use
    code points...), so an ident, function, at-keyword, hash or dimension
    ends before them, and they are delim tokens. The token is cut there, and
    the text after it tokenized again.
    """
    check = RE_NON_IDENT.search(css) is not None
    pos = 0
    while True:
        resume = -1
        for t in kg_tokenize(css[pos:] if pos else css, comments=True):
            raw = t.source_text
            if check and t.type in _IDENT_LIKE and (cut := _non_ident_index(raw)) != -1:
                start = pos + t.pos
                for p in kg_tokenize(raw[:cut], comments=True):
                    yield _kg_token(p, start)
                ch = raw[cut]
                yield _KgToken(KgTokenType.DELIM, ch, start + cut, start + cut + 1, ch, None, None, False, False, True)
                resume = start + cut + 1
                break
            yield _kg_token(t, pos)
        if resume == -1 or resume >= len(css):
            return
        pos = resume


def _kg_token(t: KgToken, base: int) -> _KgToken:
    return _KgToken(
        t.type,
        t.source_text,
        base + t.pos,
        base + t.end,
        t.value,
        t.unit,
        t.representation,
        t.is_integer,
        t.hash_is_id,
        t.closed,
    )


def _numeric(t: _KgToken, representation: str) -> tuple[float, NumberType, str | None]:
    sign = representation[0] if representation[0] in '+-' else None
    return float(representation), NumberType.INTEGER if t.is_integer else NumberType.NUMBER, sign


def tokenize(css: str, *, on_parse_error: OnParseError | None = None) -> list[CSSToken]:
    """The tokens of css, ending with an EOF token, as @csstools/css-tokenizer's tokenize().

    on_parse_error is called with the errors of the tokenizer in order.
    """
    ans: list[CSSToken] = []
    errors: list[ParseError] = []
    pending_whitespace: CSSToken | None = None
    size = len(css)
    for t in _kg_tokens(css):
        ttype = _TYPES[t.type]
        raw = t.raw
        start, end = t.start, t.end - 1
        data: TokenData | None = None
        if pending_whitespace is not None:
            # the whitespace after url( that kgcss reads with the function token
            if ttype is TokenType.WHITESPACE:
                pending_whitespace.raw += raw
                pending_whitespace.end = end
                continue
            pending_whitespace = None
        if ttype in _VALUE_TYPES:
            value = t.value
            assert isinstance(value, str)
            data = ValueData(value)
        elif ttype is TokenType.HASH:
            value = t.value
            assert isinstance(value, str)
            data = HashData(value, HashType.ID if t.hash_is_id else HashType.UNRESTRICTED)
        elif ttype is TokenType.NUMBER or ttype is TokenType.DIMENSION or ttype is TokenType.PERCENTAGE:
            representation = t.representation
            assert representation is not None
            number, ntype, sign = _numeric(t, representation)
            if ttype is TokenType.PERCENTAGE:
                data = NumericData(number, None, sign)
            else:
                data = NumericData(number, ntype, sign, t.unit if ttype is TokenType.DIMENSION else None)
        if ttype is TokenType.FUNCTION and raw[-1] != '(':
            # url( followed by whitespace and a quote: @csstools leaves the whitespace out of the function token
            stripped = raw.rstrip(_CSS_WHITESPACE)
            token = CSSToken(ttype, stripped, start, start + len(stripped) - 1, data)
            ans.append(token)
            pending_whitespace = CSSToken(TokenType.WHITESPACE, raw[len(stripped) :], token.end + 1, end)
            ans.append(pending_whitespace)
            continue
        token = CSSToken(ttype, raw, start, end, data)
        ans.append(token)
        if on_parse_error is not None or ttype is TokenType.URL:
            _token_errors(token, t.closed, end + 1 >= size, css, errors)
    ans.append(CSSToken(TokenType.EOF, '', -1, -1))
    if on_parse_error is not None:
        for err in errors:
            on_parse_error(err)
    return ans


def _token_errors(token: CSSToken, closed: bool, at_end: bool, css: str, errors: list[ParseError]) -> None:
    """Add the errors @csstools reports for token to errors, and make a url bad as @csstools does."""
    ttype, start, end = token.type, token.start, token.end
    raw = token.raw
    match ttype:
        case TokenType.BAD_STRING:
            crlf = css.startswith('\r\n', end + 1)
            errors.append(
                ParseErrorWithToken(
                    ParseErrorMessage.UNEXPECTED_NEW_LINE_IN_STRING,
                    start,
                    end + (2 if crlf else 1),
                    ['4.3.5. Consume a string token', 'Unexpected newline'],
                    token,
                )
            )
        case TokenType.STRING if not closed:
            errors.append(
                ParseErrorWithToken(
                    ParseErrorMessage.UNEXPECTED_EOF_IN_STRING,
                    start,
                    end,
                    ['4.3.5. Consume a string token', 'Unexpected EOF'],
                    token,
                )
            )
        case TokenType.COMMENT if not closed:
            errors.append(
                ParseErrorWithToken(
                    ParseErrorMessage.UNEXPECTED_EOF_IN_COMMENT,
                    start,
                    end,
                    ['4.3.2. Consume comments', 'Unexpected EOF'],
                    token,
                )
            )
        case TokenType.DELIM if raw == '\\':
            errors.append(
                ParseErrorWithToken(
                    ParseErrorMessage.INVALID_ESCAPE_SEQUENCE_AFTER_BACKSLASH,
                    start,
                    end,
                    [
                        '4.3.1. Consume a token',
                        'U+005C REVERSE SOLIDUS (\\)',
                        'The input stream does not start with a valid escape sequence',
                    ],
                    token,
                )
            )
        case TokenType.URL | TokenType.BAD_URL:
            if at_end and _ends_with_escape(raw):
                errors.append(_escape_error(start, end))
            cause = _url_error(raw)
            if cause is False:
                if ttype is TokenType.URL and not closed:
                    errors.append(
                        ParseErrorWithToken(
                            ParseErrorMessage.UNEXPECTED_EOF_IN_URL,
                            start,
                            end,
                            ['4.3.6. Consume a url token', 'Unexpected EOF'],
                            token,
                        )
                    )
                return
            token.type = TokenType.BAD_URL
            token.data = None
            if cause == ParseErrorMessage.UNEXPECTED_CHARACTER_IN_URL:
                state = [
                    '4.3.6. Consume a url token',
                    'Unexpected U+0022 QUOTATION MARK ("), U+0027 APOSTROPHE (\'), U+0028 LEFT PARENTHESIS (() '
                    'or non-printable code point',
                ]
                errors.append(ParseErrorWithToken(cause, start, end, state, token))
            elif cause is not None:
                state = [
                    '4.3.6. Consume a url token',
                    'U+005C REVERSE SOLIDUS (\\)',
                    'The input stream does not start with a valid escape sequence',
                ]
                errors.append(ParseErrorWithToken(cause, start, end, state, token))
        case TokenType.IDENT | TokenType.AT_KEYWORD | TokenType.HASH | TokenType.DIMENSION:
            if at_end and _ends_with_escape(raw):
                errors.append(_escape_error(start, end))
        case _:
            pass


def _escape_error(start: int, end: int) -> ParseError:
    return ParseError(
        ParseErrorMessage.UNEXPECTED_EOF_IN_ESCAPED_CODE_POINT,
        start,
        end,
        ['4.3.7. Consume an escaped code point', 'Unexpected EOF'],
    )


def stringify(tokens: Iterable[CSSToken | None]) -> str:
    """The text of the tokens: their raw texts joined.

    As in the JavaScript, a missing token (None, see FunctionNode.end_token) raises TypeError.
    """
    parts = []
    for t in tokens:
        if t is None:
            raise TypeError("Cannot read properties of undefined (reading '1')")
        parts.append(t.raw)
    return ''.join(parts)


def clone_tokens(tokens: Iterable[CSSToken]) -> list[CSSToken]:
    """Deep copies of the tokens, as cloneTokens()."""
    return [copy.deepcopy(t) for t in tokens]


def mirror_variant_type(type: TokenType) -> TokenType | None:
    """The type of the token that closes (or opens) a block that the token type opens (or closes)."""
    return _MIRRORS.get(type)


def mirror_variant(token: CSSToken) -> CSSToken | None:
    """A new token that closes (or opens) the block that token opens (or closes)."""
    mirror = _MIRRORS.get(token.type)
    if mirror is None:
        return None
    return CSSToken(mirror, _BRACKETS[mirror], -1, -1)


_MIRRORS: Final = {
    TokenType.OPEN_PAREN: TokenType.CLOSE_PAREN,
    TokenType.CLOSE_PAREN: TokenType.OPEN_PAREN,
    TokenType.OPEN_CURLY: TokenType.CLOSE_CURLY,
    TokenType.CLOSE_CURLY: TokenType.OPEN_CURLY,
    TokenType.OPEN_SQUARE: TokenType.CLOSE_SQUARE,
    TokenType.CLOSE_SQUARE: TokenType.OPEN_SQUARE,
}
_BRACKETS: Final = {
    TokenType.OPEN_PAREN: '(',
    TokenType.CLOSE_PAREN: ')',
    TokenType.OPEN_CURLY: '{',
    TokenType.CLOSE_CURLY: '}',
    TokenType.OPEN_SQUARE: '[',
    TokenType.CLOSE_SQUARE: ']',
}


def js_number_to_string(x: float) -> str:
    """JavaScript's Number.prototype.toString() of x."""
    if math.isnan(x):
        return 'NaN'
    if math.isinf(x):
        return 'Infinity' if x > 0 else '-Infinity'
    if x == 0:
        return '0'
    sign = '-' if x < 0 else ''
    mantissa, _, exponent = repr(abs(float(x))).partition('e')
    whole, _, fraction = mantissa.partition('.')
    digits = whole + fraction
    n = len(whole) + (int(exponent) if exponent else 0)  # the position of the decimal point in digits
    stripped = digits.lstrip('0')
    n -= len(digits) - len(stripped)
    digits = stripped.rstrip('0')
    k = len(digits)
    if k <= n <= 21:
        text = digits + '0' * (n - k)
    elif 0 < n <= 21:
        text = f'{digits[:n]}.{digits[n:]}'
    elif -6 < n <= 0:
        text = '0.' + '0' * -n + digits
    else:
        e = n - 1
        text = digits[0] + (f'.{digits[1:]}' if k > 1 else '') + 'e' + ('+' if e >= 0 else '-') + str(abs(e))
    return sign + text


def _is_ident_start_code_point(c: int) -> bool:
    return 0x61 <= c <= 0x7A or 0x41 <= c <= 0x5A or c == 0x5F or _is_non_ascii_ident_code_point(c)


def _is_ident_code_point(c: int) -> bool:
    return _is_ident_start_code_point(c) or 0x30 <= c <= 0x39 or c == 0x2D


def _is_non_ascii_ident_code_point(c: int) -> bool:
    if c < 0xB7:
        return c == 0
    if c in (0xB7, 0x200C, 0x200D, 0x203F, 0x2040):
        return True
    return (
        0xC0 <= c <= 0xD6
        or 0xD8 <= c <= 0xF6
        or 0xF8 <= c <= 0x37D
        or 0x37F <= c <= 0x1FFF
        or 0x2070 <= c <= 0x218F
        or 0x2C00 <= c <= 0x2FEF
        or 0x3001 <= c <= 0xD7FF
        or 0xF900 <= c <= 0xFDCF
        or 0xFDF0 <= c <= 0xFFFD
        or 0xD800 <= c <= 0xDFFF
        or c >= 0x10000
    )


def _insert_escaped_code_point(cps: list[int], index: int, cp: int) -> int:
    hex_cps = [ord(x) for x in f'{cp:x}']
    cps[index : index + 1] = [0x5C, *hex_cps, 0x20]
    return 1 + len(hex_cps)


def _serialize_ident(cps: list[int]) -> list[int]:
    """serializeIdent() of @csstools/css-tokenizer's mutations, quirks included."""
    first = cps[0] if cps else None
    second = cps[1] if len(cps) > 1 else None
    if first == 0:
        cps[0] = 0xFFFD
        remainder = 1
    elif first == 0x2D and second == 0x2D:
        remainder = 2
    elif first == 0x2D and second:
        remainder = 2
        if not _is_ident_start_code_point(second):
            remainder += _insert_escaped_code_point(cps, 1, second)
    elif first == 0x2D:
        return [0x5C, first]
    elif first is not None and _is_ident_start_code_point(first):
        remainder = 1
    else:
        if first is None:
            raise TypeError("Cannot read properties of undefined (reading 'toString')")
        remainder = 1 + _insert_escaped_code_point(cps, 0, first)
    i = remainder
    while i < len(cps):
        c = cps[i]
        if c == 0:
            cps[i] = 0xFFFD
            i += 2  # sic: the code point after a NUL is not checked
            continue
        if not _is_ident_code_point(c):
            cps[i : i + 1] = [0x5C, c]
            i += 1
        i += 1
    return cps


def mutate_ident(ident: CSSToken, new_value: str) -> None:
    """Set the value of an ident token, and its raw text to the value escaped, as mutateIdent()."""
    ident.raw = ''.join(map(chr, _serialize_ident([ord(c) for c in new_value])))
    ident.value_data.value = new_value


def mutate_unit(dimension: CSSToken, new_unit: str) -> None:
    """Set the unit of a dimension token and rewrite its raw text, as mutateUnit().

    The raw text is the sign (only if it is '+'), the value as JavaScript
    prints numbers and the escaped unit, with a leading 'e' (but not 'E')
    escaped.
    """
    escaped = _serialize_ident([ord(c) for c in new_unit])
    if escaped[0] == 0x65:
        _insert_escaped_code_point(escaped, 0, 0x65)
    data = dimension.numeric_data
    sign = '+' if data.sign_character == '+' else ''
    dimension.raw = sign + js_number_to_string(data.value) + ''.join(map(chr, escaped))
    data.unit = new_unit


def is_token(x: object) -> TypeIs[CSSToken]:
    """Whether x is a token."""
    return isinstance(x, CSSToken)


def _is(x: CSSToken | None, t: TokenType) -> bool:
    return x is not None and x.type is t


def is_token_numeric(x: CSSToken | None) -> bool:
    """Whether x is a number, percentage or dimension token."""
    return x is not None and x.type in (TokenType.DIMENSION, TokenType.NUMBER, TokenType.PERCENTAGE)


def is_token_white_space_or_comment(x: CSSToken | None) -> bool:
    """Whether x is a whitespace or comment token."""
    return x is not None and x.type in (TokenType.WHITESPACE, TokenType.COMMENT)


def is_token_at_keyword(x: CSSToken | None) -> bool:
    return _is(x, TokenType.AT_KEYWORD)


def is_token_bad_string(x: CSSToken | None) -> bool:
    return _is(x, TokenType.BAD_STRING)


def is_token_bad_url(x: CSSToken | None) -> bool:
    return _is(x, TokenType.BAD_URL)


def is_token_cdc(x: CSSToken | None) -> bool:
    return _is(x, TokenType.CDC)


def is_token_cdo(x: CSSToken | None) -> bool:
    return _is(x, TokenType.CDO)


def is_token_colon(x: CSSToken | None) -> bool:
    return _is(x, TokenType.COLON)


def is_token_comma(x: CSSToken | None) -> bool:
    return _is(x, TokenType.COMMA)


def is_token_comment(x: CSSToken | None) -> bool:
    return _is(x, TokenType.COMMENT)


def is_token_delim(x: CSSToken | None) -> bool:
    return _is(x, TokenType.DELIM)


def is_token_dimension(x: CSSToken | None) -> bool:
    return _is(x, TokenType.DIMENSION)


def is_token_eof(x: CSSToken | None) -> bool:
    return _is(x, TokenType.EOF)


def is_token_function(x: CSSToken | None) -> bool:
    return _is(x, TokenType.FUNCTION)


def is_token_hash(x: CSSToken | None) -> bool:
    return _is(x, TokenType.HASH)


def is_token_ident(x: CSSToken | None) -> bool:
    return _is(x, TokenType.IDENT)


def is_token_number(x: CSSToken | None) -> bool:
    return _is(x, TokenType.NUMBER)


def is_token_percentage(x: CSSToken | None) -> bool:
    return _is(x, TokenType.PERCENTAGE)


def is_token_semicolon(x: CSSToken | None) -> bool:
    return _is(x, TokenType.SEMICOLON)


def is_token_string(x: CSSToken | None) -> bool:
    return _is(x, TokenType.STRING)


def is_token_url(x: CSSToken | None) -> bool:
    return _is(x, TokenType.URL)


def is_token_whitespace(x: CSSToken | None) -> bool:
    return _is(x, TokenType.WHITESPACE)


def is_token_open_paren(x: CSSToken | None) -> bool:
    return _is(x, TokenType.OPEN_PAREN)


def is_token_close_paren(x: CSSToken | None) -> bool:
    return _is(x, TokenType.CLOSE_PAREN)


def is_token_open_square(x: CSSToken | None) -> bool:
    return _is(x, TokenType.OPEN_SQUARE)


def is_token_close_square(x: CSSToken | None) -> bool:
    return _is(x, TokenType.CLOSE_SQUARE)


def is_token_open_curly(x: CSSToken | None) -> bool:
    return _is(x, TokenType.OPEN_CURLY)


def is_token_close_curly(x: CSSToken | None) -> bool:
    return _is(x, TokenType.CLOSE_CURLY)


def is_token_unicode_range(x: CSSToken | None) -> bool:
    return _is(x, TokenType.UNICODE_RANGE)


# }}}

# css-parser-algorithms {{{

MAX_NESTING_DEPTH: Final = 512


class NestingDepthError(Exception):
    """Raised, as the JavaScript throws, for functions and blocks nested too deeply."""


class ComponentValueType(StrEnum):
    """The type of a component value node."""

    FUNCTION = 'function'
    SIMPLE_BLOCK = 'simple-block'
    WHITESPACE = 'whitespace'
    COMMENT = 'comment'
    TOKEN = 'token'


class WalkEntry[N, P, S: Mapping[str, object]]:
    """What a walk callback gets: the node, its parent and the state (a copy for each node)."""

    __slots__ = ('_node', '_parent', '_state')

    def __init__(self, node: N, parent: P, state: S | None) -> None:
        self._node = node
        self._parent = parent
        self._state = state

    @property
    def node(self) -> N:
        return self._node

    @property
    def parent(self) -> P:
        return self._parent

    @property
    def state(self) -> S | None:
        """The state of the walk: changes to it are seen by the descendants of the node."""
        return self._state


class ListParent:
    """The parent that walk() gives the nodes of the list it walks: {value: componentValues} in JavaScript."""

    __slots__ = ('value',)

    def __init__(self, value: list[ComponentValue]) -> None:
        self.value = value


type WalkResult = Literal[False] | None


def walker_index_generator[T](initial_list: list[T]) -> Callable[[list[T], T, int], int]:
    """walkerIndexGenerator(): finds the index of the next node to walk in a list that may have been changed."""
    reference = initial_list[:]
    previous_list: list[T] | None = None
    previous_length = -1

    def index_of(items: list[T], item: T) -> int:
        for i, x in enumerate(items):
            if x is item:
                return i
        return -1

    def next_index(items: list[T], child: T, index: int) -> int:
        nonlocal reference, previous_list, previous_length
        if items is previous_list and len(items) == previous_length and index < len(items) and items[index] is child:
            previous_length = len(items)
            n = index + 1
            return n if n < len(items) else -1
        n = -1
        ref_index = index_of(reference, child)
        while ref_index < len(reference):
            # reference.indexOf(child) is -1 when the child is not in it, and reference[-1] is undefined
            n = -1 if ref_index < 0 else index_of(items, reference[ref_index])
            if n == -1 or n < index:
                ref_index += 1
                continue
            break
        if n == -1:
            previous_list = items
            previous_length = len(items)
            return -1
        if n == index and index < len(items) and child is items[index]:
            n += 1
            if n >= len(items):
                previous_list = items
                previous_length = len(items)
                return -1
        reference = items[:]
        previous_list = items
        previous_length = len(items)
        return n

    return next_index


def _copy_state[S: Mapping[str, object]](state: S | None) -> S | None:
    return None if state is None else copy.copy(state)


class _WalkFrame[S: Mapping[str, object]]:
    __slots__ = ('container', 'gen', 'index', 'items', 'pending', 'state')

    def __init__(self, container: ContainerNode | None, items: list[ComponentValue], state: S | None) -> None:
        self.container = container
        self.items = items
        self.gen = walker_index_generator(items)
        self.index = 0
        self.pending: ComponentValue | None = None
        self.state = state

    def current(self) -> list[ComponentValue]:
        return self.items if self.container is None else self.container.value


def _walk[S: Mapping[str, object]](
    container: ContainerNode | None,
    items: list[ComponentValue],
    cb: Callable[[WalkEntry[ComponentValue, ContainerNode | ListParent, S], int | str], object],
    state: S | None,
) -> None:
    """The walk of a list of component values or a container (iterative, as the nesting can be deep)."""
    if not items:
        return
    stack: list[_WalkFrame[S]] = [_WalkFrame(container, items, state)]
    while stack:
        f = stack[-1]
        if f.pending is not None:
            child, f.pending = f.pending, None
            f.index = f.gen(f.current(), child, f.index)
            if f.index == -1:
                stack.pop()
                continue
        current = f.current()
        if f.index >= len(current):
            stack.pop()
            continue
        child = current[f.index]
        clone = _copy_state(f.state)
        parent = f.container if f.container is not None else ListParent(f.items)
        if cb(WalkEntry(child, parent, clone), f.index) is False:
            stack.pop()  # forEach stops, but the walk of the parent goes on
            continue
        f.pending = child
        if isinstance(child, (FunctionNode, SimpleBlockNode)) and child.value:
            current = f.current()
            if (f.index < len(current) and current[f.index] is child) or any(x is child for x in current):
                stack.append(_WalkFrame(child, child.value, clone))


def _for_each[S: Mapping[str, object]](
    items: list[ComponentValue],
    get: Callable[[], list[ComponentValue]],
    parent: Callable[[], ContainerNode | ListParent],
    cb: Callable[[WalkEntry[ComponentValue, ContainerNode | ListParent, S], int | str], object],
    state: S | None,
) -> WalkResult:
    if not items:
        return None
    gen = walker_index_generator(items)
    index = 0
    while index < len(get()):
        child = get()[index]
        if cb(WalkEntry(child, parent(), _copy_state(state)), index) is False:
            return False
        index = gen(get(), child, index)
        if index == -1:
            break
    return None


def _flatten_tokens(node: ComponentValue) -> list[CSSToken | None]:
    """node.tokens(), without recursion."""
    ans: list[CSSToken | None] = []
    stack: list[ComponentValue | CSSToken | None] = [node]
    while stack:
        item = stack.pop()
        match item:
            case FunctionNode():
                end = item.end_token
                if not is_token_eof(end):
                    stack.append(end)
                stack.extend(reversed(item.value))
                stack.append(item.name)
            case SimpleBlockNode():
                end = item.end_token
                if not is_token_eof(end):
                    stack.append(end)
                stack.extend(reversed(item.value))
                stack.append(item.start_token)
            case WhitespaceNode():
                ans.extend(item.value)
            case CommentNode() | TokenNode():
                ans.append(item.value)
            case _:
                ans.append(item)
    return ans


def _to_string(node: ComponentValue) -> str:
    """node.toString(), without recursion: TypeError for a missing end token."""
    parts: list[str] = []
    stack: list[ComponentValue | CSSToken | None] = [node]
    while stack:
        item = stack.pop()
        match item:
            case FunctionNode():
                stack.append(item.end_token)
                stack.extend(reversed(item.value))
                stack.append(item.name)
            case SimpleBlockNode():
                stack.append(item.end_token)
                stack.extend(reversed(item.value))
                stack.append(item.start_token)
            case WhitespaceNode():
                parts.append(stringify(item.value))
            case CommentNode() | TokenNode():
                parts.append(item.value.raw)
            case None:
                raise TypeError("Cannot read properties of undefined (reading '1')")
            case _:
                parts.append(item.raw)
    return ''.join(parts)


class ContainerNodeBaseClass:
    """The base class of functions and simple blocks: nodes with a list of component values."""

    __slots__ = ('value',)

    def __init__(self, value: list[ComponentValue]) -> None:
        self.value = value

    def index_of(self, item: object) -> int:
        """The index of item (by identity) in value, -1 if it is not there."""
        for i, x in enumerate(self.value):
            if x is item:
                return i
        return -1

    def at(self, index: int) -> ComponentValue | None:
        """The child at index, which counts from the end if negative."""
        if index < 0:
            index += len(self.value)
        return self.value[index] if 0 <= index < len(self.value) else None

    def for_each[S: Mapping[str, object]](
        self,
        cb: Callable[[WalkEntry[ComponentValue, ContainerNode | ListParent, S], int | str], object],
        state: S | None = None,
    ) -> WalkResult:
        """Call cb for each child, as forEach(); stops when cb returns False."""
        assert isinstance(self, (FunctionNode, SimpleBlockNode))
        node = self
        return _for_each(self.value, lambda: node.value, lambda: node, cb, state)

    def walk[S: Mapping[str, object]](
        self,
        cb: Callable[[WalkEntry[ComponentValue, ContainerNode | ListParent, S], int | str], object],
        state: S | None = None,
    ) -> None:
        """Call cb for each descendant, depth first. Always returns None, as the JavaScript does."""
        assert isinstance(self, (FunctionNode, SimpleBlockNode))
        _walk(self, self.value, cb, state)

    def tokens(self) -> list[CSSToken | None]:
        """The tokens of the node; None for a missing end token."""
        assert isinstance(self, (FunctionNode, SimpleBlockNode))
        return _flatten_tokens(self)

    def to_string(self) -> str:
        """The text of the node (TypeError if it has no end token, see FunctionNode.end_token)."""
        assert isinstance(self, (FunctionNode, SimpleBlockNode))
        return _to_string(self)

    def __str__(self) -> str:
        return self.to_string()


class FunctionNode(ContainerNodeBaseClass):
    """A function: its name token, value and end token.

    end_token is the close-paren token, the EOF token if the input ended
    before it, or None if it ended inside a nested function or block.
    """

    __slots__ = ('end_token', 'name')
    type: Final = ComponentValueType.FUNCTION

    def __init__(self, name: CSSToken, end_token: CSSToken | None, value: list[ComponentValue]) -> None:
        super().__init__(value)
        self.name = name
        self.end_token = end_token

    def get_name(self) -> str:
        """The (unescaped) name of the function."""
        return self.name.value_data.value

    def normalize(self) -> None:
        """Replace an EOF end token with a close-paren token."""
        if is_token_eof(self.end_token):
            self.end_token = CSSToken(TokenType.CLOSE_PAREN, ')', -1, -1)

    def __repr__(self) -> str:
        return f'FunctionNode({self.name.raw!r}, {len(self.value)} values)'


class SimpleBlockNode(ContainerNodeBaseClass):
    """A () [] or {} block: its start token, value and end token (see FunctionNode.end_token)."""

    __slots__ = ('end_token', 'start_token')
    type: Final = ComponentValueType.SIMPLE_BLOCK

    def __init__(self, start_token: CSSToken, end_token: CSSToken | None, value: list[ComponentValue]) -> None:
        super().__init__(value)
        self.start_token = start_token
        self.end_token = end_token

    def normalize(self) -> None:
        """Replace an EOF end token with the token that closes the block."""
        if is_token_eof(self.end_token):
            mirror = mirror_variant(self.start_token)
            if mirror is not None:
                self.end_token = mirror

    def __repr__(self) -> str:
        return f'SimpleBlockNode({self.start_token.raw!r}, {len(self.value)} values)'


class WhitespaceNode:
    """A run of whitespace tokens."""

    __slots__ = ('value',)
    type: Final = ComponentValueType.WHITESPACE

    def __init__(self, value: list[CSSToken]) -> None:
        self.value = value

    def tokens(self) -> list[CSSToken | None]:
        return list(self.value)

    def to_string(self) -> str:
        return stringify(self.value)

    def __str__(self) -> str:
        return self.to_string()

    def __repr__(self) -> str:
        return f'WhitespaceNode({self.to_string()!r})'


class CommentNode:
    """A comment."""

    __slots__ = ('value',)
    type: Final = ComponentValueType.COMMENT

    def __init__(self, value: CSSToken) -> None:
        self.value = value

    def tokens(self) -> list[CSSToken | None]:
        return [self.value]

    def to_string(self) -> str:
        return self.value.raw

    def __str__(self) -> str:
        return self.to_string()

    def __repr__(self) -> str:
        return f'CommentNode({self.value.raw!r})'


class TokenNode:
    """Any other token."""

    __slots__ = ('value',)
    type: Final = ComponentValueType.TOKEN

    def __init__(self, value: CSSToken) -> None:
        self.value = value

    def tokens(self) -> list[CSSToken | None]:
        return [self.value]

    def to_string(self) -> str:
        return self.value.raw

    def __str__(self) -> str:
        return self.to_string()

    def __repr__(self) -> str:
        return f'TokenNode({self.value!r})'


type ComponentValue = FunctionNode | SimpleBlockNode | WhitespaceNode | CommentNode | TokenNode
type ContainerNode = FunctionNode | SimpleBlockNode
type ComponentWalkEntry[S: Mapping[str, object]] = WalkEntry[ComponentValue, ContainerNode | ListParent, S]
type ComponentWalkCallback[S: Mapping[str, object]] = Callable[[ComponentWalkEntry[S], int | str], object]


def is_function_node(x: object) -> TypeIs[FunctionNode]:
    return isinstance(x, FunctionNode)


def is_simple_block_node(x: object) -> TypeIs[SimpleBlockNode]:
    return isinstance(x, SimpleBlockNode)


def is_whitespace_node(x: object) -> TypeIs[WhitespaceNode]:
    return isinstance(x, WhitespaceNode)


def is_comment_node(x: object) -> TypeIs[CommentNode]:
    return isinstance(x, CommentNode)


def is_white_space_or_comment_node(x: object) -> TypeIs[WhitespaceNode | CommentNode]:
    return isinstance(x, (WhitespaceNode, CommentNode))


def is_token_node(x: object) -> TypeIs[TokenNode]:
    return isinstance(x, TokenNode)


class _Context:
    __slots__ = ('on_parse_error',)

    def __init__(self, on_parse_error: OnParseError | None) -> None:
        self.on_parse_error = on_parse_error

    def error(self, message: str, start: int, end: int, state: list[str]) -> None:
        if self.on_parse_error is not None:
            self.on_parse_error(ParseError(message, start, end, state))


class _Open:
    __slots__ = ('depth', 'index', 'mirror', 'value')

    def __init__(self, index: int, depth: int, mirror: TokenType | None) -> None:
        self.index = index
        self.depth = depth
        self.mirror = mirror  # None for a function
        self.value: list[ComponentValue] = []


def _opens(t: CSSToken) -> bool:
    return t.type in (TokenType.OPEN_PAREN, TokenType.OPEN_CURLY, TokenType.OPEN_SQUARE, TokenType.FUNCTION)


def _open(tokens: list[CSSToken], index: int, depth: int) -> _Open:
    if depth >= MAX_NESTING_DEPTH:
        raise NestingDepthError('Maximum nesting depth of 512 exceeded, reduce the complexity of your stylesheet.')
    t = tokens[index]
    return _Open(index, depth, None if t.type is TokenType.FUNCTION else mirror_variant_type(t.type))


def _consume_comments_and_whitespace(tokens: list[CSSToken], i: int, into: list[ComponentValue]) -> int:
    n = len(tokens)
    start = i
    while i < n:
        t = tokens[i]
        if t.type is TokenType.WHITESPACE:
            j = i
            while j < n and tokens[j].type is TokenType.WHITESPACE:
                j += 1
            into.append(WhitespaceNode(tokens[i:j]))
            i = j
        elif t.type is TokenType.COMMENT:
            into.append(CommentNode(t))
            i += 1
        else:
            break
    return i - start


def _consume_component_value(ctx: _Context, tokens: list[CSSToken], i: int, depth: int) -> tuple[int, ComponentValue]:
    """consumeComponentValue(): (advance, node). tokens ends with an EOF token."""
    t = tokens[i]
    if not _opens(t):
        if t.type is TokenType.WHITESPACE:
            j = i
            while j < len(tokens) and tokens[j].type is TokenType.WHITESPACE:
                j += 1
            return j - i, WhitespaceNode(tokens[i:j])
        if t.type is TokenType.COMMENT:
            return 1, CommentNode(t)
        return 1, TokenNode(t)
    n = len(tokens)
    stack = [_open(tokens, i, depth)]
    r = i + 1
    while True:
        frame = stack[-1]
        a = tokens[r] if r < n else None
        o = frame.index
        node: ComponentValue
        if a is None or a.type is TokenType.EOF:
            if frame.mirror is None:
                ctx.error(
                    'Unexpected EOF while consuming a function.',
                    tokens[o].start,
                    tokens[-1].end,
                    ['5.4.9. Consume a function', 'Unexpected EOF'],
                )
                node = FunctionNode(tokens[o], a, frame.value)
            else:
                ctx.error(
                    'Unexpected EOF while consuming a simple block.',
                    tokens[o].start,
                    tokens[-1].end,
                    ['5.4.8. Consume a simple block', 'Unexpected EOF'],
                )
                node = SimpleBlockNode(tokens[o], a, frame.value)
            advance = n - o
        elif frame.mirror is None and a.type is TokenType.CLOSE_PAREN:
            node = FunctionNode(tokens[o], a, frame.value)
            advance = r - o + 1
        elif frame.mirror is not None and a.type is frame.mirror:
            node = SimpleBlockNode(tokens[o], a, frame.value)
            advance = r - o + 1
        elif a.type is TokenType.WHITESPACE or a.type is TokenType.COMMENT:
            r += _consume_comments_and_whitespace(tokens, r, frame.value)
            continue
        elif _opens(a):
            stack.append(_open(tokens, r, frame.depth + 1))
            r += 1
            continue
        else:
            frame.value.append(TokenNode(a))
            r += 1
            continue
        stack.pop()
        if not stack:
            return advance, node
        stack[-1].value.append(node)
        r = o + advance


def _with_eof(tokens: list[CSSToken]) -> list[CSSToken]:
    ans = list(tokens)
    if ans and not is_token_eof(ans[-1]):
        ans.append(CSSToken(TokenType.EOF, '', ans[-1].start, ans[-1].end))
    return ans


def parse_component_value(
    tokens: list[CSSToken], *, on_parse_error: OnParseError | None = None
) -> ComponentValue | None:
    """The single component value of tokens, or None, as parseComponentValue()."""
    ctx = _Context(on_parse_error)
    copy_ = _with_eof(tokens)
    if not copy_:
        return None
    advance, node = _consume_component_value(ctx, copy_, 0, 0)
    if is_token_eof(copy_[min(advance, len(copy_) - 1)]):
        return node
    ctx.error(
        'Expected EOF after parsing a component value.',
        tokens[0].start,
        tokens[-1].end,
        ['5.3.9. Parse a component value', 'Expected EOF'],
    )
    return None


def parse_list_of_component_values(
    tokens: list[CSSToken], *, on_parse_error: OnParseError | None = None
) -> list[ComponentValue]:
    """The component values of tokens, as parseListOfComponentValues()."""
    ctx = _Context(on_parse_error)
    copy_ = _with_eof(tokens)
    ans: list[ComponentValue] = []
    i = 0
    while i < len(copy_) and copy_[i].type is not TokenType.EOF:
        advance, node = _consume_component_value(ctx, copy_, i, 0)
        ans.append(node)
        i += advance
    return ans


def parse_comma_separated_list_of_component_values(
    tokens: list[CSSToken], *, on_parse_error: OnParseError | None = None
) -> list[list[ComponentValue]]:
    """The lists of component values between the top level commas of tokens."""
    if not tokens:
        return []
    ctx = _Context(on_parse_error)
    copy_ = _with_eof(tokens)
    ans: list[list[ComponentValue]] = []
    current: list[ComponentValue] = []
    i = 0
    while True:
        if i >= len(copy_) or copy_[i].type is TokenType.EOF:
            ans.append(current)
            return ans
        if copy_[i].type is TokenType.COMMA:
            ans.append(current)
            current = []
            i += 1
            continue
        advance, node = _consume_component_value(ctx, copy_, i, 0)
        current.append(node)
        i += advance


def walk[S: Mapping[str, object]](
    component_values: list[ComponentValue], cb: ComponentWalkCallback[S], state: S | None = None
) -> None:
    """Call cb(entry, index) for every node in component_values and their descendants, depth first.

    entry.state is a shallow copy of state, made for each node; its
    descendants get the same copy. When cb returns False, the walk of the
    list that the node is in stops (and the walk of the parent list goes on).
    The lists can be changed during the walk.
    """
    _walk(None, component_values, cb, state)


def for_each[S: Mapping[str, object]](
    component_values: list[ComponentValue], cb: ComponentWalkCallback[S], state: S | None = None
) -> WalkResult:
    """Call cb(entry, index) for each node of component_values; stops, returning False, when cb does."""
    return _for_each(component_values, lambda: component_values, lambda: ListParent(component_values), cb, state)


def stringify_component_value_lists(component_value_lists: list[list[ComponentValue]]) -> str:
    """The text of the lists, joined by commas: css-parser-algorithms' stringify()."""
    return ','.join(''.join(stringify(x.tokens()) for x in values) for values in component_value_lists)


def source_indices(x: ComponentValue | list[ComponentValue] | object) -> tuple[int, int]:
    """(start of the first token, inclusive end of the last token) of a node or list of nodes.

    Anything with a tokens() method works, as in the JavaScript.
    """
    if isinstance(x, list):
        if not x:
            return 0, 0
        return source_indices(x[0])[0], source_indices(x[-1])[1]
    tokens_method = getattr(x, 'tokens', None)
    assert tokens_method is not None
    tokens: list[CSSToken | None] = tokens_method()
    if not tokens:
        return 0, 0
    first, last = tokens[0], tokens[-1]
    if first is None or last is None:
        return 0, 0
    return first.start, last.end


def replace_component_values(
    component_values_list: list[list[ComponentValue]],
    replace_with: Callable[[ComponentValue], list[ComponentValue] | ComponentValue | None],
) -> list[list[ComponentValue]]:
    """Replace each node for which replace_with returns a node or a (non-empty) list of nodes."""
    for values in component_values_list:

        def cb(entry: ComponentWalkEntry[dict[str, object]], index: int | str) -> None:
            if not isinstance(index, int):
                return
            replacement = replace_with(entry.node)
            if replacement is None:
                return
            entry.parent.value[index : index + 1] = replacement if isinstance(replacement, list) else [replacement]

        walk(values, cb)
    return component_values_list


# }}}
