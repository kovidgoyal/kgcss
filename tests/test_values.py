# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

import random

import pytest

from kgcss import ParseError
from kgcss.syntax import Block, ComponentValueList, Function, parse_component_value_list, parse_one_component_value
from kgcss.tokenizer import Token, TokenType, tokenize

from .suite import group_tokens, values_to_json

T = TokenType


def kinds(values: ComponentValueList) -> list[str]:
    ans: list[str] = []
    for v in values:
        match v:
            case Block():
                ans.append(v.opener)
            case Function():
                ans.append(v.name + '(')
            case Token():
                ans.append(v.source_text)
    return ans


def test_types_cannot_be_instantiated() -> None:
    for cls in (Block, Function, ComponentValueList):
        with pytest.raises(TypeError):
            cls()


def test_flat_list() -> None:
    values = parse_component_value_list('a b')
    assert len(values) == 3
    assert [v.source_text for v in values if isinstance(v, Token)] == ['a', ' ', 'b']
    assert values[-1].pos == 2
    with pytest.raises(IndexError):
        values[3]
    with pytest.raises(IndexError):
        values[-4]
    assert values.text == 'a b'
    assert (values.pos, values.end) == (0, 3)
    assert values.stream.source == 'a b'
    empty = parse_component_value_list('')
    assert len(empty) == 0
    assert not empty
    assert empty.text == ''


def test_blocks() -> None:
    values = parse_component_value_list('x{a [b] (c)}y')
    assert kinds(values) == ['x', '{', 'y']
    block = values[1]
    assert isinstance(block, Block)
    assert block.type is T.LEFT_CURLY
    assert block.opener == '{'
    assert block.closed
    assert (block.pos, block.end) == (1, 12)
    assert block.source_text == '{a [b] (c)}'
    assert (block.line, block.column) == (1, 2)
    content = block.content
    assert kinds(content) == ['a', ' ', '[', ' ', '(']
    assert content.text == 'a [b] (c)'
    assert (content.pos, content.end) == (2, 11)
    sq, paren = content[2], content[4]
    assert isinstance(sq, Block)
    assert isinstance(paren, Block)
    assert (sq.type, sq.opener, sq.content.text) == (T.LEFT_SQUARE, '[', 'b')
    assert (paren.type, paren.opener, paren.content.text) == (T.LEFT_PAREN, '(', 'c')
    assert repr(block) == '<Block {} at 1:2>'
    assert block.stream is values.stream


def test_functions() -> None:
    values = parse_component_value_list('rgb(1, calc(2 + 3)) \\75 rl("x")')
    f = values[0]
    assert isinstance(f, Function)
    assert f.type is T.FUNCTION
    assert f.name == 'rgb'
    assert f.closed
    assert f.source_text == 'rgb(1, calc(2 + 3))'
    assert f.arguments.text == '1, calc(2 + 3)'
    assert kinds(f.arguments) == ['1', ',', ' ', 'calc(']
    inner = f.arguments[3]
    assert isinstance(inner, Function)
    assert inner.arguments.text == '2 + 3'
    escaped = values[2]
    assert isinstance(escaped, Function)
    assert escaped.name == 'url'
    assert escaped.arguments.text == '"x"'
    assert repr(f) == "<Function 'rgb' at 1:1>"
    # An unquoted url() is a single URL token, not a function
    assert parse_component_value_list('url(x)')[0].type is T.URL


def test_mismatched_closers() -> None:
    # A closer that does not match the innermost open block is an ordinary token
    values = parse_component_value_list('( ] } ) ]')
    assert kinds(values) == ['(', ' ', ']']
    paren = values[0]
    assert isinstance(paren, Block)
    assert kinds(paren.content) == [' ', ']', ' ', '}', ' ']
    assert values[2].type is T.RIGHT_SQUARE
    f = parse_component_value_list('f(})')[0]
    assert isinstance(f, Function)
    assert f.closed
    assert kinds(f.arguments) == ['}']


def test_unclosed() -> None:
    css = '{ a ( b [ c /* d */'
    values = parse_component_value_list(css)
    assert kinds(values) == ['{']
    outer = values[0]
    assert isinstance(outer, Block)
    assert not outer.closed
    assert outer.end == len(css)
    assert outer.source_text == css
    paren = outer.content[3]
    assert isinstance(paren, Block)
    assert not paren.closed
    assert paren.content.text == ' b [ c /* d */'
    sq = paren.content[3]
    assert isinstance(sq, Block)
    assert not sq.closed
    assert sq.content.text == ' c /* d */'
    assert kinds(sq.content) == [' ', 'c', ' ', '/* d */']
    f = parse_component_value_list('f(x')[0]
    assert isinstance(f, Function)
    assert not f.closed
    assert f.arguments.text == 'x'


def test_comments() -> None:
    css = 'a /* x */ {/* y */b}'
    assert kinds(parse_component_value_list(css)) == ['a', ' ', '/* x */', ' ', '{']
    values = parse_component_value_list(css, comments=False)
    assert kinds(values) == ['a', ' ', ' ', '{']
    block = values[3]
    assert isinstance(block, Block)
    assert kinds(block.content) == ['b']
    # The text of a list is the exact source, comments included
    assert block.content.text == '/* y */b'
    # A comment hiding the closer does not close the block
    unclosed = parse_component_value_list('{ /* } */', comments=False)[0]
    assert isinstance(unclosed, Block)
    assert not unclosed.closed
    assert unclosed.end == 9


def test_parse_one_component_value() -> None:
    v = parse_one_component_value('  /**/ rgb(1) /* x */ \n')
    assert isinstance(v, Function)
    assert v.name == 'rgb'
    v = parse_one_component_value('{a}')
    assert isinstance(v, Block)
    assert v.content.text == 'a'
    v = parse_one_component_value(' 5px ')
    assert isinstance(v, Token)
    assert (v.value, v.unit) == (5, 'px')
    for css, kind, offset in [
        ('', 'empty', 0),
        (' /**/ ', 'empty', 6),
        ('a b', 'extra-input', 2),
        ('{} x', 'extra-input', 3),
    ]:
        err = parse_one_component_value(css)
        assert isinstance(err, ParseError)
        assert (err.kind, err.offset) == (kind, offset)
        assert err.message


def test_deep_nesting() -> None:
    # Grouping is iterative in C, so deeply nested input cannot overflow the C stack
    depth = 200_000
    css = '(' * depth + 'x' + ')' * depth + '['
    values = parse_component_value_list(css)
    assert kinds(values) == ['(', '[']
    item = values[0]
    for _ in range(depth - 1):
        assert isinstance(item, Block)
        assert item.closed
        (item,) = item.content
    assert isinstance(item, Block)
    assert kinds(item.content) == ['x']
    unclosed = parse_component_value_list('{' * depth)
    assert len(unclosed) == 1


def test_matches_python_reference_grouping() -> None:
    # Differential test against the independent Python grouping in suite.py
    rng = random.Random(4321)
    alphabet = [*'ab()[]{} ,;:"\'\\/*-1url', 'f(', 'url(', '/**/', '/*', 'é', '\U0001f600']
    for _ in range(3000):
        css = ''.join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
        assert values_to_json(parse_component_value_list(css)) == group_tokens(tokenize(css)), css


def test_values_have_exact_source_text() -> None:
    rng = random.Random(99)
    alphabet = [*'ab()[]{} "\'\\/*', 'f(', 'url(', '/**/']
    for _ in range(2000):
        css = ''.join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
        values = parse_component_value_list(css)
        assert ''.join(v.source_text for v in values) == css, css
        stack = [values]
        while stack:
            vals = stack.pop()
            assert vals.text == css[vals.pos : vals.end]
            pos = vals.pos
            for v in vals:
                assert v.pos == pos
                assert v.source_text == css[v.pos : v.end]
                pos = v.end
                if isinstance(v, Block):
                    stack.append(v.content)
                elif isinstance(v, Function):
                    stack.append(v.arguments)
            assert pos == vals.end
