# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

import random

import pytest

from kgcss import ParseError, _kgcss
from kgcss.tokenizer import Token, TokenStream, TokenType, tokenize

T = TokenType


def toks(css: str, *, comments: bool = True) -> list[tuple[TokenType, object]]:
    return [(t.type, t.value) for t in tokenize(css, comments=comments)]


def single(css: str) -> Token:
    stream = tokenize(css)
    assert len(stream) == 1, list(stream)
    return stream[0]


def assert_round_trip(css: str) -> TokenStream:
    stream = tokenize(css)
    pos = 0
    for tok in stream:
        assert tok.pos == pos, (css, tok)
        assert tok.end > tok.pos
        assert tok.source_text == css[tok.pos : tok.end]
        pos = tok.end
    assert pos == len(css)
    return stream


def test_token_type_enum_matches_c() -> None:
    names = _kgcss._TOKEN_TYPE_NAMES
    assert names[0] is None
    assert list(names[1:]) == [t.name for t in TokenType]
    assert [t.value for t in TokenType] == list(range(1, len(names)))


def test_types_cannot_be_instantiated() -> None:
    for cls in (Token, TokenStream):
        with pytest.raises(TypeError):
            cls()


def test_parse_error_construction() -> None:
    # The CSSOM layer reports its own errors as ParseError
    e = ParseError('misplaced-rule', 'msg', 5, line=2, column=3)
    assert (e.kind, e.message, e.offset, e.line, e.column) == ('misplaced-rule', 'msg', 5, 2, 3)
    assert repr(e) == '<ParseError misplaced-rule at 2:3: msg>'
    assert ParseError('k', 'm').offset == 0
    with pytest.raises(TypeError):
        ParseError(1, 'm')  # ty: ignore[invalid-argument-type]


def test_rejects_non_str() -> None:
    with pytest.raises(TypeError):
        tokenize(b'a')  # ty: ignore[invalid-argument-type]


def test_empty() -> None:
    stream = tokenize('')
    assert len(stream) == 0
    assert list(stream) == []
    assert stream.errors == ()
    with pytest.raises(IndexError):
        stream[0]


def test_sequence_protocol() -> None:
    stream = tokenize('a b')
    assert len(stream) == 3
    assert stream[-1].value == 'b'
    assert stream[-3].value == 'a'
    with pytest.raises(IndexError):
        stream[-4]
    assert [t.value for t in stream] == ['a', ' ', 'b']
    assert stream.source == 'a b'
    assert stream[0].stream is stream


@pytest.mark.parametrize(
    'css',
    [
        'a{color:red}',
        '/* c */ @media screen { a > b + c ~ d { x: url( a b ) } }',
        '"unclosed',
        "'bad\nstring' x",
        'url(a"b) url( a ) url(',
        '\\',
        '\\\n',
        'a\\',
        '#\\',
        '@\\',
        '\r\n\f\t ',
        '1e3 1e+3 1e 1e+ 1.5.5 +.5 -.5 .5. 10% 5px 5\\px',
        '<!-- --> <!- -- ->',
        '/* unclosed',
        '/**/*/',
        'a\x00b \x00 "\x00" url(\x00)',
        'u+1-2 U+? \\75 rl(x)',
        '\ud800 \udfff "\ud800" a\ud800b',
        '日本語 #日本 .日本 "日本" url(日本)',
        '\U0001f600 #\U0001f600',
    ],
)
def test_round_trip(css: str) -> None:
    assert_round_trip(css)


def test_comments() -> None:
    assert toks('a/* x */b') == [(T.IDENT, 'a'), (T.COMMENT, ' x '), (T.IDENT, 'b')]
    assert toks('a/* x */b', comments=False) == [(T.IDENT, 'a'), (T.IDENT, 'b')]
    t = single('/* x')
    assert t.type is T.COMMENT
    assert t.value == ' x'
    assert not t.closed
    assert [e.kind for e in t.stream.errors] == ['eof-in-comment']
    assert single('/**/').value == ''
    assert single('/**/').closed


def test_escapes() -> None:
    assert single('#\\5f x').value == '_x'
    assert single('\\31 a').value == '1a'
    assert single('\\31\r\na').value == '1a'
    assert single('a\\0').value == 'a\ufffd'
    assert single('a\\d800').value == 'a\ufffd'
    assert single('a\\110000').value == 'a\ufffd'
    assert single('a\\10FFFF').value == 'a\U0010ffff'
    assert single('a\\1234567').value == 'a\ufffd7'
    assert single('a\\0123456').value == 'a\U000123456'
    assert single('a\\').value == 'a\ufffd'
    assert [e.kind for e in tokenize('a\\').errors] == ['eof-in-escape']
    assert single('a\\.b').value == 'a.b'
    assert single('a\x00b').value == 'a\ufffdb'
    assert single('a\ud800b').value == 'a\ufffdb'
    assert single('"a\\\r\nb"').value == 'ab'
    assert single('"a\\\fb"').value == 'ab'
    s = single('"a\\')
    assert s.value == 'a'
    assert not s.closed
    assert single('"\\"\\\\"').value == '"\\'
    assert single('"\x00\ud800"').value == '\ufffd\ufffd'
    # A backslash followed by a newline is not an escape outside strings
    assert toks('\\\n') == [(T.DELIM, '\\'), (T.WHITESPACE, '\n')]
    assert [e.kind for e in tokenize('\\\n').errors] == ['invalid-escape']


def test_strings() -> None:
    t = single('"abc"')
    assert (t.type, t.value, t.closed) == (T.STRING, 'abc', True)
    assert single("'a\"b'").value == 'a"b'
    stream = tokenize('"a\nb')
    assert [t.type for t in stream] == [T.BAD_STRING, T.WHITESPACE, T.IDENT]
    assert stream[0].value is None
    assert stream[0].source_text == '"a'
    assert [e.kind for e in stream.errors] == ['newline-in-string']


def test_numbers() -> None:
    def num(css: str) -> tuple[TokenType, object, bool, str | None]:
        t = single(css)
        assert t.representation is not None
        return t.type, t.value, t.is_integer, t.unit

    assert num('700') == (T.NUMBER, 700, True, None)
    assert type(single('700').value) is int
    assert num('+5') == (T.NUMBER, 5, True, None)
    assert num('-0') == (T.NUMBER, 0, True, None)
    assert num('010') == (T.NUMBER, 10, True, None)
    assert num('1.5') == (T.NUMBER, 1.5, False, None)
    assert num('.5') == (T.NUMBER, 0.5, False, None)
    assert num('-.5') == (T.NUMBER, -0.5, False, None)
    assert num('1e3') == (T.NUMBER, 1000.0, False, None)
    assert num('1E-2') == (T.NUMBER, 0.01, False, None)
    assert num('1.0E1px') == (T.DIMENSION, 10.0, False, 'px')
    assert num('1em') == (T.DIMENSION, 1, True, 'em')
    assert num('1e') == (T.DIMENSION, 1, True, 'e')
    assert toks('1e+') == [(T.DIMENSION, 1), (T.DELIM, '+')]
    assert num('5\\70x') == (T.DIMENSION, 5, True, 'px')
    assert num('5--x') == (T.DIMENSION, 5, True, '--x')
    assert num('50%') == (T.PERCENTAGE, 50, True, None)
    assert num('1e999') == (T.NUMBER, float('inf'), False, None)
    big = '123456789012345678901234567890'
    assert num(big) == (T.NUMBER, int(big), True, None)
    assert num('-' + big) == (T.NUMBER, -int(big), True, None)
    assert num('999999999999999999') == (T.NUMBER, 999999999999999999, True, None)
    assert single('1.0E1px').representation == '1.0E1'
    assert tokenize('a')[0].representation is None
    assert not tokenize('a')[0].is_integer


def test_idents_and_names() -> None:
    assert toks('-a --b --') == [
        (T.IDENT, '-a'),
        (T.WHITESPACE, ' '),
        (T.IDENT, '--b'),
        (T.WHITESPACE, ' '),
        (T.IDENT, '--'),
    ]
    assert toks('-->') == [(T.CDC, '-->')]
    assert toks('- 1') == [(T.DELIM, '-'), (T.WHITESPACE, ' '), (T.NUMBER, 1)]
    assert single('@media').value == 'media'
    assert single('@-x').value == '-x'
    assert toks('@ x')[0] == (T.DELIM, '@')
    assert toks('@1')[0] == (T.DELIM, '@')
    h = single('#abc')
    assert (h.type, h.value, h.hash_is_id) == (T.HASH, 'abc', True)
    h = single('#1bc')
    assert (h.type, h.value, h.hash_is_id) == (T.HASH, '1bc', False)
    assert not single('a').hash_is_id
    assert toks('# ')[0] == (T.DELIM, '#')
    f = single('rgb(')
    assert (f.type, f.value) == (T.FUNCTION, 'rgb')


def test_urls() -> None:
    u = single('url(a.png)')
    assert (u.type, u.value, u.closed) == (T.URL, 'a.png', True)
    assert single('URL( a\\)b \t)').value == 'a)b'
    assert single('u\\72l(x)').value == 'x'
    assert single('url(\\61 )').value == 'a'
    assert toks('url( "a")') == [(T.FUNCTION, 'url'), (T.WHITESPACE, ' '), (T.STRING, 'a'), (T.RIGHT_PAREN, ')')]
    assert toks("url('a')") == [(T.FUNCTION, 'url'), (T.STRING, 'a'), (T.RIGHT_PAREN, ')')]
    # Function token swallows all but one whitespace before a quote, CRLF is one unit
    stream = tokenize('url(  \r\n"a")')
    assert [t.source_text for t in stream] == ['url(  ', '\r\n', '"a"', ')']
    assert toks('urlx(a)')[0] == (T.FUNCTION, 'urlx')
    assert single('url(a b)').type is T.BAD_URL
    assert single('url(a b)').value is None
    assert single('url(a"b)').type is T.BAD_URL
    assert single('url(a\\)b').type is T.URL
    assert toks('url(a b\\)c) d') == [(T.BAD_URL, None), (T.WHITESPACE, ' '), (T.IDENT, 'd')]
    # A bad url ended by EOF rather than ) is not closed
    assert not single('url(a b').closed
    assert single('url(a b)').closed
    assert not single('url(a"b\\)').closed
    u = single('url( a ')
    assert (u.value, u.closed) == ('a', False)
    assert [e.kind for e in u.stream.errors] == ['eof-in-url']


def test_no_unicode_range_or_match_tokens() -> None:
    # Deliberate differences from the conformance suite, see test_css_parsing_suite.py
    assert toks('u+1') == [(T.IDENT, 'u'), (T.NUMBER, 1)]
    assert toks('U+1?') == [(T.IDENT, 'U'), (T.NUMBER, 1), (T.DELIM, '?')]
    assert toks('u+a') == [(T.IDENT, 'u'), (T.DELIM, '+'), (T.IDENT, 'a')]
    assert toks('u+1-2') == [(T.IDENT, 'u'), (T.NUMBER, 1), (T.NUMBER, -2)]
    assert toks('~=') == [(T.DELIM, '~'), (T.DELIM, '=')]
    assert toks('||') == [(T.DELIM, '|'), (T.DELIM, '|')]


def test_delims_and_punctuation() -> None:
    assert toks('{}[](),:;') == [
        (T.LEFT_CURLY, '{'),
        (T.RIGHT_CURLY, '}'),
        (T.LEFT_SQUARE, '['),
        (T.RIGHT_SQUARE, ']'),
        (T.LEFT_PAREN, '('),
        (T.RIGHT_PAREN, ')'),
        (T.COMMA, ','),
        (T.COLON, ':'),
        (T.SEMICOLON, ';'),
    ]
    assert toks('<!--') == [(T.CDO, '<!--')]
    t = single('>')
    assert t.is_delim('>')
    assert not t.is_delim('<')
    assert not single('a').is_delim('a')
    with pytest.raises(TypeError):
        t.is_delim(1)  # ty: ignore[invalid-argument-type]


def test_positions() -> None:
    css = 'a\nb\r\nc\rd\fe  é\U0001f600 f'
    stream = assert_round_trip(css)
    by_value = {t.value: (t.line, t.column) for t in stream if t.type is T.IDENT}
    assert by_value == {
        'a': (1, 1),
        'b': (2, 1),
        'c': (3, 1),
        'd': (4, 1),
        'e': (5, 1),
        'é\U0001f600': (5, 4),
        'f': (5, 7),
    }
    assert stream.line_col(0) == (1, 1)
    assert stream.line_col(len(css)) == (5, 8)
    # The LF of a CRLF pair is still on the first line
    assert stream.line_col(4) == (2, 3)
    with pytest.raises(IndexError):
        stream.line_col(len(css) + 1)
    with pytest.raises(IndexError):
        stream.line_col(-1)


def test_offsets_are_python_indices() -> None:
    # Selector rewriting slices the source with token offsets
    sel = 'é日\U0001f600 #old-id > [id="old-id"]'
    stream = tokenize(sel)
    out = []
    last = 0
    for t in stream:
        if (t.type is T.HASH and t.value == 'old-id') or (t.type is T.STRING and t.value == 'old-id'):
            out.append(sel[last : t.pos])
            out.append('#new' if t.type is T.HASH else '"new"')
            last = t.end
    out.append(sel[last:])
    assert ''.join(out) == 'é日\U0001f600 #new > [id="new"]'


def test_errors() -> None:
    stream = tokenize('a\n  "x\n/* y')
    errors = stream.errors
    assert [(e.kind, e.line, e.column, e.offset) for e in errors] == [
        ('newline-in-string', 2, 5, 6),
        ('eof-in-comment', 3, 1, 7),
    ]
    assert all(isinstance(e.message, str) and e.message for e in errors)
    assert 'newline-in-string' in repr(errors[0])
    assert set(_kgcss._ERROR_KINDS) >= {e.kind for e in errors}


def test_repr() -> None:
    assert repr(tokenize('a')[0]) == "<Token IDENT 'a' at 1:1>"
    assert repr(tokenize('5px')[0]) == "<Token DIMENSION 5'px' at 1:1>"
    assert repr(tokenize('a b')) == '<TokenStream with 3 tokens and 0 errors>'


def test_large_input() -> None:
    # Large enough that the tokenizer runs with the GIL released
    rule = '.c\\31 x > a[href^="x"] { color: #fff; margin: -1.5em 2px; background: url(a.png) } /* é */\n'
    css = rule * 2000
    stream = assert_round_trip(css)
    small = tokenize(rule)
    assert len(stream) == len(small) * 2000
    assert stream[-2].line == 2000


def test_random_inputs_round_trip() -> None:
    # Cheap fuzzing, beside the hypothesis based tests in test_properties.py
    rng = random.Random(1234)
    alphabet = [
        *'ab-_\\/*"\'()[]{}<>!@#%+.,:;= \t\n\r\f0123456789eEuUrlL\x00\x7f',
        'é',
        '\u0100',
        '\ud800',
        '\U0001f600',
    ]
    for _ in range(2000):
        css = ''.join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        stream = assert_round_trip(css)
        for t in stream:
            assert t.line >= 1
            assert t.column >= 1
            repr(t)
        for e in stream.errors:
            assert 0 <= e.offset <= len(css)
