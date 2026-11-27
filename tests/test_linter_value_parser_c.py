# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The C version of the value parser (csrc/value_parser.c) against python_parse() in kgcss.linter._value_parser."""

import gc
import weakref

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kgcss._kgcss import value_parse
from kgcss.linter._value_parser import ValueNode, parse, stringify

from .linter_c_checks import check_value
from .strategies import FRAGMENTS

VALUE_FRAGMENTS = (
    *FRAGMENTS,
    'calc(', 'calc(1px/2*3)', 'calc(a * b)', 'url(', 'url( a b )', 'url("x")', 'url(x', 'url(x\\)', 'url( ', 'url()',
    'f(a, b / c : d)', ' , ', ' / ', ' : ', ' /', '/', '*', '/**/', '/* x', '/*/', '"', "'", '"a\\"b"', 'U+0-7F',
    'u+a?', 'u+', '\\', '\\)', ')', '((', 'a(b(c))', '#fff', '!important', '1e3px', '+.5em', '-1', '--x', 'var(--y, )',
    '\t', '\x00', '\x7f', '\u00e9', '\U0001f600', '\ud800',
)  # fmt: skip
values = st.lists(st.one_of(st.sampled_from(VALUE_FRAGMENTS), st.characters(exclude_categories=())), max_size=30).map(
    ''.join
)


def check(value: str) -> None:
    check_value(value)


@pytest.mark.parametrize(
    'value',
    [
        '',
        ' ',
        'a b',
        'a(b, c) url( x ) "s',
        'calc(1px+2px / 3 * (4 - 5))',
        'calc(a /b)',
        'f(a) / b',
        'f(a /b)',
        'url(',
        'url(a',
        'url( a ',
        'url( a b )',
        'url()',
        'url( )',
        'url(a\\)',
        'url(a\\) b)',
        'URL(a)',
        'f(a',
        'f(g(a',
        'a /* c',
        '/*/',
        '/**/',
        '1px , 2px/3px:4',
        'U+0025-00FF, u+4??',
        'u+',
        'u+\\',
        '\\(a\\) b',
        ')a(',
        '"a',
        "'a\\'",
        '"a\\\\"',
        'a\\',
        'a\\ b',
        '\x00\x1f \x20',
        '\u00e9\U0001f600\ud800',
    ],
)
def test_examples(value: str) -> None:
    check(value)


@given(values)
def test_random(value: str) -> None:
    check(value)


@given(st.text(st.sampled_from('a1 ,/:*()"\'\\u+/*calcurl'), max_size=30))
def test_random_punctuation(value: str) -> None:
    check(value)


def test_node() -> None:
    node = ValueNode('word', 3)
    assert (node.type, node.value, node.source_index, node.source_end_index) == ('word', '', 3, 3)
    assert (node.before, node.after, node.quote, node.unclosed, node.nodes) == ('', '', '', False, None)
    node = ValueNode('function', 1, 'f')
    node.nodes = [ValueNode('word', 3, 'x')]
    node.source_end_index = 5
    assert stringify(node) == 'f(x)'
    assert repr(node) == "ValueNode(function, 'f', 1, 5)"
    with pytest.raises(TypeError):
        ValueNode(1, 2)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError):
        value_parse(1)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize('field', ['type', 'value', 'before', 'after', 'quote', 'nodes'])
def test_cycles_are_collected(field: str) -> None:
    # The fields are writable, so the collector must see all of them
    class Holder:
        node: ValueNode

    holder = Holder()
    holder.node = ValueNode('word', 0)
    setattr(holder.node, field, holder)
    ref = weakref.ref(holder)
    del holder
    gc.collect()
    assert ref() is None


def test_deep_nesting() -> None:
    # Parsing is iterative and freeing the nodes uses the trashcan: neither recurses on the C stack
    nodes = parse('f(' * 100_000)
    depth = 0
    while nodes:
        nodes = nodes[0].nodes or []
        depth += 1
    assert depth == 100_000
