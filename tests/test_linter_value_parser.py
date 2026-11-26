# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The port of postcss-value-parser (kgcss.linter._value_parser) against the original.

Skipped unless the stylelint oracle is installed (see tests/stylelint_oracle.py).
"""

from collections.abc import Iterator

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from kgcss.linter._value_parser import ValueNode, parse, stringify, unit

from .strategies import FRAGMENTS
from .stylelint_oracle import Stylelint, require_oracle, utf16_offsets
from .test_linter_postcss import RE_SURROGATE_PAIR

require_oracle()

VALUE_FRAGMENTS = (
    *FRAGMENTS,
    'calc(', 'calc(1px/2*3)', 'url(', 'url( a b )', 'url("x")', 'url(x', 'f(a, b / c : d)', ' , ', ' / ', ' : ',
    '/**/', '/* x', '"', "'", '"a\\"b"', 'U+0-7F', 'u+a?', '\\', '\\)', ')', '((', 'a(b(c))', '#fff', '!important',
    '1e3px', '+.5em', '-1', '--x', 'var(--y, )',
)  # fmt: skip
values = st.lists(st.one_of(st.sampled_from(VALUE_FRAGMENTS), st.characters(exclude_categories=())), max_size=30).map(
    ''.join
)


@pytest.fixture(scope='module')
def oracle() -> Iterator[Stylelint]:
    with Stylelint() as s:
        yield s


def dump(node: ValueNode) -> dict[str, object]:
    """The node as postcss-value-parser's plain object, with the keys it has for its type."""
    ans: dict[str, object] = {'type': node.type, 'sourceIndex': node.source_index}
    ans['sourceEndIndex'] = node.source_end_index
    ans['value'] = node.value
    if node.type == 'string':
        ans['quote'] = node.quote
    if node.type in ('div', 'function'):
        ans['before'] = node.before
        ans['after'] = node.after
    if node.unclosed:
        ans['unclosed'] = True
    if node.nodes is not None:
        ans['nodes'] = [dump(n) for n in node.nodes]
    return ans


def normalize(node: dict[str, object], offsets: list[int]) -> dict[str, object]:
    """The oracle's node, with only the keys dump() gives and Python offsets."""
    keys = ('type', 'sourceIndex', 'sourceEndIndex', 'value', 'quote', 'before', 'after')
    ans = {k: node[k] for k in keys if k in node}
    for key in ('sourceIndex', 'sourceEndIndex'):
        i = ans[key]
        assert isinstance(i, int)
        # an unclosed string or url() gets a character added, past the end of the text
        ans[key] = offsets[i] if i < len(offsets) else offsets[-1] + i - len(offsets) + 1
    if node.get('unclosed'):
        ans['unclosed'] = True
    nodes = node.get('nodes')
    if isinstance(nodes, list):
        ans['nodes'] = [normalize(n, offsets) for n in nodes]
    return ans


def check(oracle: Stylelint, value: str) -> None:
    offsets = utf16_offsets(value)
    assert [dump(n) for n in parse(value)] == [normalize(n, offsets) for n in oracle.parse_value(value)]


@pytest.mark.parametrize(
    'value',
    [
        '',
        'a(b, c) url( x ) "s',
        'calc(1px+2px / 3 * (4 - 5))',
        'url(',
        'url(a',
        'url( a ',
        'f(a',
        'a /* c',
        '1px , 2px/3px:4',
        'U+0025-00FF, u+4??',
        '\\(a\\) b',
        ')a(',
    ],
)
def test_examples(oracle: Stylelint, value: str) -> None:
    check(oracle, value)


@given(values)
def test_random(oracle: Stylelint, value: str) -> None:
    assume(not RE_SURROGATE_PAIR.search(value))
    check(oracle, value)


@pytest.mark.parametrize('value', ['a(b, c) url( x ) "s"', 'calc(1px + 2px)', ' a , b / c ', 'u+1?'])
def test_stringify(value: str) -> None:
    assert stringify(parse(value)) == value


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('1px', ('1', 'px')),
        ('-.5e+3em', ('-.5e+3', 'em')),
        ('+1', ('+1', '')),
        ('1e', ('1', 'e')),
        ('1.', ('1', '.')),
        ('px', None),
        ('', None),
        ('.', None),
        ('-', None),
        ('-.a', None),
    ],
)
def test_unit(value: str, expected: tuple[str, str] | None) -> None:
    assert unit(value) == expected
