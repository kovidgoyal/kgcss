# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Property based tests of the tokenizer, grouping, parsers and decoding."""

from collections.abc import Callable

from hypothesis import given
from hypothesis import strategies as st

from kgcss import (
    AtRule,
    Block,
    ComponentValueList,
    Declaration,
    Function,
    NodeList,
    ParseError,
    QualifiedRule,
    Token,
    TokenType,
    decode_stylesheet,
    lookup_encoding,
    parse_block_contents,
    parse_component_value_list,
    parse_one_declaration,
    parse_one_rule,
    parse_rule_list,
    parse_stylesheet,
    serialize_identifier,
    serialize_string,
    tokenize,
)
from kgcss._encodings import LABELS

from .strategies import any_char, css_text
from .suite import group_tokens, values_to_json

T = TokenType
NAME_TYPES = frozenset((T.IDENT, T.FUNCTION, T.AT_KEYWORD, T.HASH, T.STRING, T.URL))
NUMERIC_TYPES = frozenset((T.NUMBER, T.PERCENTAGE, T.DIMENSION))
LIST_PARSERS: tuple[Callable[..., NodeList], ...] = (parse_stylesheet, parse_rule_list, parse_block_contents)


def preprocessed(text: str) -> str:
    """The value a tokenizer must produce for raw text: NUL and surrogates become U+FFFD."""
    return ''.join('�' if c == '\x00' or 0xD800 <= ord(c) <= 0xDFFF else c for c in text)


def reference_line_col(css: str, offset: int) -> tuple[int, int]:
    line, line_start, i = 1, 0, 0
    while i < offset:
        c = css[i]
        if c in '\n\f' or (c == '\r' and not (i + 1 < len(css) and css[i + 1] == '\n')):
            line, line_start = line + 1, i + 1
        i += 1
    return line, offset - line_start + 1


@given(css_text)
def test_tokens_cover_the_source(css: str) -> None:
    stream = tokenize(css)
    pos = 0
    for tok in stream:
        assert tok.pos == pos
        assert tok.end > tok.pos
        assert tok.source_text == css[tok.pos : tok.end]
        pos = tok.end
        match tok.type:
            case t if t in NAME_TYPES:
                assert isinstance(tok.value, str)
                assert '\x00' not in tok.value
            case t if t in NUMERIC_TYPES:
                assert isinstance(tok.value, int if tok.is_integer else float)
                assert tok.representation is not None
                assert (tok.unit is not None) == (t is T.DIMENSION)
            case T.BAD_STRING | T.BAD_URL:
                assert tok.value is None
            case _:
                assert isinstance(tok.value, str)
    assert pos == len(css)
    without_comments = [(t.type, t.pos, t.end) for t in tokenize(css, comments=False)]
    assert without_comments == [(t.type, t.pos, t.end) for t in stream if t.type is not T.COMMENT]
    for err in stream.errors:
        assert 0 <= err.offset <= len(css)


@given(css_text)
def test_all_string_kinds_agree(css: str) -> None:
    def summary(prefix: str) -> list[tuple[object, ...]]:
        n = len(prefix)
        return [
            (t.type, t.value, t.unit, t.pos - n, t.end - n, t.closed, t.hash_is_id)
            for t in tokenize(prefix + css, comments=False)
        ]

    ucs1 = summary('')
    assert summary('/*Ā*/') == ucs1
    assert summary('/*\U0001f600*/') == ucs1


@given(css_text)
def test_line_and_column(css: str) -> None:
    stream = tokenize(css)
    for tok in stream:
        assert (tok.line, tok.column) == reference_line_col(css, tok.pos)
    assert stream.line_col(len(css)) == reference_line_col(css, len(css))


@given(st.text(any_char, min_size=1))
def test_serialized_identifiers_tokenize_back(text: str) -> None:
    stream = tokenize(serialize_identifier(text))
    assert len(stream) == 1
    assert stream[0].type is T.IDENT
    assert stream[0].value == preprocessed(text)


@given(st.text(any_char))
def test_serialized_strings_tokenize_back(text: str) -> None:
    stream = tokenize(serialize_string(text))
    assert len(stream) == 1
    assert stream[0].type is T.STRING
    assert stream[0].value == preprocessed(text)
    assert stream[0].closed


@given(css_text)
def test_grouping_matches_reference(css: str) -> None:
    assert values_to_json(parse_component_value_list(css)) == group_tokens(tokenize(css))


def check_values(css: str, values: ComponentValueList) -> None:
    """Check a component value tree iteratively, it can be deep."""
    stack = [values]
    while stack:
        vals = stack.pop()
        assert vals.text == css[vals.pos : vals.end]
        pos = vals.pos
        for v in vals:
            assert v.pos >= pos
            assert v.source_text == css[v.pos : v.end]
            pos = v.end
            if isinstance(v, Block):
                stack.append(v.content)
            elif isinstance(v, Function):
                stack.append(v.arguments)
        assert pos <= vals.end


def check_nodes(css: str, nodes: NodeList, depth: int = 0) -> None:
    pos = 0
    for n in nodes:
        assert n.pos >= pos
        assert n.source_text == css[n.pos : n.end]
        pos = n.end
        match n:
            case AtRule() | QualifiedRule():
                check_values(css, n.prelude)
                assert n.pos <= n.prelude.pos <= n.prelude.end <= n.end
                if n.block is not None:
                    assert n.prelude.end <= n.block.pos <= n.block.end <= n.end
                if n.block is not None and depth < 3:
                    check_nodes(css, parse_block_contents(n.block), depth + 1)
                    check_nodes(css, parse_rule_list(n.block), depth + 1)
            case Declaration():
                check_values(css, n.value)
                assert n.pos <= n.value.pos <= n.value.end <= n.end
            case Token():
                assert n.type is T.COMMENT
    offsets = [e.offset for e in nodes.errors]
    assert offsets == sorted(offsets)


@given(css_text)
def test_parsers_never_raise_and_are_consistent(css: str) -> None:
    for parser in LIST_PARSERS:
        check_nodes(css, parser(css))
    assert isinstance(parse_one_rule(css), AtRule | QualifiedRule | ParseError)
    assert isinstance(parse_one_declaration(css), Declaration | ParseError)


def all_declarations(nodes: NodeList) -> list[Declaration]:
    """Declarations at every depth, parsing rule blocks as block contents."""
    ans: list[Declaration] = []
    stack = [nodes]
    while stack:
        for n in stack.pop():
            if isinstance(n, Declaration):
                ans.append(n)
            elif isinstance(n, AtRule | QualifiedRule) and n.block is not None:
                stack.append(parse_block_contents(n.block))
    return ans


@given(css_text)
def test_declarations_reparse_identically(css: str) -> None:
    for node in all_declarations(parse_block_contents(css)):
        again = parse_one_declaration(node.source_text)
        assert isinstance(again, Declaration), node.source_text
        assert (again.name, again.value.text, again.important) == (node.name, node.value.text, node.important)
        assert again.source_text == node.source_text


@given(css_text)
def test_rules_reparse_identically(css: str) -> None:
    for node in parse_stylesheet(css):
        if isinstance(node, AtRule | QualifiedRule):
            again = parse_one_rule(node.source_text)
            assert type(again) is type(node), node.source_text
            assert isinstance(again, AtRule | QualifiedRule)
            assert again.source_text == node.source_text
            assert again.prelude.text == node.prelude.text
            if isinstance(node, AtRule):
                assert isinstance(again, AtRule)
                assert again.name == node.name


ENCODING_NAMES = frozenset(LABELS.values())
labels = st.one_of(st.none(), st.sampled_from(sorted(LABELS)), st.text(max_size=12))


@given(st.binary(max_size=200), labels, labels)
def test_decode_never_fails(data: bytes, protocol: str | None, environment: str | None) -> None:
    text, encoding = decode_stylesheet(data, protocol_encoding=protocol, environment_encoding=environment)
    assert isinstance(text, str)
    assert encoding in ENCODING_NAMES
    sheet = parse_stylesheet(data, protocol_encoding=protocol, environment_encoding=environment)
    assert sheet.encoding == encoding
    assert sheet.stream.source == text
    assert [n.source_text for n in sheet] == [n.source_text for n in parse_stylesheet(text)]


@given(st.sampled_from(sorted(LABELS)), st.binary(max_size=50))
def test_charset_rule_selects_encoding(label: str, body: bytes) -> None:
    encoding = lookup_encoding(label)
    assert encoding is not None
    _, detected = decode_stylesheet(b'@charset "' + label.encode() + b'";' + body)
    assert detected == ('utf-8' if encoding in ('utf-16be', 'utf-16le') else encoding)
